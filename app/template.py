"""Onboarding template loader and phase resolver."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from .models import (
    ContactPerson,
    OnboardingProfile,
    OnboardingSession,
    OnboardingTemplate,
    Phase,
    TaskDefinition,
    TaskGroup,
    TaskLink,
    TaskProgress,
    TaskStatus,
    TeamIntroduction,
)

log = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
_cached_template: Optional[OnboardingTemplate] = None


def load_template(version: str = "v2") -> OnboardingTemplate:
    """Load the onboarding template from YAML."""
    global _cached_template
    if _cached_template and _cached_template.version == version:
        return _cached_template

    path = _TEMPLATE_DIR / f"onboarding_{version}.yaml"
    if not path.exists():
        log.error("Template file not found: %s", path)
        raise FileNotFoundError(f"Template not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    template = _parse_template(raw)
    _cached_template = template
    log.info("Loaded onboarding template %s with %d phases", version, len(template.phases))
    return template


def _parse_template(raw: dict) -> OnboardingTemplate:
    """Parse raw YAML dict into an OnboardingTemplate."""
    phases = []
    for phase_data in raw.get("phases", []):
        groups = []
        for group_data in phase_data.get("groups", []):
            tasks = []
            for task_data in group_data.get("tasks", []):
                links = [TaskLink(**lk) for lk in task_data.get("links", [])]
                tasks.append(TaskDefinition(
                    id=task_data["id"],
                    title=task_data.get("title", ""),
                    description=task_data.get("description", ""),
                    subtasks=task_data.get("subtasks", []),
                    links=links,
                    tips=task_data.get("tips", ""),
                    verification=task_data.get("verification", ""),
                    internal_query=task_data.get("internal_query", ""),
                    dynamic_field=task_data.get("dynamic_field", ""),
                    auto_complete=task_data.get("auto_complete", False),
                    optional=task_data.get("optional", False),
                    recurring=task_data.get("recurring", False),
                ))
            groups.append(TaskGroup(
                id=group_data["id"],
                name=group_data.get("name", ""),
                intro=group_data.get("intro", ""),
                tasks=tasks,
                dynamic=group_data.get("dynamic", False),
                source=group_data.get("source", ""),
            ))
        phases.append(Phase(
            id=phase_data["id"],
            name=phase_data.get("name", ""),
            trigger_day=phase_data.get("trigger_day", 0),
            groups=groups,
        ))

    contacts: Dict[str, List[ContactPerson]] = {}
    for region, people in raw.get("contacts", {}).items():
        contacts[region] = [ContactPerson(**p) for p in people]

    return OnboardingTemplate(
        version=raw.get("version", "v2"),
        phases=phases,
        contacts=contacts,
    )


def get_onboarding_day(profile: OnboardingProfile) -> int:
    """Calculate the onboarding day number (0-indexed from start_date)."""
    today = date.today()
    start = profile.start_date
    if isinstance(start, str):
        start = date.fromisoformat(start)
    delta = (today - start).days
    return max(0, delta)


def get_current_phase(profile: OnboardingProfile, template: OnboardingTemplate) -> Optional[Phase]:
    """Get the current phase based on onboarding day."""
    day = get_onboarding_day(profile)
    current = None
    for phase in template.phases:
        if day >= phase.trigger_day:
            current = phase
    return current


def get_active_phases(profile: OnboardingProfile, template: OnboardingTemplate) -> List[Phase]:
    """Get all phases that are currently active (trigger_day <= current day)."""
    day = get_onboarding_day(profile)
    return [p for p in template.phases if day >= p.trigger_day]


def get_all_tasks(template: OnboardingTemplate) -> List[TaskDefinition]:
    """Get a flat list of all static tasks across all phases."""
    tasks = []
    for phase in template.phases:
        for group in phase.groups:
            if not group.dynamic:
                tasks.extend(group.tasks)
    return tasks


def get_next_incomplete_group(
    profile: OnboardingProfile,
    template: OnboardingTemplate,
    progress: Dict[str, TaskProgress],
) -> Optional[TaskGroup]:
    """Find the next task group that has incomplete tasks."""
    active_phases = get_active_phases(profile, template)
    for phase in active_phases:
        for group in phase.groups:
            if group.dynamic:
                continue
            has_incomplete = False
            for task in group.tasks:
                if task.auto_complete:
                    continue
                tp = progress.get(task.id)
                if not tp or tp.status == TaskStatus.NOT_STARTED:
                    has_incomplete = True
                    break
            if has_incomplete:
                return group
    return None


def get_overdue_tasks(
    profile: OnboardingProfile,
    template: OnboardingTemplate,
    progress: Dict[str, TaskProgress],
) -> List[TaskDefinition]:
    """Get tasks from earlier phases that should be done but aren't."""
    day = get_onboarding_day(profile)
    overdue = []
    for phase in template.phases:
        if phase.trigger_day > day:
            break
        # Only consider phases from at least 2 days ago as "overdue"
        if day - phase.trigger_day < 2:
            continue
        for group in phase.groups:
            if group.dynamic:
                continue
            for task in group.tasks:
                if task.auto_complete or task.optional:
                    continue
                tp = progress.get(task.id)
                if not tp or tp.status == TaskStatus.NOT_STARTED:
                    overdue.append(task)
    return overdue


def get_task_by_id(template: OnboardingTemplate, task_id: str) -> Optional[TaskDefinition]:
    """Look up a task by ID across the whole template."""
    for phase in template.phases:
        for group in phase.groups:
            for task in group.tasks:
                if task.id == task_id:
                    return task
    return None


def resolve_dynamic_tasks(
    group: TaskGroup,
    sessions: List[OnboardingSession],
    introductions: List[TeamIntroduction],
) -> List[TaskDefinition]:
    """Resolve dynamic task groups into concrete tasks."""
    if group.source == "sessions":
        return [
            TaskDefinition(
                id=f"session_{s.session_id}",
                title=f"{s.title} — {s.presenter}",
                description=f"Onboarding session with {s.presenter}",
            )
            for s in sessions
        ]
    elif group.source == "introductions":
        return [
            TaskDefinition(
                id=f"intro_{i.intro_id}",
                title=f"1-1 with {i.name} ({i.duration_minutes} min)",
                description=f"Team introduction — {i.region}",
            )
            for i in introductions
        ]
    return []


def find_contacts_for_topic(template: OnboardingTemplate, topic: str) -> List[ContactPerson]:
    """Find contacts who can help with a given topic."""
    topic_lower = topic.lower()
    matches = []
    for region, contacts in template.contacts.items():
        for contact in contacts:
            for area in contact.helps_with:
                if topic_lower in area.lower() or any(
                    word in area.lower() for word in topic_lower.split()
                ):
                    matches.append(contact)
                    break
    return matches
