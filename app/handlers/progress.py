"""Progress dashboard handler."""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Dict, List, Optional

from rival_agent_shared import AgentInvocationRequest, AgentInvocationResponse

from ..models import (
    FullProgress,
    OnboardingProfile,
    OnboardingSession,
    PhaseProgress,
    TaskProgress,
    TaskStatus,
)
from ..renderer import render_progress_dashboard
from ..state import get_all_task_progress, get_sessions
from ..template import (
    get_all_tasks,
    get_active_phases,
    get_onboarding_day,
    get_overdue_tasks,
    load_template,
)

log = logging.getLogger(__name__)


def handle_progress(
    request: AgentInvocationRequest,
    profile: OnboardingProfile,
) -> AgentInvocationResponse:
    """Handle progress check — render the dashboard."""
    fp = compute_full_progress(profile)
    response = render_progress_dashboard(fp)

    return AgentInvocationResponse(
        response_text=response,
        steps=["Progress dashboard"],
        citations=[],
        provider=request.provider,
        model=request.model,
        agent_id="onboarding",
    )


def compute_full_progress(profile: OnboardingProfile) -> FullProgress:
    """Compute full progress for a profile."""
    template = load_template(profile.template_version)
    progress = get_all_task_progress(profile.user_id)
    day = get_onboarding_day(profile)
    sessions = get_sessions(profile.user_id)

    phase_progresses = []
    total_tasks = 0
    total_completed = 0
    total_skipped = 0

    for phase in template.phases:
        phase_total = 0
        phase_completed = 0
        phase_skipped = 0
        outstanding = []

        for group in phase.groups:
            if group.dynamic:
                continue
            for task in group.tasks:
                if task.auto_complete:
                    phase_total += 1
                    phase_completed += 1
                    continue

                phase_total += 1
                tp = progress.get(task.id)
                if tp:
                    if tp.status in (TaskStatus.COMPLETED, TaskStatus.VERIFIED):
                        phase_completed += 1
                    elif tp.status == TaskStatus.SKIPPED:
                        phase_skipped += 1
                else:
                    outstanding.append(task.title)

        pct = phase_completed / phase_total if phase_total > 0 else 0.0
        phase_progresses.append(PhaseProgress(
            phase_id=phase.id,
            phase_name=phase.name,
            total_tasks=phase_total,
            completed_tasks=phase_completed,
            skipped_tasks=phase_skipped,
            percentage=pct,
            outstanding_tasks=outstanding,
        ))

        total_tasks += phase_total
        total_completed += phase_completed
        total_skipped += phase_skipped

    # Overdue tasks
    overdue = get_overdue_tasks(profile, template, progress)
    overdue_names = [t.title for t in overdue]

    # Upcoming sessions
    now = datetime.utcnow()
    upcoming = [
        s for s in sessions
        if not s.completed and (not s.scheduled_at or s.scheduled_at > now)
    ]
    upcoming.sort(key=lambda s: s.scheduled_at or datetime.max)

    overall_pct = total_completed / total_tasks if total_tasks > 0 else 0.0

    return FullProgress(
        profile=profile,
        phases=phase_progresses,
        total_tasks=total_tasks,
        total_completed=total_completed,
        total_skipped=total_skipped,
        overall_percentage=overall_pct,
        onboarding_day=day,
        days_remaining=max(0, 30 - day),
        overdue_tasks=overdue_names,
        upcoming_sessions=upcoming[:5],
    )
