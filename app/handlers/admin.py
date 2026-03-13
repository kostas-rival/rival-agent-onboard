"""Admin command handler — briefing, profile management, analytics, reports."""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Optional

from rival_agent_shared import AgentInvocationRequest, AgentInvocationResponse

from ..briefing import (
    create_blank_briefing_doc,
    generate_onboarding_doc,
    parse_briefing_doc,
    read_briefing_from_url,
)
from ..config import get_settings
from ..models import OnboardingProfile, OnboardingStatus
from ..renderer import (
    render_admin_list,
    render_analytics,
    render_daily_report,
)
from ..state import (
    create_profile,
    delete_pending_briefing,
    get_all_task_progress,
    get_pending_briefing,
    get_profile,
    list_active_profiles,
    list_all_profiles,
    save_pending_briefing,
    update_profile,
)
from ..template import load_template
from .progress import compute_full_progress

log = logging.getLogger(__name__)

# Hard-coded admin Slack IDs (expandable via config)
ADMIN_IDS: set[str] = set()


def _load_admin_ids() -> set[str]:
    """Load admin IDs from config."""
    global ADMIN_IDS  # noqa: PLW0603
    if not ADMIN_IDS:
        settings = get_settings()
        raw = settings.admin_slack_ids.strip()
        ADMIN_IDS = {s.strip() for s in raw.split(",") if s.strip()} if raw else set()
    return ADMIN_IDS


def is_admin(user_id: str) -> bool:
    """Check whether a user is an admin."""
    return user_id in _load_admin_ids()


# ── New Onboarding Flow (self-service doc creation) ───────────────────────


def handle_new_onboard(
    request: AgentInvocationRequest,
) -> AgentInvocationResponse:
    """Create a Google Doc briefing template and ask the admin to fill it in."""
    try:
        # Resolve a display name for the admin
        admin_name = request.user_id  # fallback
        admin_profile = get_profile(request.user_id)
        if admin_profile:
            admin_name = admin_profile.preferred_name or admin_profile.full_name

        doc = create_blank_briefing_doc(admin_name=admin_name)
        save_pending_briefing(request.user_id, doc["doc_id"], doc["doc_url"])

        response = (
            "📄 I've created a briefing document for you:\n\n"
            f"<{doc['doc_url']}|➡️ Open Briefing Doc>\n\n"
            "Fill in the new starter's details — name, role, start date, "
            "team introductions, sessions, etc.\n\n"
            "When you're done, just come back here and say *\"done with briefing\"* "
            "and I'll read the doc and set everything up automatically."
        )
    except Exception:
        log.exception("Failed to create blank briefing doc")
        response = (
            "❌ I couldn't create the briefing document. "
            "Please check the Drive service account permissions.\n"
            f"Service account: `{get_settings().drive_service_account}`"
        )

    return AgentInvocationResponse(
        response_text=response,
        steps=["Blank briefing doc created"],
        citations=[],
        provider=request.provider,
        model=request.model,
        agent_id="onboarding",
    )


def handle_briefing_done(
    request: AgentInvocationRequest,
) -> AgentInvocationResponse:
    """Admin signals they've finished filling in the briefing doc — read and process it."""
    pending = get_pending_briefing(request.user_id)

    if not pending:
        return AgentInvocationResponse(
            response_text=(
                "I don't have a pending briefing doc for you.\n"
                "Say *\"onboard someone\"* to create a new briefing document, "
                "or paste a Google Doc URL directly."
            ),
            steps=["No pending briefing"],
            citations=[],
            provider=request.provider,
            model=request.model,
            agent_id="onboarding",
        )

    doc_url = pending["doc_url"]
    doc_id = pending["doc_id"]

    # Re-use the existing briefing reader on the pending doc
    return handle_read_briefing(request, url=doc_url, cleanup_admin_id=request.user_id)


# ── Briefing Flow ──────────────────────────────────────────────────────────


