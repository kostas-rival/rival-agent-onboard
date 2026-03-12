"""Automated verification — Slack profile, tool access, task completion checks."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from .config import get_settings
from .models import OnboardingStatus, TaskProgress
from .state import (
    get_all_task_progress,
    get_link_clicks,
    list_active_profiles,
    mark_task_completed,
)
from .template import get_task_by_id, load_template

log = logging.getLogger(__name__)

# Tasks that can be automatically verified via API checks
VERIFIABLE_TASKS = {
    "slack_profile_photo": "verify_slack_profile_photo",
    "slack_display_name": "verify_slack_display_name",
    "slack_status": "verify_slack_status",
    "slack_setup": "verify_slack_profile_complete",
    "google_workspace": "verify_google_workspace",
}


async def run_all_verifications() -> list[dict[str, Any]]:
    """Run automated verifications for all active onboardees.

    This combines two verification strategies:
    1. API-based checks (Slack profile, Google Workspace)
    2. Link-click verification (auto-complete tasks when all links have been clicked)
    """
    profiles = list_active_profiles()
    results = []

    for profile in profiles:
        tasks = get_all_task_progress(profile.user_id)
        completed_ids = {t.task_id for t in tasks.values() if t.completed}

        # ── Strategy 1: API-based verification ──────────────────────
        for task_id, verify_fn_name in VERIFIABLE_TASKS.items():
            if task_id in completed_ids:
                continue  # Already done

            verify_fn = VERIFICATION_FUNCTIONS.get(verify_fn_name)
            if not verify_fn:
                continue

            try:
                passed = await verify_fn(profile.user_id)
                if passed:
                    mark_task_completed(profile.user_id, task_id, auto_verified=True,
                                       verification_details="API verification passed")
                    results.append(
                        {
                            "user_id": profile.user_id,
                            "name": profile.full_name,
                            "task": task_id,
                            "status": "auto_completed",
                            "method": "api_check",
                        }
                    )
                    await _notify_auto_complete(profile.user_id, task_id)
                else:
                    results.append(
                        {
                            "user_id": profile.user_id,
                            "name": profile.full_name,
                            "task": task_id,
                            "status": "not_yet_done",
                            "method": "api_check",
                        }
                    )
            except Exception:
                log.exception(
                    "Verification %s failed for %s", task_id, profile.full_name
                )
                results.append(
                    {
                        "user_id": profile.user_id,
                        "name": profile.full_name,
                        "task": task_id,
                        "status": "error",
                        "method": "api_check",
                    }
                )

        # ── Strategy 2: Link-click verification ────────────────────
        link_results = await _verify_link_clicks(profile, completed_ids)
        results.extend(link_results)

    return results


async def _verify_link_clicks(profile, completed_ids: set[str]) -> list[dict[str, Any]]:
    """Auto-complete tasks where all links have been clicked."""
    results = []
    template = load_template(profile.template_version)

    for phase in template.phases:
        for group in phase.groups:
            if group.dynamic:
                continue
            for task in group.tasks:
                if task.id in completed_ids or task.auto_complete:
                    continue
                if not task.links:
                    continue  # No links to track

                # Check if all links have been clicked
                clicks = get_link_clicks(profile.user_id, task.id)
                clicked_indices = {c.get("link_index", -1) for c in clicks}
                all_clicked = all(i in clicked_indices for i in range(len(task.links)))

                if all_clicked:
                    mark_task_completed(
                        profile.user_id, task.id,
                        auto_verified=True,
                        verification_details=f"All {len(task.links)} link(s) clicked",
                    )
                    results.append({
                        "user_id": profile.user_id,
                        "name": profile.full_name,
                        "task": task.id,
                        "status": "auto_completed",
                        "method": "link_click",
                        "links_clicked": len(clicks),
                    })
                    await _notify_auto_complete(profile.user_id, task.id)

    return results


# ── Verification Functions ──────────────────────────────────────────────────


async def verify_slack_profile_photo(user_id: str) -> bool:
    """Check if the user has uploaded a Slack profile photo."""
    profile = await _get_slack_profile(user_id)
    if not profile:
        return False

    image = profile.get("image_original") or profile.get("image_512", "")
    # Default Slack avatars contain 'avatar' in the URL
    return bool(image) and "avatar" not in image.lower()


async def verify_slack_display_name(user_id: str) -> bool:
    """Check if the user has set a display name."""
    profile = await _get_slack_profile(user_id)
    if not profile:
        return False

    display_name = profile.get("display_name", "").strip()
    real_name = profile.get("real_name", "").strip()
    return bool(display_name) or bool(real_name)


async def verify_slack_status(user_id: str) -> bool:
    """Check if the user has set a Slack status."""
    profile = await _get_slack_profile(user_id)
    if not profile:
        return False

    status_text = profile.get("status_text", "").strip()
    return bool(status_text)


async def verify_slack_profile_complete(user_id: str) -> bool:
    """Check if the user's Slack profile is reasonably complete."""
    photo = await verify_slack_profile_photo(user_id)
    name = await verify_slack_display_name(user_id)
    return photo and name


