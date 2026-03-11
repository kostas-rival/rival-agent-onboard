"""Firestore state management for onboarding profiles and progress."""

from __future__ import annotations

import logging
from datetime import datetime, timezone, date
from typing import Any, Dict, List, Optional

from google.cloud import firestore

from .config import get_settings
from .models import (
    FullProgress,
    InteractionLog,
    InteractionType,
    OnboardingProfile,
    OnboardingSession,
    OnboardingStatus,
    PhaseProgress,
    TaskProgress,
    TaskStatus,
    TeamIntroduction,
)

log = logging.getLogger(__name__)

_client: firestore.Client | None = None


def _get_client() -> firestore.Client:
    global _client
    if _client is None:
        settings = get_settings()
        _client = firestore.Client(
            project=settings.project_id,
            database=settings.firestore_database_id,
        )
        log.info(
            "Firestore client initialized: project=%s, database=%s",
            settings.project_id,
            settings.firestore_database_id,
        )
    return _client


def _collection():
    settings = get_settings()
    return _get_client().collection(settings.onboarding_collection)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── Profile CRUD ──────────────────────────────────────────────────────────


def create_profile(profile: OnboardingProfile) -> OnboardingProfile:
    """Create or overwrite an onboarding profile."""
    profile.created_at = _now()
    data = profile.model_dump(mode="json")
    # Convert date objects to string for Firestore
    if isinstance(data.get("start_date"), date):
        data["start_date"] = data["start_date"].isoformat()
    _collection().document(profile.slack_user_id).set(data)
    log.info("Created onboarding profile for %s (%s)", profile.name, profile.slack_user_id)
    return profile


def get_profile(slack_user_id: str) -> Optional[OnboardingProfile]:
    """Get an onboarding profile by Slack user ID."""
    doc = _collection().document(slack_user_id).get()
    if not doc.exists:
        return None
    data = doc.to_dict()
    # Handle Firestore timestamp conversion
    for field in ("created_at", "last_interaction"):
        val = data.get(field)
        if val and hasattr(val, "isoformat"):
            data[field] = val.isoformat()
    return OnboardingProfile(**data)


def get_profile_by_name(name: str) -> Optional[OnboardingProfile]:
    """Find a profile by name (case-insensitive partial match)."""
    name_lower = name.lower().strip()
    docs = _collection().stream()
    for doc in docs:
        data = doc.to_dict()
        doc_name = (data.get("name") or "").lower()
        if name_lower in doc_name or doc_name in name_lower:
            for field in ("created_at", "last_interaction"):
                val = data.get(field)
                if val and hasattr(val, "isoformat"):
                    data[field] = val.isoformat()
            return OnboardingProfile(**data)
    return None


def update_profile(slack_user_id: str, updates: Dict[str, Any]) -> None:
    """Update specific fields on a profile."""
    updates["last_interaction"] = _now()
    _collection().document(slack_user_id).update(updates)
    log.info("Updated profile %s: %s", slack_user_id, list(updates.keys()))


def delete_profile(slack_user_id: str) -> None:
    """Delete a profile and all subcollections."""
    doc_ref = _collection().document(slack_user_id)
    # Delete subcollections
    for sub in ("progress", "sessions", "introductions", "interactions"):
        for sub_doc in doc_ref.collection(sub).stream():
            sub_doc.reference.delete()
    doc_ref.delete()
    log.info("Deleted onboarding profile %s", slack_user_id)


def list_active_profiles() -> List[OnboardingProfile]:
    """List all active (and pending) onboarding profiles."""
    profiles = []
    for status in (OnboardingStatus.ACTIVE.value, OnboardingStatus.PENDING.value):
        query = _collection().where("status", "==", status)
        for doc in query.stream():
            data = doc.to_dict()
            for field in ("created_at", "last_interaction"):
                val = data.get(field)
                if val and hasattr(val, "isoformat"):
                    data[field] = val.isoformat()
            try:
                profiles.append(OnboardingProfile(**data))
            except Exception as exc:
                log.warning("Failed to parse profile %s: %s", doc.id, exc)
    return profiles


def list_all_profiles(include_completed: bool = False) -> List[OnboardingProfile]:
    """List all profiles, optionally including completed ones."""
    profiles = []
    for doc in _collection().stream():
        data = doc.to_dict()
        if not include_completed and data.get("status") == OnboardingStatus.COMPLETED.value:
            continue
        for field in ("created_at", "last_interaction"):
            val = data.get(field)
            if val and hasattr(val, "isoformat"):
                data[field] = val.isoformat()
        try:
            profiles.append(OnboardingProfile(**data))
        except Exception as exc:
            log.warning("Failed to parse profile %s: %s", doc.id, exc)
    return profiles


# ── Task Progress ─────────────────────────────────────────────────────────


def get_task_progress(slack_user_id: str, task_id: str) -> Optional[TaskProgress]:
    """Get progress for a specific task."""
    doc = _collection().document(slack_user_id).collection("progress").document(task_id).get()
    if not doc.exists:
        return None
    data = doc.to_dict()
    for field in ("completed_at", "skipped_at"):
        val = data.get(field)
        if val and hasattr(val, "isoformat"):
            data[field] = val.isoformat()
    return TaskProgress(**data)


