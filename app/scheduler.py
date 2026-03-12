"""Scheduler — daily check-ins, session prep reminders, admin reports."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from .config import get_settings
from .models import OnboardingStatus
from .renderer import render_daily_report, render_progress_dashboard
from .state import (
    get_all_task_progress,
    get_profile,
    get_sessions,
    list_active_profiles,
)
from .template import get_next_incomplete_group, get_overdue_tasks, load_template

log = logging.getLogger(__name__)


class DailyCheckinGenerator:
    """Generates and sends daily check-in DMs to active onboardees."""

    def __init__(self) -> None:
        self.settings = get_settings()

    async def send_daily_checkins(self) -> list[dict[str, Any]]:
        """Send a personalised morning DM to each active onboardee."""
        profiles = list_active_profiles()
        results = []

        for profile in profiles:
            try:
                message = self._build_checkin_message(profile)
                await self._send_slack_dm(profile.user_id, message)
                results.append(
                    {"user_id": profile.user_id, "name": profile.full_name, "status": "sent"}
                )
            except Exception:
                log.exception("Failed to send check-in to %s", profile.full_name)
                results.append(
                    {"user_id": profile.user_id, "name": profile.full_name, "status": "failed"}
                )

        return results

    async def send_session_prep_reminders(self) -> list[dict[str, Any]]:
        """Send prep reminders for sessions happening tomorrow."""
        profiles = list_active_profiles()
        results = []
        tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).date()

        for profile in profiles:
            sessions = get_sessions(profile.user_id)
            upcoming = [
                s
                for s in sessions
                if not s.completed and s.scheduled_date == tomorrow
            ]

            for session in upcoming:
                try:
                    message = (
                        f"📅 *Reminder: {session.title}* is tomorrow!\n\n"
                        f"> {session.description}\n\n"
                        f"Say `prep for {session.session_type}` to get preparation tips."
                    )
                    await self._send_slack_dm(profile.user_id, message)
                    results.append(
                        {
                            "user_id": profile.user_id,
                            "session": session.title,
                            "status": "sent",
                        }
                    )
                except Exception:
                    log.exception(
                        "Failed to send session prep reminder for %s", session.title
                    )
                    results.append(
                        {
                            "user_id": profile.user_id,
                            "session": session.title,
                            "status": "failed",
                        }
                    )

        return results

    def _build_checkin_message(self, profile) -> str:
        """Build a personalised daily check-in message."""
        template = load_template(profile.template_version)
        tasks = get_all_task_progress(profile.user_id)

        # Day number
        now = datetime.now(timezone.utc)
        start_dt = datetime.combine(
            profile.start_date, datetime.min.time()
        ).replace(tzinfo=timezone.utc)
        day_number = max(1, (now - start_dt).days + 1)

        # Calculate completion
        total = sum(
            len(group.tasks) for phase in template.phases for group in phase.groups
        )
        completed = len([t for t in tasks.values() if t.completed])
        pct = int((completed / total) * 100) if total > 0 else 0

        # Overdue tasks
        overdue = get_overdue_tasks(template, tasks, profile.start_date)

        # Next group
        next_group = get_next_incomplete_group(template, tasks)

        lines = [
            f"☀️ *Good morning, {profile.full_name.split()[0]}!*",
            f"It's *Day {day_number}* of your onboarding.\n",
        ]

        # Progress bar
        filled = int(pct / 10)
        bar = "█" * filled + "░" * (10 - filled)
        lines.append(f"Progress: [{bar}] {pct}% ({completed}/{total} tasks)\n")

        # Overdue warning
        if overdue:
            lines.append(f"⚠️ You have *{len(overdue)}* overdue task(s):")
            for task in overdue[:3]:
                lines.append(f"  • {task.title}")
            lines.append("")

        # Today's focus
        if next_group:
            lines.append(f"📋 *Today's focus:* {next_group.title}")
            incomplete = [
                t
                for t in next_group.tasks
                if not any(
                    tp.task_id == t.id and tp.completed for tp in tasks.values()
                )
            ]
            for task in incomplete[:3]:
                lines.append(f"  ☐ {task.title}")
            lines.append("")

        lines.append("Say `next` to get started, or `progress` for your full dashboard.")

        # Auto-complete check — if 30 days have passed
        if day_number >= self.settings.active_duration_days:
            lines.extend([
                "",
                "---",
                f"🎓 *You've reached Day {self.settings.active_duration_days}!*",
                "Your formal onboarding period is wrapping up.",
                "Your line manager will do a final check-in with you.",
            ])

        return "\n".join(lines)

    async def _send_slack_dm(self, user_id: str, text: str) -> None:
        """Send a Slack DM to a user."""
        if not self.settings.slack_bot_token:
            log.warning("No SLACK_BOT_TOKEN — skipping DM to %s", user_id)
            return

        async with httpx.AsyncClient() as client:
            # Open DM channel
            open_resp = await client.post(
                "https://slack.com/api/conversations.open",
                headers={"Authorization": f"Bearer {self.settings.slack_bot_token}"},
                json={"users": user_id},
            )
            open_data = open_resp.json()
            if not open_data.get("ok"):
                raise RuntimeError(f"Failed to open DM: {open_data}")

            channel_id = open_data["channel"]["id"]

            # Send message
            msg_resp = await client.post(
                "https://slack.com/api/chat.postMessage",
                headers={"Authorization": f"Bearer {self.settings.slack_bot_token}"},
                json={"channel": channel_id, "text": text},
            )
            msg_data = msg_resp.json()
            if not msg_data.get("ok"):
                raise RuntimeError(f"Failed to send DM: {msg_data}")


async def send_daily_admin_reports() -> None:
    """Send the daily admin report to configured recipients."""
    settings = get_settings()
    profiles = list_active_profiles()
    all_progress = {}

    for p in profiles:
        tasks = get_all_task_progress(p.user_id)
        all_progress[p.user_id] = tasks

    report = render_daily_report(profiles, all_progress)

    if not report.strip():
        log.info("No active onboardings — skipping admin report.")
        return

    # Send to each configured recipient
    recipients = list(settings.daily_report_recipients)

    if not settings.slack_bot_token:
        log.warning("No SLACK_BOT_TOKEN — cannot send admin reports")
        return

    async with httpx.AsyncClient() as client:
        for recipient_id in recipients:
            try:
                # Open DM
                open_resp = await client.post(
                    "https://slack.com/api/conversations.open",
                    headers={
                        "Authorization": f"Bearer {settings.slack_bot_token}"
                    },
                    json={"users": recipient_id},
                )
                open_data = open_resp.json()
                if not open_data.get("ok"):
                    log.error("Failed to open DM to %s: %s", recipient_id, open_data)
                    continue

                channel_id = open_data["channel"]["id"]

                # Send report
                await client.post(
                    "https://slack.com/api/chat.postMessage",
                    headers={
                        "Authorization": f"Bearer {settings.slack_bot_token}"
                    },
                    json={"channel": channel_id, "text": report},
                )
            except Exception:
                log.exception("Failed to send admin report to %s", recipient_id)

    # Also send to the daily report channel if configured
    if settings.daily_report_channel:
        try:
            async with httpx.AsyncClient() as client:
                await client.post(
                    "https://slack.com/api/chat.postMessage",
                    headers={
                        "Authorization": f"Bearer {settings.slack_bot_token}"
                    },
                    json={
                        "channel": settings.daily_report_channel,
                        "text": report,
                    },
                )
        except Exception:
            log.exception("Failed to send admin report to channel")
