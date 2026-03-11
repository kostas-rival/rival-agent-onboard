"""Slack-friendly response formatting for onboarding agent."""

from __future__ import annotations

from typing import Dict, List, Optional

from .models import (
    ContactPerson,
    FullProgress,
    OnboardingProfile,
    OnboardingSession,
    PhaseProgress,
    TaskDefinition,
    TaskGroup,
    TaskProgress,
    TaskStatus,
    TeamIntroduction,
)


def render_progress_bar(completed: int, total: int, width: int = 20) -> str:
    """Render a text-based progress bar."""
    if total == 0:
        return "░" * width
    pct = completed / total
    filled = int(pct * width)
    empty = width - filled
    bar = "█" * filled + "░" * empty
    return bar


def render_percentage(completed: int, total: int) -> str:
    """Render a percentage string."""
    if total == 0:
        return "0%"
    return f"{int((completed / total) * 100)}%"


def render_phase_status_emoji(pct: float) -> str:
    """Get a status emoji based on completion percentage."""
    if pct >= 1.0:
        return "✅"
    elif pct >= 0.5:
        return "🟡"
    elif pct > 0:
        return "🔵"
    return "⚪"


def render_task_checklist(
    tasks: List[TaskDefinition],
    progress: Dict[str, TaskProgress],
    show_tips: bool = True,
) -> str:
    """Render a task group as a checklist."""
    lines = []
    for task in tasks:
        tp = progress.get(task.id)
        if tp and tp.status in (TaskStatus.COMPLETED, TaskStatus.VERIFIED):
            check = "✅"
        elif tp and tp.status == TaskStatus.SKIPPED:
            check = "⏭️"
        else:
            check = "□"

        line = f"  {check} {task.title}"
        if task.optional:
            line += " _(optional)_"
        lines.append(line)

        # Show subtasks for incomplete items
        if not tp or tp.status == TaskStatus.NOT_STARTED:
            for subtask in task.subtasks:
                lines.append(f"      → {subtask}")
            for link in task.links:
                lines.append(f"      🔗 <{link.url}|{link.label}>")

    if show_tips:
        for task in tasks:
            tp = progress.get(task.id)
            if (not tp or tp.status == TaskStatus.NOT_STARTED) and task.tips:
                lines.append(f"\n💡 *Tip:* {task.tips}")
                break  # Only show one tip at a time

    return "\n".join(lines)


def render_task_group_intro(group: TaskGroup, progress: Dict[str, TaskProgress]) -> str:
    """Render a task group with its intro and tasks."""
    parts = []
    if group.name:
        parts.append(f"*{group.name}*")
    if group.intro:
        parts.append(f"_{group.intro}_")
    parts.append("")

    checklist = render_task_checklist(group.tasks, progress)
    parts.append(checklist)
    return "\n".join(parts)


def render_progress_dashboard(progress: FullProgress) -> str:
    """Render the full progress dashboard."""
    profile = progress.profile
    name = profile.preferred_name or profile.name

    lines = [
        f"📊 *{name}'s Onboarding Progress — Day {progress.onboarding_day}*",
        "",
    ]

    for phase in progress.phases:
        emoji = render_phase_status_emoji(phase.percentage)
        bar = render_progress_bar(phase.completed_tasks, phase.total_tasks, 14)
        pct = render_percentage(phase.completed_tasks, phase.total_tasks)
        lines.append(
            f"{emoji} {phase.phase_name:<26} {bar}  {pct:>4}  ({phase.completed_tasks}/{phase.total_tasks})"
        )
        if phase.outstanding_tasks:
            for task_name in phase.outstanding_tasks[:3]:
                lines.append(f"     Missing: {task_name}")

    lines.append("")
    overall_pct = render_percentage(progress.total_completed, progress.total_tasks)
    lines.append(f"*Overall:* {progress.total_completed}/{progress.total_tasks} tasks ({overall_pct})")

    if progress.overdue_tasks:
        lines.append("")
        lines.append(f"⚠️ *Overdue ({len(progress.overdue_tasks)}):*")
        for task_name in progress.overdue_tasks[:5]:
            lines.append(f"  🔲 {task_name}")

    if progress.upcoming_sessions:
        lines.append("")
        lines.append("📅 *Upcoming sessions:*")
        for session in progress.upcoming_sessions[:3]:
            time_str = session.scheduled_at.strftime("%a %d %b, %H:%M") if session.scheduled_at else "TBC"
            lines.append(f"  → {session.title} — {session.presenter} ({time_str})")

    return "\n".join(lines)


