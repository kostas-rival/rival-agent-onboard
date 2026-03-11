"""Schedule and session handler."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from rival_agent_shared import AgentInvocationRequest, AgentInvocationResponse

from ..models import OnboardingProfile, OnboardingSession
from ..renderer import render_schedule
from ..state import get_sessions, save_session
from ..template import load_template

log = logging.getLogger(__name__)


def handle_schedule(
    request: AgentInvocationRequest,
    profile: OnboardingProfile,
) -> AgentInvocationResponse:
    """Show the full onboarding session schedule."""
    template = load_template(profile.template_version)
    sessions = get_sessions(profile.user_id)

    response = render_schedule(template, sessions, profile)

    return AgentInvocationResponse(
        response_text=response,
        steps=["Schedule rendered"],
        citations=[],
        provider=request.provider,
        model=request.model,
        agent_id="onboarding",
    )


def handle_session_prep(
    request: AgentInvocationRequest,
    profile: OnboardingProfile,
    session_type: Optional[str] = None,
) -> AgentInvocationResponse:
    """Prepare for an upcoming session (1:1, team meeting, etc.)."""
    template = load_template(profile.template_version)
    sessions = get_sessions(profile.user_id)

    # Find the target session
    target = _find_next_session(sessions, session_type)

    if not target:
        response = (
            "You don't have any upcoming sessions scheduled. "
            "Use `my schedule` to see the full timeline."
        )
    else:
        response = _render_session_prep(target, template, profile)

    return AgentInvocationResponse(
        response_text=response,
        steps=["Session prep"],
        citations=[],
        provider=request.provider,
        model=request.model,
        agent_id="onboarding",
    )


def handle_session_complete(
    request: AgentInvocationRequest,
    profile: OnboardingProfile,
    session_id: Optional[str] = None,
) -> AgentInvocationResponse:
    """Mark a session as completed and capture notes."""
    sessions = get_sessions(profile.user_id)
    target = _find_next_session(sessions, session_id)

    if not target:
        return AgentInvocationResponse(
            response_text="No upcoming session found to mark complete.",
            steps=["Session lookup"],
            citations=[],
            provider=request.provider,
            model=request.model,
            agent_id="onboarding",
        )

    from ..state import mark_session_completed

    mark_session_completed(profile.user_id, target.session_id)

    response = (
        f"✅ *{target.title}* marked as completed!\n\n"
        f"Great progress. Let me know if you have any follow-up questions "
        f"from the session, or say `next` to continue your onboarding tasks."
    )

    return AgentInvocationResponse(
        response_text=response,
        steps=["Session marked complete"],
        citations=[],
        provider=request.provider,
        model=request.model,
        agent_id="onboarding",
    )


def create_default_sessions(profile: OnboardingProfile) -> None:
    """Create the default onboarding sessions for a new starter."""
    start = profile.start_date
    sessions_def = [
        {
            "session_id": "day1_standup",
            "title": "First Team Standup",
            "description": "Join the daily standup to meet the team.",
            "scheduled_date": start,
            "session_type": "meeting",
        },
        {
            "session_id": "day2_1on1_manager",
            "title": "1:1 with Line Manager",
            "description": "Initial 1:1 — expectations, goals, and questions.",
            "scheduled_date": start + timedelta(days=1),
            "session_type": "one_on_one",
        },
        {
            "session_id": "week1_deep_dive",
            "title": "Architecture Deep Dive",
            "description": "Technical overview of the platform architecture.",
            "scheduled_date": start + timedelta(days=3),
            "session_type": "knowledge_transfer",
        },
        {
            "session_id": "week1_culture",
            "title": "Culture & Ways of Working",
            "description": "How we work — communication norms, rituals, tools.",
            "scheduled_date": start + timedelta(days=4),
            "session_type": "onboarding",
        },
        {
            "session_id": "week2_review",
            "title": "Week 1 Review",
            "description": "Check-in on progress, blockers, and early impressions.",
            "scheduled_date": start + timedelta(days=7),
            "session_type": "review",
        },
        {
            "session_id": "month1_30day_checkin",
            "title": "30-Day Check-In",
            "description": "Formal review of onboarding experience, role expectations, and next steps.",
            "scheduled_date": start + timedelta(days=30),
            "session_type": "review",
        },
    ]

    for s in sessions_def:
        session = OnboardingSession(
            session_id=s["session_id"],
            user_id=profile.user_id,
            title=s["title"],
            description=s["description"],
            scheduled_date=s["scheduled_date"],
            session_type=s["session_type"],
        )
        save_session(session)


def _find_next_session(
    sessions: list[OnboardingSession], filter_type: Optional[str] = None
) -> Optional[OnboardingSession]:
    """Find the next incomplete session, optionally filtered by type or ID."""
    incomplete = [s for s in sessions if not s.completed]

    if not incomplete:
        return None

    if filter_type:
        matches = [
            s
            for s in incomplete
            if s.session_type == filter_type or s.session_id == filter_type
        ]
        if matches:
            return matches[0]

    # Sort by scheduled date and return earliest
    incomplete.sort(key=lambda s: s.scheduled_date)
    return incomplete[0]


def _render_session_prep(
    session: OnboardingSession,
    template,
    profile: OnboardingProfile,
) -> str:
    """Render session preparation guidance."""
    lines = [
        f"📋 *Prep for: {session.title}*\n",
        f"> {session.description}\n",
    ]

    # Add type-specific advice
    prep_tips = {
        "one_on_one": [
            "• Prepare 2-3 questions about your role and expectations",
            "• Note down any blockers or uncertainties from Day 1",
            "• Think about what success looks like in your first 30 days",
        ],
        "meeting": [
            "• Don't worry about contributing yet — observe the format first",
            "• Note the cadence, tone, and who speaks",
            "• Introduce yourself briefly if prompted",
        ],
        "knowledge_transfer": [
            "• Open the repo/codebase beforehand and have a look around",
            "• Write down terminology or acronyms you don't recognise",
            "• Don't be afraid to ask 'why' — context is gold",
        ],
        "review": [
            "• Reflect on what went well and what was confusing",
            "• Prepare honest feedback on the onboarding process",
            "• Think about your goals for the next period",
        ],
    }

    tips = prep_tips.get(session.session_type, [])
    if tips:
        lines.append("*Things to prepare:*")
        lines.extend(tips)

    lines.append(
        f"\n_Scheduled for: {session.scheduled_date.strftime('%A %d %B')}_"
    )

    return "\n".join(lines)
