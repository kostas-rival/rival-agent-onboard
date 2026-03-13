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
    _collection().document(profile.user_id).set(data)
    log.info("Created onboarding profile for %s (%s)", profile.full_name, profile.user_id)
    return profile


def get_profile(user_id: str) -> Optional[OnboardingProfile]:
    """Get an onboarding profile by Slack user ID."""
    doc = _collection().document(user_id).get()
    if not doc.exists:
        return None
    data = doc.to_dict()
    # Handle Firestore timestamp conversion
    for field in ("created_at", "last_interaction", "activated_at", "completed_at"):
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
        doc_name = (data.get("full_name") or "").lower()
        if name_lower in doc_name or doc_name in name_lower:
            for field in ("created_at", "last_interaction", "activated_at", "completed_at"):
                val = data.get(field)
                if val and hasattr(val, "isoformat"):
                    data[field] = val.isoformat()
            return OnboardingProfile(**data)
    return None


def update_profile(user_id_or_profile, updates: Optional[Dict[str, Any]] = None) -> None:
    """Update a profile. Accepts (user_id, dict) or (profile_object)."""
    if isinstance(user_id_or_profile, OnboardingProfile):
        profile = user_id_or_profile
        data = profile.model_dump(mode="json")
        # Convert date objects
        if isinstance(data.get("start_date"), date):
            data["start_date"] = data["start_date"].isoformat()
        data["last_interaction"] = _now()
        _collection().document(profile.user_id).set(data, merge=True)
        log.info("Updated profile %s (full object)", profile.user_id)
    else:
        user_id = user_id_or_profile
        if updates is None:
            updates = {}
        updates["last_interaction"] = _now()
        _collection().document(user_id).update(updates)
        log.info("Updated profile %s: %s", user_id, list(updates.keys()))


def delete_profile(user_id: str) -> None:
    """Delete a profile and all subcollections."""
    doc_ref = _collection().document(user_id)
    # Delete subcollections
    for sub in ("progress", "sessions", "introductions", "interactions"):
        for sub_doc in doc_ref.collection(sub).stream():
            sub_doc.reference.delete()
    doc_ref.delete()
    log.info("Deleted onboarding profile %s", user_id)


def list_active_profiles() -> List[OnboardingProfile]:
    """List all active (and pending) onboarding profiles."""
    profiles = []
    for status in (OnboardingStatus.ACTIVE.value, OnboardingStatus.PENDING.value):
        query = _collection().where("status", "==", status)
        for doc in query.stream():
            data = doc.to_dict()
            for field in ("created_at", "last_interaction", "activated_at", "completed_at"):
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
        for field in ("created_at", "last_interaction", "activated_at", "completed_at"):
            val = data.get(field)
            if val and hasattr(val, "isoformat"):
                data[field] = val.isoformat()
        try:
            profiles.append(OnboardingProfile(**data))
        except Exception as exc:
            log.warning("Failed to parse profile %s: %s", doc.id, exc)
    return profiles


# ── Task Progress ─────────────────────────────────────────────────────────


def get_task_progress(user_id: str, task_id: str) -> Optional[TaskProgress]:
    """Get progress for a specific task."""
    doc = _collection().document(user_id).collection("progress").document(task_id).get()
    if not doc.exists:
        return None
    data = doc.to_dict()
    for field in ("completed_at", "skipped_at"):
        val = data.get(field)
        if val and hasattr(val, "isoformat"):
            data[field] = val.isoformat()
    return TaskProgress(**data)


def get_all_task_progress(user_id: str) -> Dict[str, TaskProgress]:
    """Get all task progress for a user, keyed by task_id."""
    progress = {}
    for doc in _collection().document(user_id).collection("progress").stream():
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
    user_id: str,
    task_id: str,
    verified: bool = False,
    auto_verified: bool = False,
    verification_details: str = "",
    notes: str = "",
) -> TaskProgress:
    """Mark a task as completed."""
    now = _now()
    is_verified = verified or auto_verified
    tp = TaskProgress(
        task_id=task_id,
        status=TaskStatus.VERIFIED if is_verified else TaskStatus.COMPLETED,
        completed_at=now,
        verified=is_verified,
        auto_verified=auto_verified,
        verification_details=verification_details,
        notes=notes,
    )
    _collection().document(user_id).collection("progress").document(task_id).set(
        tp.model_dump(mode="json")
    )
    # Update last_interaction on profile
    update_profile(user_id, {"last_interaction": now})
    log.info("Task %s marked completed for %s (verified=%s)", task_id, user_id, is_verified)
    return tp


def mark_task_skipped(user_id: str, task_id: str, notes: str = "") -> TaskProgress:
    """Mark a task as skipped."""
    now = _now()
    tp = TaskProgress(
        task_id=task_id,
        status=TaskStatus.SKIPPED,
        skipped_at=now,
        notes=notes,
    )
    _collection().document(user_id).collection("progress").document(task_id).set(
        tp.model_dump(mode="json")
    )
    update_profile(user_id, {"last_interaction": now})
    log.info("Task %s marked skipped for %s", task_id, user_id)
    return tp


# ── Sessions ──────────────────────────────────────────────────────────────


def save_session(session: OnboardingSession) -> None:
    """Save a single onboarding session."""
    user_id = session.user_id
    coll = _collection().document(user_id).collection("sessions")
    data = session.model_dump(mode="json")
    if session.scheduled_date:
        data["scheduled_date"] = session.scheduled_date.isoformat()
    coll.document(session.session_id).set(data)
    log.info("Saved session %s for %s", session.session_id, user_id)


