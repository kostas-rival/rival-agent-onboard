"""LLM-powered intent classification for onboarding conversations."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel, Field

from .config import get_settings
from .models import OnboardingProfile

log = logging.getLogger(__name__)

_INTENT_SYSTEM_PROMPT = """You are the intent classifier for Rival's Onboarding Guide agent.
Your job is to determine what the user wants from their message in the context of their onboarding journey.

The user may be a new starter going through onboarding, or an admin managing onboarding for others.

INTENTS:
- greeting: Simple hello, hi, hey, or initial contact
- get_started: User wants to begin or continue onboarding ("let's go", "what's first", "start")
- next_task: User wants the next task or group ("next", "what's next", "continue", "move on")
- mark_complete: User is reporting a task as done ("done", "finished X", "completed", "I've set up X")
- progress: User wants to see their progress ("how am I doing", "progress", "status", "dashboard")
- ask_question: User asks about a specific tool, process, or topic related to onboarding
- who_is: User asks about a person or who to contact ("who handles expenses", "who is Jess")
- show_schedule: User asks about upcoming sessions or meetings ("what's this week", "my schedule")
- skip_task: User wants to skip a task ("skip", "not now", "later")
- session_prep: User asks about preparing for an upcoming session
- admin_read_briefing: Admin providing a Google Doc URL to read a briefing
- admin_new_onboard: Admin wants to start onboarding someone new ("onboard someone", "new starter")
- admin_briefing_done: Admin says they finished filling in the briefing doc ("done with briefing", "filled it in")
- admin_create: Admin creating a new onboarding profile manually
- admin_list: Admin asking to list active onboardings
- admin_progress: Admin asking about a specific person's progress ("progress for @name")
- admin_activate: Admin activating a pending onboarding ("activate <name>")
- admin_pause: Admin pausing/resuming an onboarding
- admin_complete: Admin marking an onboarding as complete
- admin_update: Admin updating an onboarding (add task, change date, etc.)
- admin_analytics: Admin requesting aggregate analytics
- admin_report: Admin requesting a status report
- freeform: Any other question that should be answered using knowledge/LLM

CONTEXT:
{context}

RULES:
- If the message contains a Google Doc URL (docs.google.com/document/...), classify as admin_read_briefing
- If the message starts with "list active" or "show onboardings", classify as admin_list
- If the message contains "progress for" or "status of" followed by a name/@mention, classify as admin_progress
- If the message contains "analytics" or "stats" or "metrics", classify as admin_analytics
- If the message says "done", "finished", "completed", "set up" referencing a task, classify as mark_complete
- If the message says "next", "continue", "what's next", classify as next_task
- If the message asks "who" + verb (handles, manages, is responsible), classify as who_is
- For ambiguous messages, consider: is the user a new starter or an admin?
- "pause" or "resume" followed by a name → admin_pause
- "activate" followed by a name → admin_activate
- "complete onboarding" for a name → admin_complete
- "report" or "daily report" or "update" (from admin) → admin_report
- Multiple intents: if "done with X, what's next" → mark_complete (primary, with task context)

OUTPUT: Return JSON with these fields:
- intent: one of the intent types listed above
- task_ids: list of task IDs if relevant (for mark_complete, skip_task)
- task_keywords: list of keywords from the message that hint at which tasks (e.g., "slack", "google drive", "1password")
- entity: person name or topic if relevant (for who_is, admin_progress)
- question_topic: the topic being asked about (for ask_question, freeform)
- confidence: float 0-1
- secondary_intent: optional second intent (e.g., "next_task" after "mark_complete")
- doc_url: Google Doc URL if present