def handle_read_briefing(
    request: AgentInvocationRequest,
    url: Optional[str] = None,
    cleanup_admin_id: Optional[str] = None,
) -> AgentInvocationResponse:
    """Read and parse a briefing Google Doc, then create an onboarding profile.

    If *cleanup_admin_id* is given, delete the pending-briefing record for that
    admin after the briefing has been successfully processed.
    """
    try:
        doc_url = url or _extract_url(request.text)
        if not doc_url:
            return AgentInvocationResponse(
                response_text=(
                    "Please provide the briefing doc URL.\n"
                    "Example: `read briefing https://docs.google.com/document/d/…/edit`"
                ),
                steps=["Awaiting URL"],
                citations=[],
                provider=request.provider,
                model=request.model,
                agent_id="onboarding",
            )

        briefing = read_briefing_from_url(doc_url)

        if not briefing.full_name:
            hint = (
                "It looks like some key details are missing (name is blank). "
                "Please make sure the doc has at least:\n"
                "• *Name*\n• *Role*\n• *Start date*\n\n"
                f"<{doc_url}|➡️ Open the doc> — update it and say *\"done with briefing\"* again."
            )
            return AgentInvocationResponse(
                response_text=hint,
                steps=["Briefing incomplete"],
                citations=[],
                provider=request.provider,
                model=request.model,
                agent_id="onboarding",
            )

        # Attempt to generate personalised onboarding doc (optional — may fail
        # if the service account lacks Google Workspace storage quota)
        doc_url_out = ""
        doc_id_out = ""
        doc_note = ""
        try:
            doc_result = generate_onboarding_doc(briefing)
            doc_url_out = doc_result["doc_url"]
            doc_id_out = doc_result.get("doc_id", "")
            doc_note = f"\n📄 Generated onboarding doc: <{doc_url_out}|Open in Drive>\n"
        except Exception:
            log.warning("Doc generation failed (storage quota?) — continuing without it", exc_info=True)
            doc_note = (
                "\n⚠️ _Could not auto-generate onboarding doc "
                "(service account storage quota). "
                "You can copy the template manually._\n"
            )

        # Create the profile in Firestore
        start = briefing.start_date or date.today()
        profile = OnboardingProfile(
            user_id=briefing.slack_user_id or f"pending_{briefing.full_name.replace(' ', '_').lower()}",
            full_name=briefing.full_name,
            role=briefing.role or "TBC",
            start_date=start,
            line_manager=briefing.line_manager,
            office_location=briefing.office_location,
            status=OnboardingStatus.PENDING,
            briefing_doc_url=doc_url,
            generated_doc_url=doc_url_out,
            generated_doc_id=doc_id_out,
            template_version="v2",
            created_by=request.user_id,
        )
        create_profile(profile)

        # Clean up the pending briefing record if this came from the new flow
        if cleanup_admin_id:
            try:
                delete_pending_briefing(cleanup_admin_id)
            except Exception:
                log.warning("Could not delete pending briefing for %s", cleanup_admin_id)

        response = (
            f"✅ *Briefing processed for {briefing.full_name}*\n\n"
            f"• *Role:* {briefing.role}\n"
            f"• *Start date:* {briefing.start_date.strftime('%d %B %Y') if briefing.start_date else 'TBC'}\n"
            f"• *Line manager:* {briefing.line_manager or 'TBC'}\n"
            f"• *Office:* {briefing.office_location or 'Remote'}\n"
            f"{doc_note}\n"
            f"The onboarding will activate automatically on the start date, "
            f"or you can run `activate {briefing.full_name}` to start now."
        )
    except Exception:
        log.exception("Failed to read briefing")
        response = (
            "❌ I couldn't read that briefing doc. Please check the URL and "
            "ensure the service account has access.\n"
            "Service account: `ai-knowledge@rival-agents.iam.gserviceaccount.com`"
        )

    return AgentInvocationResponse(
        response_text=response,
        steps=["Briefing parsed", "Profile created", "Doc generated"],
        citations=[],
        provider=request.provider,
        model=request.model,
        agent_id="onboarding",
    )


# ── Profile Management ─────────────────────────────────────────────────────


def handle_activate(
    request: AgentInvocationRequest,
    target_name: Optional[str] = None,
) -> AgentInvocationResponse:
    """Activate an onboarding profile (transition pending → active)."""
    profiles = list_all_profiles()
    target = _resolve_profile(profiles, target_name or request.text)

    if not target:
        return _not_found_response(request, target_name)

    if target.status == OnboardingStatus.ACTIVE:
        return AgentInvocationResponse(
            response_text=f"ℹ️ *{target.full_name}* is already active.",
            steps=["Already active"],
            citations=[],
            provider=request.provider,
            model=request.model,
            agent_id="onboarding",
        )

    target.status = OnboardingStatus.ACTIVE
    target.activated_at = datetime.now(timezone.utc)
    update_profile(target)

    # Create default sessions
    from .schedule import create_default_sessions

    create_default_sessions(target)

    return AgentInvocationResponse(
        response_text=(
            f"✅ *{target.full_name}* is now *active*.\n"
            f"They'll receive a welcome DM the next time they message the bot, "
            f"or you can trigger it with `welcome {target.full_name}`."
        ),
        steps=["Profile activated", "Sessions created"],
        citations=[],
        provider=request.provider,
        model=request.model,
        agent_id="onboarding",
    )


