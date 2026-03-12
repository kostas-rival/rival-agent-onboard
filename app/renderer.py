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
    name = profile.preferred_name or profile.full_name

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
            if session.scheduled_at:
                time_str = session.scheduled_at.strftime("%a %d %b, %H:%M")
            elif session.scheduled_date:
                time_str = session.scheduled_date.strftime("%a %d %b")
            else:
                time_str = "TBC"
            presenter = f" — {session.presenter}" if session.presenter else ""
            lines.append(f"  → {session.title}{presenter} ({time_str})")

    return "\n".join(lines)


def render_schedule(template=None, sessions: List[OnboardingSession] = None, profile: OnboardingProfile = None) -> str:
    """Render the upcoming schedule."""
    lines = ["📅 *Your Schedule*", ""]
    sessions = sessions or []

    if sessions:
        # Sort by scheduled_date or scheduled_at
        def _sort_key(s):
            if s.scheduled_date:
                return __import__("datetime").datetime.combine(s.scheduled_date, __import__("datetime").time.min)
            if s.scheduled_at:
                return s.scheduled_at
            return __import__("datetime").datetime.max

        sorted_sessions = sorted(sessions, key=_sort_key)
        lines.append("*Onboarding Sessions:*")
        for s in sorted_sessions:
            check = "✅" if s.completed else "📚"
            if s.scheduled_date:
                time_str = s.scheduled_date.strftime("%a %d %b")
            elif s.scheduled_at:
                time_str = s.scheduled_at.strftime("%a %d %b, %H:%M")
            else:
                time_str = "TBC"
            presenter = f" — {s.presenter}" if s.presenter else ""
            lines.append(f"  {check} {s.title}{presenter} ({time_str})")
    else:
        lines.append("No sessions scheduled yet.")

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


def render_admin_list(profiles: List[OnboardingProfile], progress_map: Optional[Dict] = None) -> str:
    """Render the admin onboarding list view."""
    if not profiles:
        return "📋 No active onboardings at the moment."

    lines = [f"📋 *Active Onboardings ({len(profiles)}):*", ""]

    for profile in profiles:
        name = profile.full_name
        role = profile.role
        fp = progress_map.get(profile.user_id)

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


def render_analytics(profiles: List[OnboardingProfile] = None, progress_map: Optional[Dict] = None, analytics: Optional[Dict] = None) -> str:
    """Render aggregate analytics."""
    if analytics is None:
        analytics = {}
    
    # Build analytics from profiles if passed directly
    if profiles and progress_map:
        active_count = len([p for p in profiles if p.status.value == "active"])
        analytics = {
            "active_count": active_count,
            "completed_count": len([p for p in profiles if p.status.value == "completed"]),
            "total_profiles": len(profiles),
        }

    lines = [
        "📈 *Onboarding Analytics*",
        "",
        f"*Active:* {analytics.get('active_count', 0)} onboardings",
        f"*Total profiles:* {analytics.get('total_profiles', analytics.get('completed_count', 0))}",
        "",
    ]

    return "\n".join(lines)


def render_daily_report(
    profiles: List[OnboardingProfile],
    progress_map: Optional[Dict] = None,
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
        name = profile.preferred_name or profile.full_name
        lines.append(f"*{name}* — {profile.role} ({profile.status.value})")
        lines.append("")

    return "\n".join(lines)