Return ONLY the JSON object. No surrounding text."""


class OnboardingIntent(BaseModel):
    """Structured output from intent classification."""

    intent: str = Field(..., description="The classified intent type")
    task_ids: List[str] = Field(default_factory=list, description="Task IDs if relevant")
    task_keywords: List[str] = Field(default_factory=list, description="Keywords hinting at tasks")
    entity: Optional[str] = Field(default=None, description="Person name or topic")
    question_topic: Optional[str] = Field(default=None, description="Topic being asked about")
    confidence: float = Field(default=0.8, description="Confidence 0-1")
    secondary_intent: Optional[str] = Field(default=None, description="Optional follow-up intent")
    doc_url: Optional[str] = Field(default=None, description="Google Doc URL if present")


# ── Fast pattern matching (no LLM needed) ─────────────────────────────────

_GREETING_PATTERNS = {"hello", "hi", "hey", "start", "get started"}
_NEXT_PATTERNS = {"next", "continue", "what's next", "whats next", "move on", "keep going"}
_AFFIRMATIVE_PATTERNS = {"yes", "yep", "yeah", "sure", "ok", "okay", "let's go", "lets go", "ready", "let's do it", "lets do it", "begin", "bring it on"}
_PROGRESS_PATTERNS = {
    "progress", "how am i doing", "status", "dashboard", "how's it going",
    "how far", "where am i", "what have i done", "which things",
    "check my", "my progress", "what's left", "whats left",
    "what do i need to do", "what should i do", "what's remaining",
    "show me my", "can you check", "check which",
}
_DONE_KEYWORDS = {
    "done", "finished", "completed", "sorted", "all set",
    "i've done", "ive done", "i have done", "i've set up", "ive set up",
    "i set up", "i have set up",
    "i've finished", "ive finished", "i've completed", "ive completed",
    "i've sorted", "ive sorted",
    "i read", "i've read", "ive read",
    "i logged", "i've logged", "ive logged", "logged into",
    "i visited", "i've visited", "ive visited",
    "i joined", "i've joined", "ive joined",
    "i set up", "i've set up",
    "i created", "i've created", "ive created",
    "i added", "i've added", "ive added",
    "already did", "already done", "already set up",
    "took care of", "all sorted", "good to go",
}
_SKIP_KEYWORDS = {"skip", "not now", "later", "pass"}

_TASK_KEYWORD_MAP = {
    "google": "google_drive",
    "drive": "google_drive",
    "calendar": "google_drive",
    "google drive": "google_drive",
    "slack": "slack_setup",
    "slack profile": "slack_setup",
    "slack policy": "slack_policy",
    "charlie": "charlie_hr",
    "charliehr": "charlie_hr",
    "charlie hr": "charlie_hr",
    "hr portal": "charlie_hr",
    "hr profile": "charlie_hr",
    "1password": "onepassword",
    "one password": "onepassword",
    "password": "onepassword",
    "productive": "productive_setup",
    "time tracking": "productive_setup",
    "rival intelligence": "ri_first_use",
    "ri channel": "ri_channel",
    "#rival-intelligence": "ri_channel",
    "email": "email_sig",
    "signature": "email_sig",
    "email signature": "email_sig",
    "handbook": "handbook",
    "rival handbook": "handbook",
    "org chart": "org_chart",
    "organisation chart": "org_chart",
    "gtky": "gtky_card",
    "get to know": "gtky_card",
    "get to know you": "gtky_card",
    "headshot": "headshot_bio",
    "bio": "headshot_bio",
    "linkedin": "linkedin_banner",
    "linkedin banner": "linkedin_banner",
    "stand-up": "team_standup",
    "standup": "team_standup",
    "daily standup": "team_standup",
    "taco": "taco_friday",
    "taco friday": "taco_friday",
    "podcast": "social_channels",
    "content hub": "content_hub",
    "delivery sop": "delivery_sop",
    "sop": "delivery_sop",
    "dev review": "dev_review",
    "meet the team": "meet_team_website",
}


def _fast_classify(text: str, is_admin: bool) -> Optional[OnboardingIntent]:
    """Attempt fast pattern-based classification without LLM."""
    lower = text.lower().strip()

    # Check for Google Doc URL → admin briefing
    if "docs.google.com/document/" in lower or "drive.google.com" in lower:
        import re
        url_match = re.search(r"https?://docs\.google\.com/document/d/[a-zA-Z0-9_-]+(?:/[^\s]*)?", text)
        return OnboardingIntent(
            intent="admin_read_briefing",
            doc_url=url_match.group(0) if url_match else text.strip(),
            confidence=0.95,
        )

    # Admin commands
    if is_admin:
        if lower.startswith("list active") or lower.startswith("show onboarding") or lower == "list":
            return OnboardingIntent(intent="admin_list", confidence=0.95)
        if lower.startswith("analytics") or lower.startswith("stats"):
            return OnboardingIntent(intent="admin_analytics", confidence=0.9)
        if lower.startswith("report") or "daily report" in lower or "status report" in lower:
            return OnboardingIntent(intent="admin_report", confidence=0.9)
        if "progress for" in lower or "status of" in lower:
            # Extract name after "for" or "of"
            import re
            name_match = re.search(r"(?:progress for|status of)\s+@?(\w[\w\s]*)", lower)
            entity = name_match.group(1).strip() if name_match else None
            return OnboardingIntent(intent="admin_progress", entity=entity, confidence=0.9)
        if lower.startswith("pause") or lower.startswith("resume"):
            import re
            name_match = re.search(r"(?:pause|resume)\s+@?(\w[\w\s]*)", lower)
            entity = name_match.group(1).strip() if name_match else None
            return OnboardingIntent(intent="admin_pause", entity=entity, confidence=0.9)
        if lower.startswith("activate"):
            import re
            name_match = re.search(r"activate\s+@?(\w[\w\s]*)", lower)
            entity = name_match.group(1).strip() if name_match else None
            return OnboardingIntent(intent="admin_activate", entity=entity, confidence=0.95)
        if lower.startswith("complete onboarding") or lower.startswith("finish onboarding"):
            import re
            name_match = re.search(r"(?:complete|finish)\s+onboarding\s+(?:for\s+)?@?(\w[\w\s]*)", lower)
            entity = name_match.group(1).strip() if name_match else None
            return OnboardingIntent(intent="admin_complete", entity=entity, confidence=0.9)

    # Greetings
    if lower in _GREETING_PATTERNS or (len(lower.split()) <= 3 and any(g in lower for g in _GREETING_PATTERNS)):
        return OnboardingIntent(intent="greeting", confidence=0.95)

    # Next task
    if lower in _NEXT_PATTERNS or any(p in lower for p in _NEXT_PATTERNS):
        return OnboardingIntent(intent="next_task", confidence=0.9)

    # Affirmative / get started ("yes", "let's go", "sure")
    if lower in _AFFIRMATIVE_PATTERNS:
        return OnboardingIntent(intent="get_started", confidence=0.9)

    # Admin: start a new onboarding — directly triggers doc creation
    if is_admin and any(p in lower for p in (
        "onboard someone", "onboard a new", "new onboarding",
        "new starter", "add new starter", "create onboarding",
        "start onboarding", "i want to onboard", "i need to onboard",
    )):
        return OnboardingIntent(intent="admin_new_onboard", confidence=0.95)

    # Admin: finished filling in the briefing doc
    if is_admin and any(p in lower for p in (
        "done with briefing", "finished briefing", "filled in the doc",
        "briefing is ready", "briefing done", "doc is ready",
        "i filled it in", "i've filled it in", "filled it out",
        "i've filled it out", "done filling", "finished filling",
        "completed the doc", "completed the briefing",
    )):
        return OnboardingIntent(intent="admin_briefing_done", confidence=0.95)

    # Admin help — admin asking HOW to use the onboarding system
    if is_admin and any(p in lower for p in (
        "how can i onboard", "how do i onboard", "how to onboard",
        "brief someone", "how do i add", "how do i create",
        "set up onboarding", "briefing process", "how does briefing",
        "admin help",
    )):
        return OnboardingIntent(intent="admin_help", confidence=0.92)

    # Progress check — must be BEFORE mark_complete to avoid false positives
    # e.g. "can you check which things i have done" → progress not mark_complete
    if any(p in lower for p in _PROGRESS_PATTERNS):
        return OnboardingIntent(intent="progress", confidence=0.9)

    # Help / what can you do
    if any(p in lower for p in ("help", "what can you", "how do i", "how does this", "what do i")):
        if not any(kw in lower for kw in _TASK_KEYWORD_MAP):
            return OnboardingIntent(intent="help", confidence=0.85)

    # Task completion
    task_ids = _match_task_keywords(lower)
    matched_keywords = [kw for kw in _TASK_KEYWORD_MAP if kw in lower]
    if any(kw in lower for kw in _DONE_KEYWORDS):
        return OnboardingIntent(
            intent="mark_complete",
            task_ids=task_ids,
            task_keywords=matched_keywords,
            confidence=0.85,
            secondary_intent="next_task" if "next" in lower or "what" in lower else None,
        )

    # Bare "done" — no keyword match but still a clear completion signal
    if lower == "done" or lower == "done!":
        return OnboardingIntent(
            intent="mark_complete",
            task_ids=[],
            task_keywords=[],
            confidence=0.8,
        )

    # Task keyword + implicit completion verb (past tense patterns without explicit 'done')
    if task_ids and not any(kw in lower for kw in _SKIP_KEYWORDS):
        # Check for implicit completion signals
        _IMPLICIT_DONE = {"i have", "i've", "ive", "already", "just", "went to", "looked at", "checked out"}
        if any(sig in lower for sig in _IMPLICIT_DONE):
            return OnboardingIntent(
                intent="mark_complete",
                task_ids=task_ids,
                task_keywords=matched_keywords,
                confidence=0.8,
            )

    # Skip
    if any(kw in lower for kw in _SKIP_KEYWORDS) and not lower.startswith("not sure"):
        return OnboardingIntent(
            intent="skip_task",
            task_ids=task_ids,
            confidence=0.8,
        )

    # Who is
    if lower.startswith("who") and any(w in lower for w in ("handles", "manages", "is", "should", "do i")):
        return OnboardingIntent(intent="who_is", question_topic=text, confidence=0.85)

    # Schedule
    if any(w in lower for w in ("schedule", "this week", "upcoming", "sessions", "meetings")):
        return OnboardingIntent(intent="show_schedule", confidence=0.85)

    return None


def _match_task_keywords(text: str) -> List[str]:
    """Match task keywords in text and return task IDs."""
    matched = []
    for keyword, task_id in _TASK_KEYWORD_MAP.items():
        if keyword in text and task_id not in matched:
            matched.append(task_id)
    return matched


# ── LLM-based classification ──────────────────────────────────────────────


def classify_intent(
    text: str,
    profile: Optional[OnboardingProfile] = None,
    is_admin: bool = False,
    conversation_history: List[Dict[str, Any]] | None = None,
) -> OnboardingIntent:
    """Classify user intent, trying fast patterns first then falling back to LLM."""

    # Try fast classification first
    fast_result = _fast_classify(text, is_admin)
    if fast_result and fast_result.confidence >= 0.85:
        log.info("Fast-classified intent: %s (confidence=%.2f)", fast_result.intent, fast_result.confidence)
        return fast_result

    # Fall back to LLM
    try:
        return _llm_classify(text, profile, is_admin, conversation_history)
    except Exception as exc:
        log.warning("LLM intent classification failed: %s — using fallback", exc)
        # Fallback: if we had a low-confidence fast result, use it
        if fast_result:
            return fast_result
        return OnboardingIntent(intent="freeform", question_topic=text, confidence=0.3)


def _llm_classify(
    text: str,
    profile: Optional[OnboardingProfile],
    is_admin: bool,
    conversation_history: List[Dict[str, Any]] | None = None,
) -> OnboardingIntent:
    """Use LLM for intent classification."""
    settings = get_settings()

    # Build context
    context_parts = []
    if profile:
        from .template import get_onboarding_day
        day = get_onboarding_day(profile)
        context_parts.append(f"User: {profile.preferred_name or profile.full_name}")
        context_parts.append(f"Role: {profile.role}")
        context_parts.append(f"Onboarding day: {day}")
        context_parts.append(f"Current phase: {profile.current_phase}")
    else:
        context_parts.append("User: Unknown (may be new or admin)")

    context_parts.append(f"Is admin: {is_admin}")

    if conversation_history:
        context_parts.append("\nRecent conversation:")
        for msg in conversation_history[-3:]:
            context_parts.append(f"  User: {msg.get('request', '')[:200]}")
            context_parts.append(f"  Agent: {msg.get('response', '')[:200]}")

    context = "\n".join(context_parts)

    prompt = ChatPromptTemplate.from_messages([
        ("system", _INTENT_SYSTEM_PROMPT),
        ("human", "{user_input}"),
    ])

    parser = JsonOutputParser(pydantic_object=OnboardingIntent)

    chat_model = ChatGoogleGenerativeAI(
        model=settings.default_model,
        temperature=0.0,
        google_api_key=settings.gemini_api_key,
    )

    chain = prompt.partial(context=context) | chat_model | parser

    raw = chain.invoke({"user_input": text})
    if isinstance(raw, dict):
        return OnboardingIntent(**raw)
    return raw
