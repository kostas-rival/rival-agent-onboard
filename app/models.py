"""Data models for the onboarding agent."""

from __future__ import annotations

from datetime import datetime, date
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ── Enums ──────────────────────────────────────────────────────────────────


class OnboardingStatus(str, Enum):
    PENDING = "pending"       # Profile created, start date not reached
    ACTIVE = "active"         # Onboarding in progress
    COMPLETED = "completed"   # All phases done or manually completed
    PAUSED = "paused"         # Temporarily suspended


class TaskStatus(str, Enum):
    NOT_STARTED = "not_started"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    VERIFIED = "verified"     # Completed AND automatically verified


class InteractionType(str, Enum):
    CHECKIN = "checkin"
    TASK_COMPLETED = "task_completed"
    TASK_SKIPPED = "task_skipped"
    QUESTION = "question"
    ADMIN = "admin"
    SESSION_PREP = "session_prep"
    WELCOME = "welcome"
    NUDGE = "nudge"


# ── Onboarding Profile ────────────────────────────────────────────────────


class OnboardingProfile(BaseModel):
    """Firestore document representing a new starter's onboarding state."""

    slack_user_id: str = Field(..., description="Slack user ID of the new starter")
    name: str = Field(..., description="Full name")
    preferred_name: str = Field(default="", description="Preferred first name")
    start_date: date = Field(..., description="Start date at Rival")
    role: str = Field(..., description="Job title / role")
    team: str = Field(default="", description="Team name (product, strategy, creative, etc.)")
    location: str = Field(default="", description="Office location (London, Cape Town, NYC, etc.)")
    timezone: str = Field(default="Europe/London", description="IANA timezone")
    manager_name: str = Field(default="")
    manager_slack_id: str = Field(default="")
    status: OnboardingStatus = Field(default=OnboardingStatus.PENDING)
    briefing_doc_url: str = Field(default="", description="URL of the source briefing Google Doc")
    briefing_doc_id: str = Field(default="", description="Google Doc ID of the source briefing")
    generated_doc_url: str = Field(default="", description="URL of the generated onboarding doc")
    generated_doc_id: str = Field(default="", description="Google Doc ID of the generated doc")
    template_version: str = Field(default="v2")
    current_phase: str = Field(default="day_1")
    current_group: str = Field(default="tools_setup")
    created_at: Optional[datetime] = None
    created_by: str = Field(default="", description="Slack user ID of admin who created this")
    last_interaction: Optional[datetime] = None
    daily_checkin_enabled: bool = Field(default=True)
    role_specific_notes: List[str] = Field(default_factory=list)
    tool_access_notes: Dict[str, str] = Field(default_factory=dict)
    welcome_sent: bool = Field(default=False)


# ── Task Progress ─────────────────────────────────────────────────────────


class TaskProgress(BaseModel):
    """Progress record for a single onboarding task."""

    task_id: str
    status: TaskStatus = Field(default=TaskStatus.NOT_STARTED)
    completed_at: Optional[datetime] = None
    skipped_at: Optional[datetime] = None
    verified: bool = False
    verification_details: str = ""
    notes: str = ""


# ── Session / Introduction ────────────────────────────────────────────────


class OnboardingSession(BaseModel):
    """A scheduled onboarding session (e.g., 'Product 101 — Kostas')."""

    session_id: str
    title: str
    presenter: str
    scheduled_at: Optional[datetime] = None
    completed: bool = False
    prep_notes: str = ""


class TeamIntroduction(BaseModel):
    """A 1-1 meeting with a team member."""

    intro_id: str
    name: str
    region: str = ""
    duration_minutes: int = 15
    scheduled: bool = False
    completed: bool = False


# ── Interaction Log ───────────────────────────────────────────────────────


class InteractionLog(BaseModel):
    """A log entry for an onboarding interaction."""

    timestamp: datetime
    interaction_type: InteractionType
    summary: str
    details: Dict[str, Any] = Field(default_factory=dict)


# ── Template Models ───────────────────────────────────────────────────────


class TaskLink(BaseModel):
    label: str
    url: str


class TaskDefinition(BaseModel):
    """A task within the onboarding template."""

    id: str
    title: str
    description: str = ""
    subtasks: List[str] = Field(default_factory=list)
    links: List[TaskLink] = Field(default_factory=list)
    tips: str = ""
    verification: str = ""            # verification method key
    internal_query: str = ""          # query to send to internal agent
    dynamic_field: str = ""           # field from profile to resolve
    auto_complete: bool = False       # auto-marked as done
    optional: bool = False
    recurring: bool = False


class TaskGroup(BaseModel):
    """A group of related tasks within a phase."""

    id: str
    name: str
    intro: str = ""
    tasks: List[TaskDefinition] = Field(default_factory=list)
    dynamic: bool = False             # tasks populated from profile data
    source: str = ""                  # 'sessions' | 'introductions'


class Phase(BaseModel):
    """A phase of the onboarding (day_1, week_1, month_1, reviews)."""

    id: str
    name: str
    trigger_day: int = 0
    groups: List[TaskGroup] = Field(default_factory=list)


class ContactPerson(BaseModel):
    """A support contact."""

    name: str
    full_name: str = ""
    role: str
    emoji: str = ""
    helps_with: List[str] = Field(default_factory=list)


class OnboardingTemplate(BaseModel):
    """The full onboarding template loaded from YAML."""

    version: str = "v2"
    phases: List[Phase] = Field(default_factory=list)
    contacts: Dict[str, List[ContactPerson]] = Field(default_factory=dict)


# ── Briefing Data (parsed from Google Doc) ────────────────────────────────


class BriefingData(BaseModel):
    """Structured data parsed from a briefing Google Doc."""

    name: str = ""
    preferred_name: str = ""
    start_date: Optional[date] = None
    role: str = ""
    team: str = ""
    location: str = ""
    manager: str = ""
    manager_slack: str = ""
    local_introductions: List[Dict[str, Any]] = Field(default_factory=list)
    regional_introductions: List[Dict[str, Any]] = Field(default_factory=list)
    sessions: List[Dict[str, Any]] = Field(default_factory=list)
    review_30_day: Optional[date] = None
    review_90_day: Optional[date] = None
    role_specific_notes: List[str] = Field(default_factory=list)
    tool_access_notes: Dict[str, str] = Field(default_factory=dict)
    team_lunch: bool = False
    raw_text: str = ""


# ── Progress Summary ──────────────────────────────────────────────────────


class PhaseProgress(BaseModel):
    """Progress summary for a single phase."""

    phase_id: str
    phase_name: str
    total_tasks: int = 0
    completed_tasks: int = 0
    skipped_tasks: int = 0
    percentage: float = 0.0
    outstanding_tasks: List[str] = Field(default_factory=list)


class FullProgress(BaseModel):
    """Full onboarding progress across all phases."""

    profile: OnboardingProfile
    phases: List[PhaseProgress] = Field(default_factory=list)
    total_tasks: int = 0
    total_completed: int = 0
    total_skipped: int = 0
    overall_percentage: float = 0.0
    onboarding_day: int = 0
    days_remaining: int = 30
    overdue_tasks: List[str] = Field(default_factory=list)
    upcoming_sessions: List[OnboardingSession] = Field(default_factory=list)
