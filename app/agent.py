"""Core OnboardingAgent — intent routing and orchestration."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from rival_agent_shared import AgentInvocationRequest, AgentInvocationResponse

from .config import get_settings
from .handlers.admin import (
    handle_activate,
    handle_admin_list,
    handle_analytics,
    handle_complete_onboarding,
    handle_daily_report,
    handle_pause,
    handle_read_briefing,
    is_admin,
)
from .handlers.freeform import handle_freeform
from .handlers.people import handle_show_contacts, handle_who_is
from .handlers.progress import handle_progress
from .handlers.schedule import handle_schedule, handle_session_complete, handle_session_prep
from .handlers.tasks import handle_mark_complete, handle_next_task, handle_skip_task
from .handlers.welcome import handle_get_started, handle_welcome
from .intent import classify_intent
from .models import InteractionType, OnboardingProfile, OnboardingStatus
from .state import (
    create_profile,
    get_profile,
    log_interaction,
    update_profile,
)

log = logging.getLogger(__name__)


class OnboardingAgent:
    """Main orchestrator for the onboarding agent."""

    def __init__(self) -> None:
        self.settings = get_settings()

    async def run(self, request: AgentInvocationRequest) -> AgentInvocationResponse:
        """Process an incoming request and route to the correct handler."""
        user_id = request.user_id
        text = request.text.strip()

        log.info("Onboarding request from %s: %s", user_id, text[:100])

        # Classify intent
        admin = is_admin(user_id)
        intent = classify_intent(text, is_admin=admin)
        log.info("Classified intent: %s (confidence=%.2f)", intent.intent, intent.confidence)

        # ── Admin commands (no profile required) ──────────────────────────
        if intent.intent in {
            "admin_read_briefing",
            "admin_list",
            "admin_analytics",
            "admin_report",
            "admin_activate",
            "admin_pause",
            "admin_complete",
            "admin_help",
        }:
            return self._handle_admin(request, intent)

        # ── Resolve or create profile ─────────────────────────────────────
        profile = get_profile(user_id)

        if not profile:
            # Unknown user — could be a new starter or someone unregistered
            if intent.intent == "greeting":
                return self._handle_unknown_user(request)
            return AgentInvocationResponse(
                response_text=(
                    "👋 Hi! I don't have an onboarding profile for you yet.\n"
                    "If you're a new starter, your line manager will set things up for you. "
                    "If you think this is an error, please reach out to your line manager."
                ),
                steps=["No profile found"],
                citations=[],
                provider=request.provider,
                model=request.model,
                agent_id="onboarding",
            )

        # ── Check onboarding status ───────────────────────────────────────
        if profile.status == OnboardingStatus.PENDING:
            # Auto-activate if start date has passed
            if profile.start_date <= datetime.now(timezone.utc).date():
                profile.status = OnboardingStatus.ACTIVE
                profile.activated_at = datetime.now(timezone.utc)
                update_profile(profile)
                log.info("Auto-activated profile for %s", profile.full_name)
            else:
                return AgentInvocationResponse(
                    response_text=(
                        f"👋 Hi {profile.full_name}! Your onboarding is set to begin "
                        f"on *{profile.start_date.strftime('%d %B %Y')}*.\n\n"
                        f"I'll be here to help when you start. See you soon! 🎉"
                    ),
                    steps=["Pending — not yet started"],
                    citations=[],
                    provider=request.provider,
                    model=request.model,
                    agent_id="onboarding",
                )

        if profile.status == OnboardingStatus.COMPLETED:
            return AgentInvocationResponse(
                response_text=(
                    f"🎉 Hi {profile.full_name}! Your onboarding has been marked as complete.\n"
                    f"If you still have questions, you can use the regular Rival Intelligence bot "
                    f"— just ask your question in any channel or DM."
                ),
                steps=["Onboarding complete"],
                citations=[],
                provider=request.provider,
                model=request.model,
                agent_id="onboarding",
            )

        if profile.status == OnboardingStatus.PAUSED:
            return AgentInvocationResponse(
                response_text=(
                    f"⏸️ Hi {profile.full_name}, your onboarding is currently paused.\n"
                    f"Please check with your line manager to resume."
                ),
                steps=["Onboarding paused"],
                citations=[],
                provider=request.provider,
                model=request.model,
                agent_id="onboarding",
            )

        # ── Route active intents ──────────────────────────────────────────
        return self._route_intent(request, profile, intent)

    def _route_intent(
        self,
        request: AgentInvocationRequest,
        profile: OnboardingProfile,
        intent,
    ) -> AgentInvocationResponse:
        """Route a classified intent to the appropriate handler."""
        intent_name = intent.intent

        # ── Greetings / welcome ───────────────────────────────────────────
        if intent_name == "greeting":
            return handle_welcome(request, profile)

        if intent_name == "get_started":
            return handle_get_started(request, profile)

        # ── Task management ───────────────────────────────────────────────
        if intent_name == "next_task":
            return handle_next_task(request, profile)

        if intent_name == "mark_complete":
            return handle_mark_complete(request, profile, intent)

        if intent_name == "skip_task":
            return handle_skip_task(request, profile, intent)

        # ── Progress & schedule ───────────────────────────────────────────
        if intent_name in ("progress", "show_progress", "help"):
            return handle_progress(request, profile)

        if intent_name in ("schedule", "show_schedule"):
            return handle_schedule(request, profile)

        if intent_name == "session_prep":
            return handle_session_prep(request, profile)

        if intent_name == "session_complete":
            return handle_session_complete(request, profile)

        # ── People ────────────────────────────────────────────────────────
        if intent_name == "who_is":
            return handle_who_is(request, profile, topic=intent.entity)

        if intent_name == "contacts":
            return handle_show_contacts(request, profile)

        # ── Freeform / fallback ───────────────────────────────────────────
        return handle_freeform(request, profile)

    def _handle_admin(
        self,
        request: AgentInvocationRequest,
        intent,
    ) -> AgentInvocationResponse:
        """Handle admin-only commands."""
        if not is_admin(request.user_id):
            return AgentInvocationResponse(
                response_text="🔒 Admin commands are restricted. Please contact an admin.",
                steps=["Unauthorized"],
                citations=[],
                provider=request.provider,
                model=request.model,
                agent_id="onboarding",
            )

        handlers = {
            "admin_read_briefing": lambda: handle_read_briefing(request),
            "admin_list": lambda: handle_admin_list(request),
            "admin_analytics": lambda: handle_analytics(request),
            "admin_report": lambda: handle_daily_report(request),
            "admin_activate": lambda: handle_activate(request, target_name=intent.entity),
            "admin_pause": lambda: handle_pause(request, target_name=intent.entity),
            "admin_complete": lambda: handle_complete_onboarding(
                request, target_name=intent.entity
            ),
            "admin_help": lambda: _handle_admin_help(request),
        }

        handler = handlers.get(intent.intent)
        if handler:
            return handler()

        return AgentInvocationResponse(
            response_text="Unknown admin command.",
            steps=["Unknown admin command"],
            citations=[],
            provider=request.provider,
            model=request.model,
            agent_id="onboarding",
        )

    def _handle_unknown_user(
        self,
        request: AgentInvocationRequest,
    ) -> AgentInvocationResponse:
        """Handle a greeting from someone without a profile."""
        admin = is_admin(request.user_id)
        if admin:
            return AgentInvocationResponse(
                response_text=(
                    "👋 Hello! I'm the Rival Intelligence onboarding assistant.\n\n"
                    "As an admin, you can onboard new starters. Say *\"how do I onboard someone?\"* "
                    "for the full process, or use any of these commands:\n\n"
                    "• Paste a Google Doc URL — process a briefing\n"
                    "• `list active` — see all onboardings\n"
                    "• `activate <name>` — activate a pending profile\n"
                    "• `analytics` — aggregate stats"
                ),
                steps=["Admin greeted"],
                citations=[],
                provider=request.provider,
                model=request.model,
                agent_id="onboarding",
            )

        return AgentInvocationResponse(
            response_text=(
                "👋 Hello! I'm the Rival Intelligence onboarding assistant.\n\n"
                "I help new starters navigate their first 30 days — from tool setup "
                "to meeting the team and understanding how we work.\n\n"
                "If you're a new starter, your line manager will set up your onboarding "
                "profile. Once that's done, I'll be here to guide you through everything!"
            ),
            steps=["Unknown user greeted"],
            citations=[],
            provider=request.provider,
            model=request.model,
            agent_id="onboarding",
        )

def _handle_admin_help(request: AgentInvocationRequest) -> AgentInvocationResponse:
    """Explain how to use the onboarding system to an admin."""
    return AgentInvocationResponse(
        response_text=(
            "📋 *How to onboard a new starter*\n\n"
            "*Step 1 — Prepare a briefing doc*\n"
            "Create a Google Doc with the new starter's details:\n"
            "• Name, role, department, start date\n"
            "• Line manager\n"
            "• Team introductions (who they should meet)\n"
            "• Scheduled onboarding sessions\n"
            "• Any special tool access or notes\n\n"
            "*Step 2 — Share the briefing*\n"
            "Paste the Google Doc URL here:\n"
            "`[onboarding] https://docs.google.com/document/d/YOUR_DOC_ID/edit`\n\n"
            "I'll read the doc, extract the details, and create an onboarding profile.\n\n"
            "*Step 3 — Activate*\n"
            "Once ready (usually on their start date), run:\n"
            "`[onboarding] activate <their name>`\n\n"
            "The new starter can then message me and I'll guide them through everything.\n\n"
            "*Other commands:*\n"
            "• `list active` — see all onboardings\n"
            "• `pause <name>` — pause an onboarding\n"
            "• `analytics` — aggregate stats\n"
            "• `report` — admin digest\n"
        ),
        steps=["Admin help"],
        citations=[],
        provider=request.provider,
        model=request.model,
        agent_id="onboarding",
    )
