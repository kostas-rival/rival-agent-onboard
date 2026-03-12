"""Task walkthrough, completion, and skip handlers."""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from rival_agent_shared import AgentInvocationRequest, AgentInvocationResponse

from ..intent import OnboardingIntent
from ..models import (
    InteractionType,
    OnboardingProfile,
    TaskDefinition,
    TaskProgress,
    TaskStatus,
)
from ..renderer import render_task_checklist
from ..state import (
    get_all_task_progress,
    log_interaction,
    mark_task_completed,
    mark_task_skipped,
    update_profile,
)
from ..template import (
    get_all_tasks,
    get_next_incomplete_group,
    get_onboarding_day,
    get_task_by_id,
    load_template,
)

log = logging.getLogger(__name__)


def handle_next_task(
    request: AgentInvocationRequest,
    profile: OnboardingProfile,
) -> AgentInvocationResponse:
    """Handle 'next', 'continue', 'what's next'."""
    template = load_template(profile.template_version)
    progress = get_all_task_progress(profile.user_id)

    group = get_next_incomplete_group(profile, template, progress)
    if not group:
        # All static tasks done
        response = (
            "🎉 You've completed all your current tasks! Excellent work.\n\n"
            "Don't forget about your onboarding sessions and team 1-1s — "
            "say *\"schedule\"* to see what's coming up.\n\n"
            "You can also ask me anything about Rival, our tools, or our processes."
        )
    else:
        # Count completed in this group
        completed_in_group = sum(
            1 for t in group.tasks
            if progress.get(t.id) and progress[t.id].status in (TaskStatus.COMPLETED, TaskStatus.VERIFIED)
        )
        total_in_group = len(group.tasks)

        if completed_in_group > 0 and completed_in_group < total_in_group:
            # Partially complete — show remaining
            incomplete_tasks = [
                t for t in group.tasks
                if not progress.get(t.id) or progress[t.id].status == TaskStatus.NOT_STARTED
            ]
            parts = [
                f"*{group.name}* ({completed_in_group}/{total_in_group} done)\n",
                "📋 *Remaining:*",
                render_task_checklist(incomplete_tasks, progress),
                "\nLet me know when you've done these, or say *\"next\"* to skip ahead.",
            ]
            response = "\n".join(parts)
        else:
            # New group
            parts = []
            if group.name:
                parts.append(f"*{group.name}*\n")
            if group.intro:
                parts.append(f"_{group.intro}_\n")
            parts.append("📋 *To do:*")
            parts.append(render_task_checklist(group.tasks, progress))
            parts.append("\nLet me know when you've done these, or say *\"next\"* to move on.")
            response = "\n".join(parts)

        update_profile(profile.user_id, {"current_group": group.id})

    return AgentInvocationResponse(
        response_text=response,
        steps=["Next task group"],
        citations=[],
        provider=request.provider,
        model=request.model,
        agent_id="onboarding",
    )