def get_all_task_progress(slack_user_id: str) -> Dict[str, TaskProgress]:
    """Get all task progress for a user."""
    progress = {}
    for doc in _collection().document(slack_user_id).collection("progress").stream():
        data = doc.to_dict()
        for field in ("completed_at", "skipped_at"):
            val = data.get(field)
            if val and hasattr(val, "isoformat"):
                data[field] = val.isoformat()
        try:
            progress[doc.id] = TaskProgress(**data)
        except Exception:
            pass
    return progress


def mark_task_completed(
    slack_user_id: str,
    task_id: str,
    verified: bool = False,
    verification_details: str = "",
    notes: str = "",
) -> TaskProgress:
    """Mark a task as completed."""
    now = _now()
    tp = TaskProgress(
        task_id=task_id,
        status=TaskStatus.VERIFIED if verified else TaskStatus.COMPLETED,
        completed_at=now,
        verified=verified,
        verification_details=verification_details,
        notes=notes,
    )
    _collection().document(slack_user_id).collection("progress").document(task_id).set(
        tp.model_dump(mode="json")
    )
    # Update last_interaction on profile
    update_profile(slack_user_id, {"last_interaction": now})
    log.info("Task %s marked completed for %s (verified=%s)", task_id, slack_user_id, verified)
    return tp


def mark_task_skipped(slack_user_id: str, task_id: str, notes: str = "") -> TaskProgress:
    """Mark a task as skipped."""
    now = _now()
    tp = TaskProgress(
        task_id=task_id,
        status=TaskStatus.SKIPPED,
        skipped_at=now,
        notes=notes,
    )
    _collection().document(slack_user_id).collection("progress").document(task_id).set(
        tp.model_dump(mode="json")
    )
    update_profile(slack_user_id, {"last_interaction": now})
    log.info("Task %s marked skipped for %s", task_id, slack_user_id)
    return tp


# ── Sessions ──────────────────────────────────────────────────────────────


def save_sessions(slack_user_id: str, sessions: List[OnboardingSession]) -> None:
    """Save onboarding sessions for a user."""
    coll = _collection().document(slack_user_id).collection("sessions")
    for session in sessions:
        data = session.model_dump(mode="json")
        if session.scheduled_at and hasattr(session.scheduled_at, "isoformat"):
            data["scheduled_at"] = session.scheduled_at.isoformat()
        coll.document(session.session_id).set(data)
    log.info("Saved %d sessions for %s", len(sessions), slack_user_id)


def get_sessions(slack_user_id: str) -> List[OnboardingSession]:
    """Get all sessions for a user."""
    sessions = []
    for doc in _collection().document(slack_user_id).collection("sessions").stream():
        data = doc.to_dict()
        val = data.get("scheduled_at")
        if val and hasattr(val, "isoformat"):
            data["scheduled_at"] = val.isoformat()
        try:
            sessions.append(OnboardingSession(**data))
        except Exception:
            pass
    return sorted(sessions, key=lambda s: s.scheduled_at or datetime.max)


def mark_session_completed(slack_user_id: str, session_id: str) -> None:
    """Mark a session as completed."""
    _collection().document(slack_user_id).collection("sessions").document(session_id).update(
        {"completed": True}
    )


# ── Introductions ─────────────────────────────────────────────────────────


def save_introductions(slack_user_id: str, intros: List[TeamIntroduction]) -> None:
    """Save team introduction 1-1s for a user."""
    coll = _collection().document(slack_user_id).collection("introductions")
    for intro in intros:
        coll.document(intro.intro_id).set(intro.model_dump(mode="json"))
    log.info("Saved %d introductions for %s", len(intros), slack_user_id)


def get_introductions(slack_user_id: str) -> List[TeamIntroduction]:
    """Get all introductions for a user."""
    intros = []
    for doc in _collection().document(slack_user_id).collection("introductions").stream():
        try:
            intros.append(TeamIntroduction(**doc.to_dict()))
        except Exception:
            pass
    return intros


def mark_introduction_completed(slack_user_id: str, intro_id: str) -> None:
    """Mark a 1-1 introduction as completed."""
    _collection().document(slack_user_id).collection("introductions").document(intro_id).update(
        {"completed": True}
    )


# ── Interaction Log ───────────────────────────────────────────────────────


def log_interaction(
    slack_user_id: str,
    interaction_type: InteractionType,
    summary: str,
    details: Dict[str, Any] | None = None,
) -> None:
    """Log an onboarding interaction."""
    entry = InteractionLog(
        timestamp=_now(),
        interaction_type=interaction_type,
        summary=summary,
        details=details or {},
    )
    _collection().document(slack_user_id).collection("interactions").add(
        entry.model_dump(mode="json")
    )


def get_interaction_history(
    slack_user_id: str, limit: int = 50
) -> List[InteractionLog]:
    """Get recent interaction history."""
    entries = []
    query = (
        _collection()
        .document(slack_user_id)
        .collection("interactions")
        .order_by("timestamp", direction=firestore.Query.DESCENDING)
        .limit(limit)
    )
    for doc in query.stream():
        data = doc.to_dict()
        val = data.get("timestamp")
        if val and hasattr(val, "isoformat"):
            data["timestamp"] = val.isoformat()
        try:
            entries.append(InteractionLog(**data))
        except Exception:
            pass
    return list(reversed(entries))  # chronological order
