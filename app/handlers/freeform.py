"""Freeform / fallback handler — LLM conversation + Internal Agent fallback."""

from __future__ import annotations

import logging
from typing import Optional

import httpx
from langchain_core.messages import HumanMessage, SystemMessage
from rival_agent_shared import (
    AgentInvocationRequest,
    AgentInvocationResponse,
    ProviderConfig,
    create_chat_model,
)

from ..config import get_settings
from ..models import OnboardingProfile
from ..state import get_all_task_progress, log_interaction
from ..template import load_template

log = logging.getLogger(__name__)

ONBOARDING_SYSTEM_PROMPT = """You are a warm, helpful onboarding assistant for Rival Intelligence.
You're helping {full_name} ({role}) settle into their new role.

Key context:
- They started on {start_date}
- Their line manager is {line_manager}
- They are based in {office}
- Today is day {day_number} of their onboarding

Current progress: {completed_tasks}/{total_tasks} tasks done.

Guidelines:
- Be warm, friendly, and encouraging — starting a new job is stressful
- Keep answers concise but helpful
- If they ask something you're not sure about, say so and offer to escalate
- Reference their onboarding tasks when relevant
- Use British English spelling
- Never make up information about the company — if unsure, suggest they ask their line manager or use the internal agent

You can help with:
- Questions about their onboarding tasks and what's next
- General orientation (tools, processes, culture)
- Connecting them with the right people
- Encouragement and moral support

If the question is about company-specific knowledge, policies, or technical details you don't have,
say you'll check with the knowledge base and return the answer from the internal agent.
"""


def handle_freeform(
    request: AgentInvocationRequest,
    profile: OnboardingProfile,
    use_internal_fallback: bool = True,
) -> AgentInvocationResponse:
    """Handle open-ended conversational messages."""
    settings = get_settings()
    template = load_template(profile.template_version)
    tasks = get_all_task_progress(profile.user_id)

    # Calculate progress
    total = sum(
        len(group.tasks)
        for phase in template.phases
        for group in phase.groups
    )
    completed = len([t for t in tasks if t.completed])

    # Calculate day number
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    day_number = max(1, (now - datetime.combine(profile.start_date, datetime.min.time()).replace(tzinfo=timezone.utc)).days + 1)

    system_prompt = ONBOARDING_SYSTEM_PROMPT.format(
        full_name=profile.full_name,
        role=profile.role,
        start_date=profile.start_date.strftime("%d %B %Y"),
        line_manager=profile.line_manager or "TBC",
        office=profile.office_location or "Remote",
        day_number=day_number,
        completed_tasks=completed,
        total_tasks=total,
    )

    # Try LLM response first
    try:
        provider_config = ProviderConfig(
            provider=request.provider or "google",
            model=request.model or "gemini-2.5-flash",
            temperature=0.7,
        )
        llm = create_chat_model(provider_config)

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=request.text),
        ]
        result = llm.invoke(messages)
        response_text = result.content

        # Check if the LLM indicated it needs to consult the knowledge base
        needs_fallback = any(
            phrase in response_text.lower()
            for phrase in [
                "check with the knowledge base",
                "i'll check",
                "let me find out",
                "i'm not sure about that specific",
                "i don't have that information",
            ]
        )

        if needs_fallback and use_internal_fallback:
            internal_response = _call_internal_agent(request, settings)
            if internal_response:
                response_text = (
                    f"I checked with our knowledge base, and here's what I found:\n\n"
                    f"{internal_response}\n\n"
                    f"_Let me know if you need anything else!_"
                )

    except Exception:
        log.exception("LLM call failed, falling back to internal agent")
        if use_internal_fallback:
            internal_response = _call_internal_agent(request, settings)
            if internal_response:
                response_text = internal_response
            else:
                response_text = (
                    "I'm having a bit of trouble right now. "
                    "Could you try again in a moment, or rephrase your question?"
                )
        else:
            response_text = (
                "I'm having a bit of trouble right now. "
                "Could you try again in a moment?"
            )

    # Log the interaction
    from ..models import InteractionType

    log_interaction(
        user_id=profile.user_id,
        interaction_type=InteractionType.QUESTION,
        message=request.text,
        response=response_text,
    )

    return AgentInvocationResponse(
        response_text=response_text,
        steps=["Freeform response"],
        citations=[],
        provider=request.provider,
        model=request.model,
        agent_id="onboarding",
    )


def _call_internal_agent(
    request: AgentInvocationRequest,
    settings,
) -> Optional[str]:
    """Call the internal agent for company-specific knowledge."""
    if not settings.internal_agent_url:
        return None

    try:
        payload = {
            "text": request.text,
            "user_id": request.user_id,
            "thread_id": request.thread_id,
            "provider": request.provider or "google",
            "model": request.model or "gemini-2.5-flash",
        }

        with httpx.Client(timeout=30.0) as client:
            resp = client.post(
                f"{settings.internal_agent_url}/v1/run",
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("response_text")

    except Exception:
        log.exception("Internal agent call failed")
        return None
