"""Google Drive integration for reading briefing docs and generating onboarding docs."""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from google.oauth2 import service_account as sa_module
from googleapiclient.discovery import build as build_service

from .config import get_settings
from .models import BriefingData, OnboardingSession, TeamIntroduction

log = logging.getLogger(__name__)

_docs_service = None
_drive_service = None

_SCOPES = [
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive",
]


def _get_drive_credentials():
    """Load the AI-knowledge service account credentials from Secret Manager.

    Falls back to application-default credentials if unavailable.
    """
    try:
        from google.cloud import secretmanager

        client = secretmanager.SecretManagerServiceClient()
        settings = get_settings()
        name = f"projects/{settings.project_id}/secrets/DRIVE_SA_JSON/versions/latest"
        response = client.access_secret_version(request={"name": name})
        sa_info = json.loads(response.payload.data.decode("UTF-8"))
        return sa_module.Credentials.from_service_account_info(sa_info, scopes=_SCOPES)
    except Exception:
        log.warning("Could not load DRIVE_SA_JSON secret — falling back to default credentials")
        from google.auth import default as google_auth_default
        from google.auth.transport.requests import Request as GoogleAuthRequest

        credentials, _ = google_auth_default(scopes=_SCOPES)
        credentials.refresh(GoogleAuthRequest())
        return credentials


def _get_docs_service():
    """Get or create a Google Docs API service."""
    global _docs_service
    if _docs_service is None:
        credentials = _get_drive_credentials()
        _docs_service = build_service("docs", "v1", credentials=credentials)
    return _docs_service


def _get_drive_service():
    """Get or create a Google Drive API service."""
    global _drive_service
    if _drive_service is None:
        credentials = _get_drive_credentials()
        _drive_service = build_service("drive", "v3", credentials=credentials)
    return _drive_service


def extract_doc_id(url_or_id: str) -> str:
    """Extract a Google Doc ID from a URL or return as-is if already an ID."""
    # Match /d/{id}/ pattern in Google Docs URLs
    match = re.search(r"/d/([a-zA-Z0-9_-]+)", url_or_id)
    if match:
        return match.group(1)
    # Already an ID
    if re.match(r"^[a-zA-Z0-9_-]{20,}$", url_or_id.strip()):
        return url_or_id.strip()
    return url_or_id.strip()


def read_google_doc(doc_id: str) -> str:
    """Read the full text content of a Google Doc."""
    service = _get_docs_service()
    doc = service.documents().get(documentId=doc_id).execute()

    text_parts = []
    for element in doc.get("body", {}).get("content", []):
        if "paragraph" in element:
            for run in element["paragraph"].get("elements", []):
                text_content = run.get("textRun", {}).get("content", "")
                text_parts.append(text_content)
        elif "table" in element:
            for row in element["table"].get("tableRows", []):
                row_texts = []
                for cell in row.get("tableCells", []):
                    cell_text = ""
                    for p in cell.get("content", []):
                        if "paragraph" in p:
                            for run in p["paragraph"].get("elements", []):
                                cell_text += run.get("textRun", {}).get("content", "")
                    row_texts.append(cell_text.strip())
                text_parts.append("\t".join(row_texts))

    return "\n".join(text_parts)


