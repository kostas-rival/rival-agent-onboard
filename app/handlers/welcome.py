"""Welcome and get_started handlers."""

from __future__ import annotations

import logging
from typing import Dict

from rival_agent_shared import AgentInvocationRequest, AgentInvocationResponse

from ..models import OnboardingProfile, TaskProgress
from ..renderer import render_task_checklist
from ..state import get_all_task_progress, log_interaction, update_profile
from ..models import InteractionType
from ..template import (
    get_active_phases,
    get_next_incomplete_group,
    get_onboarding_day,
    load_template,
)

log = logging.getLogger(__name__)


def handle_welcome(
    request: AgentInvocationRequest,
    profile: OnboardingProfile,
) -> AgentInvocationResponse:
    """Handle initial greeting from a new starter."""
    template = load_template(profile.template_version)
    day = get_onboarding_day(profile)
    name = profile.preferred_name or profile.full_name.split()[0]

    if day == 0 and not profile.welcome_sent:
        # First contact on Day 1
        response = _first_day_welcome(name, profile, template)
        update_profile(profile.user_id, {"welcome_sent": True})
        log_interaction(profile.user_id, InteractionType.WELCOME, f"Welcome message sent (Day {day})")
    elif day == 0:
        response = _returning_day1(name, profile, template)
    else:
        response = _returning_welcome(name, profile, day, template)

    return AgentInvocationResponse(
        response_text=response,
        steps=["Onboarding welcome"],
        citations=[],
        provider=request.provider,
        model=request.model,
        agent_id="onboarding",
    )


def handle_get_started(
    request: AgentInvocationRequest,
    profile: OnboardingProfile,
) -> AgentInvocationResponse:
    """Handle 'let's go', 'start', 'what's first'."""
    template = load_template(profile.template_version)
    progress = get_all_task_progress(profile.user_id)

    group = get_next_incomplete_group(profile, template, progress)
    if not group:
        response = (
            "🎉 You've completed all available tasks! Amazing work.\n\n"
            "Keep an eye out for your upcoming onboarding sessions and team 1-1s. "
            "You can ask me about your schedule anytime."
        )
    else:
        response = _render_group_walkthrough(group, progress, profile)
        update_profile(profile.user_id, {
            "current_phase": _find_phase_for_group(template, group.id),
            "current_group": group.id,
        })

    return AgentInvocationResponse(
        response_text=response,
        steps=["Task walkthrough started"],
        citations=[],
        provider=request.provider,
        model=request.model,
        agent_id="onboarding",
    )


def _first_day_welcome(name: str, profile: OnboardingProfile, template) -> str:
    """Generate the first-ever welcome message."""
    phases = template.phases
    total_tasks = sum(
        len(g.tasks) for p in phases for g in p.groups if not g.dynamic
    )

    # Count by phase
    phase_summary = []
    for phase in phases:
        non_dynamic_groups = [g for g in phase.groups if not g.dynamic]
        group_names = [g.name for g in non_dynamic_groups if g.name]
        if group_names:
            phase_summary.append(f"  {phase.name}")
            for gn in group_names:
                phase_summary.append(f"    • {gn}")

    manager_line = ""
    if profile.line_manager:
        manager_line = f"\n🤝 Your manager *{profile.line_manager}* will have a 1-1 with you to discuss your role and current projects."

    return (
        f"🎉 *Welcome to Rival, {name}!*\n\n"
        f"I'm your onboarding guide — I'll walk you through everything you need "
        f"to get set up, meet the team, and hit the ground running.\n\n"
        f"Think of me as your always-available teammate who never forgets what's next. "
        f"You can talk to me anytime using `[onboarding]` or just message me directly.\n\n"
        f"📍 *Here's your onboarding journey:*\n\n"
        + "\n".join(phase_summary) + "\n"
        f"{manager_line}\n\n"
        f"Ready to start with your tool setup? Just say *\"let's go\"* or ask me about anything!"
    )


def _returning_day1(name: str, profile: OnboardingProfile, template) -> str:
    """Welcome back on Day 1."""
    return (
        f"👋 Hey {name}! Welcome back.\n\n"
        f"Say *\"let's go\"* to continue where you left off, or *\"progress\"* to see how you're doing.\n\n"
        f"You can also ask me anything — about tools, people, policies, or what's coming up."
    )


def _returning_welcome(name: str, profile: OnboardingProfile, day: int, template) -> str:
    """Welcome back on subsequent days."""
    from ..handlers.admin import is_admin
    admin_hint = ""
    if is_admin(profile.user_id):
        admin_hint = "\n\nAs an admin you can also say *\"how do I onboard someone?\"* for the briefing process."

    return (
        f":wave: Hey {name}! Day {day} at Rival.\n\n"
        f"Say *\"progress\"* to see your dashboard, *\"next\"* to pick up where you left off, "
        f"or *\"schedule\"* to see upcoming sessions.\n\n"
        f"Or just ask me anything!{admin_hint}"
    )


def _render_group_walkthrough(
    group, progress: Dict[str, TaskProgress], profile: OnboardingProfile
) -> str:
    """Render a walkthrough of a task group."""
    parts = []
    if group.name:
        parts.append(f"*{group.name}*\n")
    if group.intro:
        parts.append(f"_{group.intro}_\n")
    parts.append("📋 *To do:*")
    parts.append(render_task_checklist(group.tasks, progress, user_id=profile.user_id))
    parts.append("\nLet me know when you've done these, or say *\"next\"* to move on.")
    return "\n".join(parts)


def _find_phase_for_group(template, group_id: str) -> str:
    """Find which phase a group belongs to."""
    for phase in template.phases:
        for group in phase.groups:
            if group.id == group_id:
                return phase.id
    return "day_1"