async def verify_google_workspace(user_id: str) -> bool:
    """Check if the user can be found in Google Workspace (placeholder)."""
    # This would need Google Admin API access — for now, return False
    # so it's always manually confirmed.
    return False


# ── Slack API Helpers ───────────────────────────────────────────────────────


async def _get_slack_profile(user_id: str) -> Optional[dict]:
    """Fetch a user's Slack profile."""
    settings = get_settings()
    if not settings.slack_bot_token:
        return None

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://slack.com/api/users.profile.get",
                headers={"Authorization": f"Bearer {settings.slack_bot_token}"},
                params={"user": user_id},
            )
            data = resp.json()
            if data.get("ok"):
                return data.get("profile", {})
            log.warning("Slack profile fetch failed: %s", data)
            return None
    except Exception:
        log.exception("Failed to fetch Slack profile for %s", user_id)
        return None


async def _notify_auto_complete(user_id: str, task_id: str) -> None:
    """Send a DM to the user that a task was auto-verified."""
    settings = get_settings()
    if not settings.slack_bot_token:
        return

    task_labels = {
        "slack_profile_photo": "Upload profile photo",
        "slack_display_name": "Set display name",
        "slack_status": "Set Slack status",
        "slack_setup": "Complete Slack profile setup",
        "google_workspace": "Google Workspace access",
    }
    label = task_labels.get(task_id, task_id)

    try:
        async with httpx.AsyncClient() as client:
            open_resp = await client.post(
                "https://slack.com/api/conversations.open",
                headers={"Authorization": f"Bearer {settings.slack_bot_token}"},
                json={"users": user_id},
            )
            open_data = open_resp.json()
            if not open_data.get("ok"):
                return

            channel_id = open_data["channel"]["id"]
            await client.post(
                "https://slack.com/api/chat.postMessage",
                headers={"Authorization": f"Bearer {settings.slack_bot_token}"},
                json={
                    "channel": channel_id,
                    "text": (
                        f"✅ I noticed you've completed *{label}* — nice one! "
                        f"I've ticked it off your list automatically.\n\n"
                        f"Say `next` to see what's coming up, or `progress` to "
                        f"check your overall dashboard."
                    ),
                },
            )
    except Exception:
        log.exception("Failed to notify auto-complete for %s", user_id)


# Registry of verification functions
VERIFICATION_FUNCTIONS = {
    "verify_slack_profile_photo": verify_slack_profile_photo,
    "verify_slack_display_name": verify_slack_display_name,
    "verify_slack_status": verify_slack_status,
    "verify_slack_profile_complete": verify_slack_profile_complete,
    "verify_google_workspace": verify_google_workspace,
}