def parse_briefing_doc(raw_text: str) -> BriefingData:
    """Parse structured sections from briefing document text.

    The document follows a known template with sections like:
    NEW STARTER DETAILS, TEAM INTRODUCTIONS, ONBOARDING SESSIONS, etc.
    """
    briefing = BriefingData(raw_text=raw_text)
    lines = raw_text.split("\n")

    current_section = ""
    section_lines: Dict[str, List[str]] = {}

    for line in lines:
        stripped = line.strip()
        upper = stripped.upper()

        # Detect section headers
        if "NEW STARTER DETAILS" in upper or "STARTER DETAILS" in upper:
            current_section = "details"
            section_lines[current_section] = []
        elif "TEAM INTRODUCTIONS" in upper or "TEAM INTRO" in upper:
            current_section = "introductions"
            section_lines[current_section] = []
        elif "ONBOARDING SESSIONS" in upper:
            current_section = "sessions"
            section_lines[current_section] = []
        elif "REVIEW SCHEDULE" in upper:
            current_section = "reviews"
            section_lines[current_section] = []
        elif "ROLE-SPECIFIC" in upper or "ROLE SPECIFIC" in upper:
            current_section = "role_notes"
            section_lines[current_section] = []
        elif "TOOL ACCESS" in upper:
            current_section = "tool_access"
            section_lines[current_section] = []
        elif current_section:
            section_lines.setdefault(current_section, []).append(stripped)

    # Parse details section
    _parse_details(section_lines.get("details", []), briefing)
    _parse_introductions(section_lines.get("introductions", []), briefing)
    _parse_sessions(section_lines.get("sessions", []), briefing)
    _parse_reviews(section_lines.get("reviews", []), briefing)
    _parse_role_notes(section_lines.get("role_notes", []), briefing)
    _parse_tool_access(section_lines.get("tool_access", []), briefing)

    return briefing


def _parse_details(lines: List[str], briefing: BriefingData) -> None:
    """Parse the NEW STARTER DETAILS section."""
    for line in lines:
        if not line or line.startswith("─"):
            continue
        key_val = _extract_key_value(line)
        if not key_val:
            continue
        key, val = key_val
        key_lower = key.lower()

        if "name" == key_lower:
            briefing.full_name = val
        elif "preferred" in key_lower:
            briefing.preferred_name = val
        elif "start" in key_lower and "date" in key_lower:
            briefing.start_date = _parse_date(val)
        elif "role" in key_lower:
            briefing.role = val
        elif "team" in key_lower:
            briefing.team = val
        elif "location" in key_lower:
            briefing.office_location = val
        elif "manager" in key_lower and "slack" in key_lower:
            briefing.line_manager_slack = val.lstrip("@")
        elif "manager" in key_lower:
            briefing.line_manager = val


def _parse_introductions(lines: List[str], briefing: BriefingData) -> None:
    """Parse the TEAM INTRODUCTIONS section."""
    current_region = ""
    for line in lines:
        if not line or line.startswith("─"):
            continue
        lower = line.lower()

        if "local" in lower or "uk" in lower and "1-1" in lower:
            current_region = "local"
        elif "regional" in lower or ("us" in lower or "sa" in lower) and "1-1" in lower:
            current_region = "regional"
        elif "team lunch" in lower:
            briefing.team_lunch = "yes" in lower
        elif line.startswith("-") or line.startswith("•"):
            names_text = line.lstrip("-•").strip()
            # Handle "US: Lisa, Molly" or "SA: Adam, Clement, Byron"
            region_match = re.match(r"(US|SA|UK)\s*[:—-]\s*(.*)", names_text, re.IGNORECASE)
            if region_match:
                region_label = region_match.group(1).upper()
                names = [n.strip() for n in region_match.group(2).split(",") if n.strip()]
                for name in names:
                    # Extract duration if present
                    dur_match = re.search(r"\((\d+)(?:/\d+)?\s*min", name)
                    duration = int(dur_match.group(1)) if dur_match else 15
                    clean_name = re.sub(r"\s*\(.*?\)", "", name).strip()
                    briefing.regional_introductions.append({
                        "name": clean_name,
                        "region": region_label,
                        "duration_minutes": duration,
                    })
            else:
                names = [n.strip() for n in names_text.split(",") if n.strip()]
                for name in names:
                    briefing.local_introductions.append({
                        "name": name,
                        "region": current_region or "local",
                        "duration_minutes": 15,
                    })


