"""Admin command handler — briefing, profile management, analytics, reports."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from rival_agent_shared import AgentInvocationRequest, AgentInvocationResponse

from ..briefing import (
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
    get_all_task_progress,
    get_profile,
    list_active_profiles,
    list_all_profiles,
    update_profile,
)
from ..template import load_template

log = logging.getLogger(__name__)

# Hard-coded admin Slack IDs (expandable via config)
ADMIN_IDS: set[str] = set()


def _load_admin_ids() -> set[str]:
    """Load admin IDs from config."""
    global ADMIN_IDS  # noqa: PLW0603
    if not ADMIN_IDS:
        settings = get_settings()
        ADMIN_IDS = set(settings.admin_slack_ids)
    return ADMIN_IDS


def is_admin(user_id: str) -> bool:
    """Check whether a user is an admin."""
    return user_id in _load_admin_ids()


# ── Briefing Flow ──────────────────────────────────────────────────────────


def handle_read_briefing(
    request: AgentInvocationRequest,
    url: Optional[str] = None,
) -> AgentInvocationResponse:
    """Read and parse a briefing Google Doc, then create an onboarding profile."""
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

        # Generate personalised onboarding doc
        doc_result = generate_onboarding_doc(briefing)
        doc_url_out = doc_result["doc_url"]

        # Create the profile in Firestore
        profile = OnboardingProfile(
            user_id=briefing.slack_user_id or f"pending_{briefing.full_name.replace(' ', '_').lower()}",
            full_name=briefing.full_name,
            role=briefing.role,
            start_date=briefing.start_date,
            line_manager=briefing.line_manager,
            office_location=briefing.office_location,
            status=OnboardingStatus.PENDING,
            briefing_doc_url=doc_url,
            generated_doc_url=doc_url_out,
            generated_doc_id=doc_result.get("doc_id", ""),
            template_version="onboarding_v2",
        )
        create_profile(profile)

        response = (
            f"✅ *Briefing processed for {briefing.full_name}*\n\n"
            f"• *Role:* {briefing.role}\n"
            f"• *Start date:* {briefing.start_date.strftime('%d %B %Y')}\n"
            f"• *Line manager:* {briefing.line_manager or 'TBC'}\n"
            f"• *Office:* {briefing.office_location or 'Remote'}\n\n"
            f"📄 Generated onboarding doc: <{doc_url_out}|Open in Drive>\n\n"
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
        response = render_admin_list(profiles)

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
    all_progress = {}

    for p in profiles:
        tasks = get_all_task_progress(p.user_id)
        all_progress[p.user_id] = tasks

    response = render_analytics(profiles, all_progress)

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
    all_progress = {}

    for p in profiles:
        tasks = get_all_task_progress(p.user_id)
        all_progress[p.user_id] = tasks

    response = render_daily_report(profiles, all_progress)

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