def render_schedule(sessions: List[OnboardingSession], introductions: List[TeamIntroduction]) -> str:
    """Render the upcoming schedule."""
    lines = ["📅 *Your Schedule*", ""]

    if sessions:
        # Sort by date
        sorted_sessions = sorted(
            sessions,
            key=lambda s: s.scheduled_at or __import__("datetime").datetime.max,
        )
        lines.append("*Onboarding Sessions:*")
        for s in sorted_sessions:
            check = "✅" if s.completed else "📚"
            time_str = s.scheduled_at.strftime("%a %d %b, %H:%M") if s.scheduled_at else "TBC"
            lines.append(f"  {check} {s.title} — {s.presenter} ({time_str})")

    if introductions:
        lines.append("")
        lines.append("*Team 1-1s:*")
        for i in introductions:
            check = "✅" if i.completed else "👋"
            sched = " _(scheduled)_" if i.scheduled else " _(not yet scheduled)_"
            lines.append(f"  {check} {i.name} ({i.region}, {i.duration_minutes} min){sched}")

    return "\n".join(lines)


def render_contacts(contacts: Dict[str, List[ContactPerson]], topic: Optional[str] = None) -> str:
    """Render support contacts, optionally filtered by topic."""
    lines = ["📍 *Operations Support Contacts*", ""]

    for region, people in contacts.items():
        region_label = region.replace("_", " ").title()
        lines.append(f"*{region_label}:*")
        for person in people:
            emoji = person.emoji or "👤"
            lines.append(f"  {emoji} *{person.full_name or person.name}* — {person.role}")
            for area in person.helps_with:
                if topic and topic.lower() not in area.lower():
                    continue
                lines.append(f"    → {area}")
        lines.append("")

    return "\n".join(lines)


def render_admin_list(profiles: List[OnboardingProfile], progress_map: Dict[str, FullProgress]) -> str:
    """Render the admin onboarding list view."""
    if not profiles:
        return "📋 No active onboardings at the moment."

    lines = [f"📋 *Active Onboardings ({len(profiles)}):*", ""]

    for profile in profiles:
        name = profile.name
        role = profile.role
        fp = progress_map.get(profile.slack_user_id)

        if fp:
            day = fp.onboarding_day
            total_c = fp.total_completed
            total_t = fp.total_tasks
            pct = fp.overall_percentage
            bar = render_progress_bar(total_c, total_t, 14)

            # Determine status label
            if pct >= 0.85:
                status = "Ahead 🚀"
            elif pct >= 0.5:
                status = "On track ✅"
            elif day > 7 and pct < 0.3:
                status = "Behind ⚠️"
            else:
                status = "Getting started 🌱"

            lines.append(f"*{name}* — {role}")
            lines.append(f"  Day {day} | {total_c}/{total_t} tasks ({render_percentage(total_c, total_t)}) | {bar} | {status}")
        else:
            lines.append(f"*{name}* — {role}")
            lines.append(f"  Status: {profile.status.value}")

        lines.append("")

    return "\n".join(lines)


def render_analytics(analytics: Dict) -> str:
    """Render aggregate analytics."""
    lines = [
        "📈 *Onboarding Analytics*",
        "",
        f"*Active:* {analytics.get('active_count', 0)} onboardings",
        f"*Completed (last 6 months):* {analytics.get('completed_count', 0)}",
        f"*Avg completion time:* {analytics.get('avg_completion_days', 'N/A')} days",
        "",
    ]

    blockers = analytics.get("common_blockers", [])
    if blockers:
        lines.append("*Most common blockers:*")
        for i, (task, avg_days) in enumerate(blockers[:5], 1):
            lines.append(f"  {i}. {task} — avg {avg_days:.1f} days to complete")
        lines.append("")

    faq = analytics.get("frequent_questions", [])
    if faq:
        lines.append("*Most asked questions:*")
        for i, (q, count) in enumerate(faq[:5], 1):
            lines.append(f"  {i}. \"{q}\" ({count} times)")

    return "\n".join(lines)


def render_daily_report(
    profiles: List[OnboardingProfile],
    progress_map: Dict[str, FullProgress],
) -> str:
    """Render the daily status report for admins."""
    from datetime import date

    today = date.today()
    lines = [
        f"📬 *Daily Onboarding Report — {today.strftime('%A, %d %B %Y')}*",
        "",
    ]

    if not profiles:
        lines.append("No active onboardings.")
        return "\n".join(lines)

    for profile in profiles:
        fp = progress_map.get(profile.slack_user_id)
        name = profile.preferred_name or profile.name

        if fp:
            day = fp.onboarding_day
            bar = render_progress_bar(fp.total_completed, fp.total_tasks, 10)
            pct = render_percentage(fp.total_completed, fp.total_tasks)
            overdue_count = len(fp.overdue_tasks)

            lines.append(f"*{name}* — Day {day} | {pct} {bar}")
            if overdue_count > 0:
                lines.append(f"  ⚠️ {overdue_count} overdue task(s)")
            if fp.upcoming_sessions:
                next_s = fp.upcoming_sessions[0]
                lines.append(f"  📅 Next: {next_s.title} — {next_s.presenter}")
        else:
            lines.append(f"*{name}* — {profile.status.value}")

        lines.append("")

    return "\n".join(lines)