def _parse_sessions(lines: List[str], briefing: BriefingData) -> None:
    """Parse the ONBOARDING SESSIONS section."""
    for line in lines:
        if not line or line.startswith("─") or line.startswith("("):
            continue
        if line.startswith("-") or line.startswith("•"):
            session_text = line.lstrip("-•").strip()
            # Pattern: "Title — Presenter (date, time)" or "Title — Presenter"
            match = re.match(
                r"(.+?)\s*[—–-]\s*(\w[\w\s]*?)(?:\s*\((.+?)\))?\s*$",
                session_text,
            )
            if match:
                title = match.group(1).strip()
                presenter = match.group(2).strip()
                date_str = match.group(3)
                scheduled = _parse_datetime(date_str) if date_str else None
                briefing.sessions.append({
                    "title": title,
                    "presenter": presenter,
                    "scheduled_at": scheduled.isoformat() if scheduled else None,
                })


def _parse_reviews(lines: List[str], briefing: BriefingData) -> None:
    """Parse the REVIEW SCHEDULE section."""
    for line in lines:
        if not line:
            continue
        key_val = _extract_key_value(line)
        if not key_val:
            continue
        key, val = key_val
        if "30" in key.lower():
            briefing.review_30_day = _parse_date(val)
        elif "90" in key.lower():
            briefing.review_90_day = _parse_date(val)


def _parse_role_notes(lines: List[str], briefing: BriefingData) -> None:
    """Parse role-specific notes."""
    for line in lines:
        if not line or line.startswith("─") or line.startswith("("):
            continue
        if line.startswith("-") or line.startswith("•"):
            note = line.lstrip("-•").strip()
            if note:
                briefing.role_specific_notes.append(note)


def _parse_tool_access(lines: List[str], briefing: BriefingData) -> None:
    """Parse tool access notes."""
    for line in lines:
        if not line or line.startswith("─"):
            continue
        if line.startswith("-") or line.startswith("•"):
            text = line.lstrip("-•").strip()
            # Pattern: "Tool invite: status" or "Tool access: status"
            match = re.match(r"(.+?)\s*(?:invite|access)?\s*[:—-]\s*(.+)", text, re.IGNORECASE)
            if match:
                tool = match.group(1).strip().lower().replace(" ", "_")
                status = match.group(2).strip()
                briefing.tool_access_notes[tool] = status


def _extract_key_value(line: str) -> Optional[tuple]:
    """Extract key:value from a line like 'Name:  Quentin Durverge'."""
    match = re.match(r"([^:]+):\s*(.+)", line)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    # Tab-separated (from table cells)
    parts = line.split("\t")
    if len(parts) >= 2 and parts[0].strip() and parts[1].strip():
        return parts[0].strip(), parts[1].strip()
    return None