def handle_pause(
    request: AgentInvocationRequest,
    target_name: Optional[str] = None,
) -> AgentInvocationResponse:
    """Pause an active onboarding."""
    profiles = list_all_profiles()
    target = _resolve_profile(profiles, target_name or request.text)

    if not target:
        return _not_found_response(request, target_name)

    target.status = OnboardingStatus.PAUSED
    update_profile(target)

    return AgentInvocationResponse(
        response_text=f"⏸️ Onboarding for *{target.full_name}* has been paused.",
        steps=["Profile paused"],
        citations=[],
        provider=request.provider,
        model=request.model,
        agent_id="onboarding",
    )


def handle_complete_onboarding(
    request: AgentInvocationRequest,
    target_name: Optional[str] = None,
) -> AgentInvocationResponse:
    """Mark an onboarding as fully complete."""
    profiles = list_all_profiles()
    target = _resolve_profile(profiles, target_name or request.text)

    if not target:
        return _not_found_response(request, target_name)

    target.status = OnboardingStatus.COMPLETED
    target.completed_at = datetime.now(timezone.utc)
    update_profile(target)

    return AgentInvocationResponse(
        response_text=(
            f"🎉 Onboarding for *{target.full_name}* is now marked *complete*.\n"
            f"They'll no longer receive daily check-ins."
        ),
        steps=["Profile completed"],
        citations=[],
        provider=request.provider,
        model=request.model,
        agent_id="onboarding",
    )


# ── Lists & Dashboards ─────────────────────────────────────────────────────


def handle_admin_list(
    request: AgentInvocationRequest,
) -> AgentInvocationResponse:
    """List all onboarding profiles with status summary."""
    profiles = list_all_profiles()

    if not profiles:
        response = "No onboarding profiles found."
    else:
        progress_map = {}
        for p in profiles:
            try:
                progress_map[p.user_id] = compute_full_progress(p)
            except Exception:
                log.warning("Failed to compute progress for %s", p.user_id)
        response = render_admin_list(profiles, progress_map=progress_map)

    return AgentInvocationResponse(
        response_text=response,
        steps=["Admin list rendered"],
        citations=[],
        provider=request.provider,
        model=request.model,
        agent_id="onboarding",
    )


def handle_analytics(
    request: AgentInvocationRequest,
) -> AgentInvocationResponse:
    """Show aggregate onboarding analytics."""
    profiles = list_all_profiles()
    progress_map = {}

    for p in profiles:
        try:
            progress_map[p.user_id] = compute_full_progress(p)
        except Exception:
            log.warning("Failed to compute progress for %s", p.user_id)

    response = render_analytics(profiles, progress_map)

    return AgentInvocationResponse(
        response_text=response,
        steps=["Analytics computed"],
        citations=[],
        provider=request.provider,
        model=request.model,
        agent_id="onboarding",
    )


def handle_daily_report(
    request: AgentInvocationRequest,
) -> AgentInvocationResponse:
    """Generate and return the daily admin report."""
    profiles = list_active_profiles()
    progress_map = {}

    for p in profiles:
        try:
            progress_map[p.user_id] = compute_full_progress(p)
        except Exception:
            log.warning("Failed to compute progress for %s", p.user_id)

    response = render_daily_report(profiles, progress_map)

    return AgentInvocationResponse(
        response_text=response,
        steps=["Daily report generated"],
        citations=[],
        provider=request.provider,
        model=request.model,
        agent_id="onboarding",
    )


# ── Helpers ─────────────────────────────────────────────────────────────────


def _extract_url(text: str) -> Optional[str]:
    """Extract a Google Docs URL from text."""
    import re

    match = re.search(r"https://docs\.google\.com/document/d/[^\s>|]+", text)
    return match.group(0) if match else None


def _resolve_profile(
    profiles: list[OnboardingProfile], text: str
) -> Optional[OnboardingProfile]:
    """Try to match a profile by name (fuzzy)."""
    if not text or not profiles:
        return None

    text_lower = text.lower().strip()

    # Exact match first
    for p in profiles:
        if p.full_name.lower() == text_lower:
            return p

    # Partial match
    for p in profiles:
        if text_lower in p.full_name.lower() or p.full_name.lower() in text_lower:
            return p

    # Try individual words
    words = text_lower.split()
    for p in profiles:
        name_lower = p.full_name.lower()
        if any(w in name_lower for w in words if len(w) > 2):
            return p

    return None


def _not_found_response(
    request: AgentInvocationRequest, name: Optional[str]
) -> AgentInvocationResponse:
    return AgentInvocationResponse(
        response_text=(
            f"❌ No onboarding profile found for \"{name or 'that person'}\".\n"
            f"Use `list onboardings` to see all profiles."
        ),
        steps=["Profile not found"],
        citations=[],
        provider=request.provider,
        model=request.model,
        agent_id="onboarding",
    )