def handle_mark_complete(
    request: AgentInvocationRequest,
    profile: OnboardingProfile,
    intent: OnboardingIntent,
) -> AgentInvocationResponse:
    """Handle task completion."""
    template = load_template(profile.template_version)
    progress = get_all_task_progress(profile.user_id)

    # Determine which task(s) to mark complete
    tasks_to_complete = _resolve_tasks(intent, template, profile)

    if not tasks_to_complete:
        # Can't determine which task — ask
        response = (
            "I'm not sure which task you've completed. Could you be more specific?\n\n"
            "You can say things like:\n"
            "• _\"done with Slack\"_\n"
            "• _\"finished Google Drive setup\"_\n"
            "• _\"completed 1Password\"_\n\n"
            "Or say *\"progress\"* to see all your tasks."
        )
        return AgentInvocationResponse(
            response_text=response,
            steps=["Task resolution failed"],
            citations=[],
            provider=request.provider,
            model=request.model,
            agent_id="onboarding",
        )

    completed_names = []
    for task in tasks_to_complete:
        if task.auto_complete:
            continue
        existing = progress.get(task.id)
        if existing and existing.status in (TaskStatus.COMPLETED, TaskStatus.VERIFIED):
            continue
        mark_task_completed(profile.user_id, task.id)
        log_interaction(
            profile.user_id,
            InteractionType.TASK_COMPLETED,
            f"Completed: {task.title}",
            {"task_id": task.id},
        )
        completed_names.append(task.title)

    if not completed_names:
        response = "Those tasks are already marked as complete! 👍\n\nSay *\"next\"* for what's next."
    else:
        # Refresh progress
        progress = get_all_task_progress(profile.user_id)
        all_tasks = get_all_tasks(template)
        total = len([t for t in all_tasks if not t.auto_complete])
        done = len([t for t in all_tasks if progress.get(t.id) and progress[t.id].status in (TaskStatus.COMPLETED, TaskStatus.VERIFIED)])

        completed_list = "\n".join(f"  ✅ {name}" for name in completed_names)

        # Check for next group
        next_group = get_next_incomplete_group(profile, template, progress)
        next_hint = ""
        if intent.secondary_intent == "next_task" and next_group:
            next_hint = f"\n\nUp next: *{next_group.name}*. Say *\"next\"* when you're ready."
        elif next_group:
            next_hint = f"\n\nSay *\"next\"* to continue with *{next_group.name}*."

        response = (
            f"{completed_list}\n\n"
            f"📊 Progress: {done}/{total} tasks complete.{next_hint}"
        )

    return AgentInvocationResponse(
        response_text=response,
        steps=[f"Completed {len(completed_names)} task(s)"],
        citations=[],
        provider=request.provider,
        model=request.model,
        agent_id="onboarding",
    )


def handle_skip_task(
    request: AgentInvocationRequest,
    profile: OnboardingProfile,
    intent: OnboardingIntent,
) -> AgentInvocationResponse:
    """Handle task skip."""
    template = load_template(profile.template_version)
    tasks_to_skip = _resolve_tasks(intent, template, profile)

    if not tasks_to_skip:
        response = "Which task would you like to skip? You can always come back to it later."
    else:
        skipped_names = []
        for task in tasks_to_skip:
            mark_task_skipped(profile.user_id, task.id)
            log_interaction(
                profile.user_id,
                InteractionType.TASK_SKIPPED,
                f"Skipped: {task.title}",
                {"task_id": task.id},
            )
            skipped_names.append(task.title)

        response = (
            "⏭️ Skipped:\n"
            + "\n".join(f"  → {name}" for name in skipped_names)
            + "\n\nYou can always come back to these later. Say *\"next\"* to continue."
        )

    return AgentInvocationResponse(
        response_text=response,
        steps=["Task skipped"],
        citations=[],
        provider=request.provider,
        model=request.model,
        agent_id="onboarding",
    )


def _resolve_tasks(
    intent: OnboardingIntent,
    template,
    profile: OnboardingProfile,
) -> List[TaskDefinition]:
    """Resolve which tasks the user is referring to."""
    tasks = []

    # Direct task IDs from intent
    for tid in intent.task_ids:
        task = get_task_by_id(template, tid)
        if task:
            tasks.append(task)

    # Keywords
    if not tasks and intent.task_keywords:
        from ..intent import _TASK_KEYWORD_MAP
        for kw in intent.task_keywords:
            tid = _TASK_KEYWORD_MAP.get(kw)
            if tid:
                task = get_task_by_id(template, tid)
                if task and task not in tasks:
                    tasks.append(task)

    # If still empty, try to match from current group
    if not tasks:
        progress = get_all_task_progress(profile.user_id)
        group = get_next_incomplete_group(profile, template, progress)
        if group:
            # If there's only one incomplete task in the current group, use it
            incomplete = [
                t for t in group.tasks
                if not progress.get(t.id) or progress[t.id].status == TaskStatus.NOT_STARTED
            ]
            if len(incomplete) == 1:
                tasks = incomplete

    return tasks