def save_sessions(user_id: str, sessions: List[OnboardingSession]) -> None:
    """Save onboarding sessions for a user."""
    coll = _collection().document(user_id).collection("sessions")
    for session in sessions:
        data = session.model_dump(mode="json")
        if session.scheduled_date:
            data["scheduled_date"] = session.scheduled_date.isoformat()
        coll.document(session.session_id).set(data)
    log.info("Saved %d sessions for %s", len(sessions), user_id)


def get_sessions(user_id: str) -> List[OnboardingSession]:
    """Get all sessions for a user."""
    sessions = []
    for doc in _collection().document(user_id).collection("sessions").stream():
        data = doc.to_dict()
        # Handle date/timestamp fields
        for field in ("scheduled_date", "scheduled_at"):
            val = data.get(field)
            if val and hasattr(val, "isoformat"):
                data[field] = val.isoformat()
        try:
            sessions.append(OnboardingSession(**data))
        except Exception:
            pass
    return sorted(sessions, key=lambda s: s.scheduled_date)


def mark_session_completed(user_id: str, session_id: str) -> None:
    """Mark a session as completed."""
    _collection().document(user_id).collection("sessions").document(session_id).update(
        {"completed": True}
    )


# ── Introductions ─────────────────────────────────────────────────────────


def save_introductions(user_id: str, intros: List[TeamIntroduction]) -> None:
    """Save team introduction 1-1s for a user."""
    coll = _collection().document(user_id).collection("introductions")
    for intro in intros:
        coll.document(intro.intro_id).set(intro.model_dump(mode="json"))
    log.info("Saved %d introductions for %s", len(intros), user_id)


def get_introductions(user_id: str) -> List[TeamIntroduction]:
    """Get all introductions for a user."""
    intros = []
    for doc in _collection().document(user_id).collection("introductions").stream():
        try:
            intros.append(TeamIntroduction(**doc.to_dict()))
        except Exception:
            pass
    return intros


def mark_introduction_completed(user_id: str, intro_id: str) -> None:
    """Mark a 1-1 introduction as completed."""
    _collection().document(user_id).collection("introductions").document(intro_id).update(
        {"completed": True}
    )


# ── Interaction Log ───────────────────────────────────────────────────────


def log_interaction(
    user_id: str,
    interaction_type: InteractionType,
    summary: str = "",
    details: Dict[str, Any] | None = None,
    message: str = "",
    response: str = "",
) -> None:
    """Log an onboarding interaction."""
    # Support both summary/details and message/response patterns
    actual_summary = summary or message
    actual_details = details or {}
    if response:
        actual_details["response"] = response
    if message and not summary:
        actual_details["message"] = message

    entry = InteractionLog(
        timestamp=_now(),
        interaction_type=interaction_type,
        summary=actual_summary,
        details=actual_details,
    )
    _collection().document(user_id).collection("interactions").add(
        entry.model_dump(mode="json")
    )


def get_interaction_history(
    user_id: str, limit: int = 50
) -> List[InteractionLog]:
    """Get recent interaction history."""
    entries = []
    query = (
        _collection()
        .document(user_id)
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


# ── Link Click Tracking ──────────────────────────────────────────────────


def record_link_click(
    user_id: str,
    task_id: str,
    link_index: int = 0,
    link_url: str = "",
    link_label: str = "",
) -> None:
    """Record that a user clicked a tracked link."""
    from .models import LinkClick

    click = LinkClick(
        user_id=user_id,
        task_id=task_id,
        link_index=link_index,
        link_url=link_url,
        link_label=link_label,
        clicked_at=_now(),
    )
    _collection().document(user_id).collection("link_clicks").add(
        click.model_dump(mode="json")
    )
    log.info("Link click recorded: user=%s task=%s link=%s", user_id, task_id, link_label or link_url)


def get_link_clicks(user_id: str, task_id: str | None = None) -> List[Dict[str, Any]]:
    """Get link clicks for a user, optionally filtered by task_id."""
    query = _collection().document(user_id).collection("link_clicks")
    if task_id:
        query = query.where("task_id", "==", task_id)
    clicks = []
    for doc in query.stream():
        data = doc.to_dict()
        ts = data.get("clicked_at")
        if ts and hasattr(ts, "isoformat"):
            data["clicked_at"] = ts.isoformat()
        clicks.append(data)
    return clicks


def get_clicked_task_ids(user_id: str) -> set[str]:
    """Get the set of task_ids for which the user has clicked at least one link."""
    task_ids = set()
    for doc in _collection().document(user_id).collection("link_clicks").stream():
        data = doc.to_dict()
        tid = data.get("task_id")
        if tid:
            task_ids.add(tid)
    return task_ids


# ── Pending Briefings ─────────────────────────────────────────────────────


def _pending_briefings_collection():
    return _get_client().collection("pending_briefings")


def save_pending_briefing(admin_user_id: str, doc_id: str, doc_url: str) -> None:
    """Store a pending briefing doc that an admin is currently filling in."""
    _pending_briefings_collection().document(admin_user_id).set({
        "admin_user_id": admin_user_id,
        "doc_id": doc_id,
        "doc_url": doc_url,
        "created_at": _now(),
    })
    log.info("Saved pending briefing for admin %s → doc %s", admin_user_id, doc_id)


def get_pending_briefing(admin_user_id: str) -> Optional[Dict[str, Any]]:
    """Return the pending briefing for an admin, or None."""
    doc = _pending_briefings_collection().document(admin_user_id).get()
    if doc.exists:
        return doc.to_dict()
    return None


def delete_pending_briefing(admin_user_id: str) -> None:
    """Remove a pending briefing after it has been processed."""
    _pending_briefings_collection().document(admin_user_id).delete()
    log.info("Deleted pending briefing for admin %s", admin_user_id)
