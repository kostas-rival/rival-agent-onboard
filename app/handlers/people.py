"""People lookup and contact handler."""

from __future__ import annotations

import logging
from typing import List, Optional

from rival_agent_shared import AgentInvocationRequest, AgentInvocationResponse

from ..models import ContactPerson, OnboardingProfile
from ..renderer import render_contacts
from ..template import find_contacts_for_topic, load_template

log = logging.getLogger(__name__)


def handle_who_is(
    request: AgentInvocationRequest,
    profile: OnboardingProfile,
    topic: Optional[str] = None,
) -> AgentInvocationResponse:
    """Handle 'who handles X' or 'who is Y' questions."""
    template = load_template(profile.template_version)
    query = topic or request.text

    # Try to find matching contacts
    matches = find_contacts_for_topic(template, query)

    if matches:
        response = _render_matched_contacts(matches, query)
    else:
        # Show all contacts as a fallback
        response = (
            f"I'm not sure exactly who handles \"{query}\", "
            f"but here are your key support contacts:\n\n"
            + render_contacts(template.contacts)
        )

    return AgentInvocationResponse(
        response_text=response,
        steps=["People lookup"],
        citations=[],
        provider=request.provider,
        model=request.model,
        agent_id="onboarding",
    )


def handle_show_contacts(
    request: AgentInvocationRequest,
    profile: OnboardingProfile,
) -> AgentInvocationResponse:
    """Show all support contacts."""
    template = load_template(profile.template_version)
    response = render_contacts(template.contacts)

    return AgentInvocationResponse(
        response_text=response,
        steps=["Contacts listed"],
        citations=[],
        provider=request.provider,
        model=request.model,
        agent_id="onboarding",
    )


def _render_matched_contacts(contacts: List[ContactPerson], query: str) -> str:
    """Render matched contacts with context."""
    lines = [f"For *{query}*, your go-to people are:\n"]

    for contact in contacts:
        emoji = contact.emoji or "👤"
        name_display = contact.full_name or contact.name
        lines.append(f"{emoji} *{name_display}* — {contact.role}")
        for area in contact.helps_with:
            lines.append(f"  → {area}")
        lines.append("")

    return "\n".join(lines)
