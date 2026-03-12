"""Analytics — aggregate stats across all onboardings."""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from .models import OnboardingProfile, OnboardingStatus, TaskProgress
from .state import get_all_task_progress, list_all_profiles
from .template import load_template

log = logging.getLogger(__name__)


def compute_aggregate_analytics() -> dict[str, Any]:
    """Compute aggregate onboarding analytics across all profiles."""
    profiles = list_all_profiles()

    if not profiles:
        return {"total": 0}

    status_counts = Counter(p.status.value for p in profiles)
    active_profiles = [p for p in profiles if p.status == OnboardingStatus.ACTIVE]

    # Per-profile progress
    per_profile: list[dict] = []
    total_tasks_all = 0
    completed_tasks_all = 0

    for profile in active_profiles:
        tasks = get_all_task_progress(profile.user_id)
        template = load_template(profile.template_version)

        total = sum(
            len(g.tasks) for phase in template.phases for g in phase.groups
        )
        completed = len([t for t in tasks.values() if t.completed])
        skipped = len([t for t in tasks.values() if t.skipped])
        pct = int((completed / total) * 100) if total > 0 else 0

        total_tasks_all += total
        completed_tasks_all += completed

        # Day number
        now = datetime.now(timezone.utc)
        start_dt = datetime.combine(
            profile.start_date, datetime.min.time()
        ).replace(tzinfo=timezone.utc)
        day_number = max(1, (now - start_dt).days + 1)

        per_profile.append(
            {
                "user_id": profile.user_id,
                "name": profile.full_name,
                "role": profile.role,
                "day_number": day_number,
                "total_tasks": total,
                "completed_tasks": completed,
                "skipped_tasks": skipped,
                "completion_pct": pct,
            }
        )

    avg_completion = (
        int((completed_tasks_all / total_tasks_all) * 100)
        if total_tasks_all > 0
        else 0
    )

    # Find common blockers (tasks incomplete across multiple people)
    incomplete_counter: Counter = Counter()
    for profile in active_profiles:
        tasks = get_all_task_progress(profile.user_id)
        completed_ids = {t.task_id for t in tasks.values() if t.completed}
        template = load_template(profile.template_version)
        for phase in template.phases:
            for group in phase.groups:
                for task in group.tasks:
                    if task.id not in completed_ids:
                        incomplete_counter[task.title] += 1

    common_blockers = incomplete_counter.most_common(5)

    return {
        "total_profiles": len(profiles),
        "status_breakdown": dict(status_counts),
        "active_count": len(active_profiles),
        "average_completion_pct": avg_completion,
        "per_profile": per_profile,
        "common_incomplete_tasks": [
            {"task": task, "count": count} for task, count in common_blockers
        ],
    }


def compute_completion_timeline(profile: OnboardingProfile) -> list[dict]:
    """Compute a timeline of task completions for a profile."""
    tasks = get_all_task_progress(profile.user_id)

    # Sort by completion time
    completed_tasks = sorted(
        [t for t in tasks.values() if t.completed and t.completed_at],
        key=lambda t: t.completed_at,
    )

    timeline = []
    for task in completed_tasks:
        timeline.append(
            {
                "task_id": task.task_id,
                "completed_at": task.completed_at.isoformat() if task.completed_at else None,
                "auto_verified": task.auto_verified,
            }
        )

    return timeline
