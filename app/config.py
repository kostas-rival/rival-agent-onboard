"""Configuration for the onboarding agent."""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Onboarding agent configuration loaded from environment variables."""

    service_name: str = Field(default="rival-agent-onboard")
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8080)

    # GCP / Firestore
    project_id: str = Field(default="rival-agents", description="GCP project for Firestore")
    firestore_database_id: str = Field(default="agentic-rival")
    onboarding_collection: str = Field(default="onboarding_profiles")
    templates_collection: str = Field(default="onboarding_templates")

    # LLM providers
    gemini_api_key: Optional[str] = Field(default=None)
    openai_api_key: Optional[str] = Field(default=None)
    anthropic_api_key: Optional[str] = Field(default=None)
    default_provider: str = Field(default="gemini")
    default_model: str = Field(default="gemini-2.5-flash")

    # Internal agent (knowledge fallback)
    internal_agent_url: str = Field(
        default="https://rival-agent-internal-730268527569.europe-west1.run.app",
        description="URL of the internal knowledge agent for freeform Q&A fallback",
    )

    # Google Drive
    drive_folder_id: str = Field(
        default="1sYBRHgFokkAGYegaJtiMYreyLoBq4VRa",
        description="Google Drive folder ID for generated onboarding docs",
    )
    template_doc_id: str = Field(
        default="1d_3mw-Td8MZcycTiYfsRt-syF52XvurE02xuiMyy9ZE",
        description="Google Doc ID of the onboarding briefing template",
    )
    drive_service_account: str = Field(
        default="ai-knowledge@rival-agents.iam.gserviceaccount.com",
        description="Service account with Drive access",
    )

    # Slack
    slack_bot_token: Optional[str] = Field(default=None, description="Slack bot token for DMs and verification")

    # Admin
    admin_slack_ids: str = Field(
        default="",
        description="Comma-separated Slack user IDs or names that have admin access",
    )
    daily_report_channel: str = Field(
        default="",
        description="Slack channel ID to post daily onboarding status reports",
    )
    daily_report_recipients: str = Field(
        default="",
        description="Comma-separated Slack user IDs to DM daily reports to",
    )

    # Service URL (for building tracking links)
    service_url: str = Field(
        default="https://rival-agent-onboard-730268527569.europe-west1.run.app",
        description="Public URL of this service, used to build tracked redirect links",
    )

    # Onboarding
    active_duration_days: int = Field(
        default=30,
        description="Number of days an onboarding remains active (until 30-day check-in)",
    )
    daily_checkin_hour_utc: int = Field(default=8, description="Hour (UTC) to send daily check-ins")

    request_timeout_seconds: int = Field(default=120)

    model_config = SettingsConfigDict(env_file=(".env",), env_prefix="ONBOARDING_AGENT_")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