def _parse_date(text: str) -> Optional[date]:
    """Parse a date from various formats."""
    text = text.strip()
    for fmt in ("%d %B %Y", "%d %b %Y", "%Y-%m-%d", "%d/%m/%Y", "%B %d, %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    # Try to extract a date pattern
    match = re.search(r"(\d{1,2})\s+(\w+)\s+(\d{4})", text)
    if match:
        try:
            return datetime.strptime(f"{match.group(1)} {match.group(2)} {match.group(3)}", "%d %B %Y").date()
        except ValueError:
            pass
    return None


def _parse_datetime(text: str) -> Optional[datetime]:
    """Parse a datetime from various formats."""
    text = text.strip()
    for fmt in ("%d %B, %H:%M", "%d %B %Y, %H:%M", "%d %b, %H:%M", "%d %b %Y, %H:%M"):
        try:
            dt = datetime.strptime(text, fmt)
            if dt.year == 1900:
                dt = dt.replace(year=date.today().year)
            return dt
        except ValueError:
            continue
    return None


# ── Doc Generation ────────────────────────────────────────────────────────


def generate_onboarding_doc(briefing: BriefingData) -> Dict[str, str]:
    """Generate a personalised onboarding Google Doc from the briefing data.

    Creates a copy of the template doc and populates it with the new starter's details.
    Returns {"doc_id": ..., "doc_url": ...}.
    """
    settings = get_settings()
    drive = _get_drive_service()
    docs = _get_docs_service()

    # Copy the template document
    title = f"Onboarding — {briefing.full_name}"
    copy_result = drive.files().copy(
        fileId=settings.template_doc_id,
        body={
            "name": title,
            "parents": [settings.drive_folder_id],
        },
    ).execute()
    new_doc_id = copy_result["id"]
    log.info("Created onboarding doc copy: %s", new_doc_id)

    # Build replacement requests
    replacements = {
        "{{NAME}}": briefing.full_name or "",
        "{{PREFERRED_NAME}}": briefing.preferred_name or briefing.full_name.split()[0] if briefing.full_name else "",
        "{{START_DATE}}": briefing.start_date.strftime("%d %B %Y") if briefing.start_date else "",
        "{{ROLE}}": briefing.role or "",
        "{{TEAM}}": briefing.team or "",
        "{{LOCATION}}": briefing.office_location or "",
        "{{MANAGER}}": briefing.line_manager or "",
        "{{MANAGER_SLACK}}": f"@{briefing.line_manager_slack}" if briefing.line_manager_slack else "",
    }

    # Build session list text
    session_lines = []
    for s in briefing.sessions:
        line = f"- {s.get('title', '')} — {s.get('presenter', '')}"
        if s.get("scheduled_at"):
            line += f" ({s['scheduled_at']})"
        session_lines.append(line)
    replacements["{{SESSIONS_LIST}}"] = "\n".join(session_lines) if session_lines else "(To be scheduled)"

    # Build introductions text
    intro_lines = []
    for i in briefing.local_introductions:
        intro_lines.append(f"- {i['name']} (local, {i.get('duration_minutes', 15)} min)")
    for i in briefing.regional_introductions:
        intro_lines.append(f"- {i['region']}: {i['name']} ({i.get('duration_minutes', 15)} min)")
    replacements["{{INTRODUCTIONS_LIST}}"] = "\n".join(intro_lines) if intro_lines else "(To be scheduled)"

    # Reviews
    replacements["{{REVIEW_30_DAY}}"] = (
        briefing.review_30_day.strftime("%d %B %Y") if briefing.review_30_day else "(TBC)"
    )
    replacements["{{REVIEW_90_DAY}}"] = (
        briefing.review_90_day.strftime("%d %B %Y") if briefing.review_90_day else "(TBC)"
    )

    # Role-specific notes
    replacements["{{ROLE_SPECIFIC_NOTES}}"] = (
        "\n".join(f"- {n}" for n in briefing.role_specific_notes)
        if briefing.role_specific_notes
        else "(None)"
    )

    # Tool access
    tool_lines = []
    for tool, status in briefing.tool_access_notes.items():
        tool_lines.append(f"- {tool.replace('_', ' ').title()}: {status}")
    replacements["{{TOOL_ACCESS_NOTES}}"] = "\n".join(tool_lines) if tool_lines else "(All standard)"

    # Apply replacements via Docs API
    requests_list = []
    for placeholder, value in replacements.items():
        requests_list.append({
            "replaceAllText": {
                "containsText": {
                    "text": placeholder,
                    "matchCase": True,
                },
                "replaceText": value,
            }
        })

    if requests_list:
        docs.documents().batchUpdate(
            documentId=new_doc_id,
            body={"requests": requests_list},
        ).execute()
        log.info("Applied %d replacements to doc %s", len(requests_list), new_doc_id)

    doc_url = f"https://docs.google.com/document/d/{new_doc_id}/edit"
    return {"doc_id": new_doc_id, "doc_url": doc_url}


def read_briefing_from_url(url_or_id: str) -> BriefingData:
    """Read and parse a briefing Google Doc from URL or ID."""
    doc_id = extract_doc_id(url_or_id)
    raw_text = read_google_doc(doc_id)
    briefing = parse_briefing_doc(raw_text)
    return briefing
