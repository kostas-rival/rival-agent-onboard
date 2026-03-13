# rival-agent-onboard

Conversational onboarding agent for the Rival Intelligence multi-agent Slack ecosystem. Guides new starters through their first 90 days with a structured, phase-based journey — and gives admins (People Ops / managers) full control over the process.

## Table of Contents

- [How It Works](#how-it-works)
- [Architecture](#architecture)
- [The Onboarding Journey](#the-onboarding-journey)
- [Admin Flow](#admin-flow)
- [New Starter Flow](#new-starter-flow)
- [Intent Classification](#intent-classification)
- [Task Completion & Verification](#task-completion--verification)
- [Link Click Tracking](#link-click-tracking)
- [Scheduled Jobs](#scheduled-jobs)
- [API Endpoints](#api-endpoints)
- [Data Model](#data-model)
- [Configuration](#configuration)
- [Deployment](#deployment)
- [Local Development](#local-development)
- [Project Structure](#project-structure)

---

## How It Works

The agent sits behind the `rival-agent-runtime` router. Users interact with it in Slack by prefixing messages with `[onboarding]`. The runtime dispatches the message to this agent's `/v1/run` endpoint as a standard `AgentInvocationRequest`.

```
User in Slack                    Runtime Router              This Agent
─────────────                    ──────────────              ──────────
[onboarding] hello        →      routes by prefix     →     POST /v1/run
                                                             ├── classify intent
                                                             ├── lookup/create profile
                                                             ├── route to handler
                                                             └── return Slack blocks
                          ←      forwards response    ←     AgentInvocationResponse
```

### The two audiences

| Audience | Trigger | What they can do |
|----------|---------|-----------------|
| **Admins** (People Ops) | Any admin intent (e.g. paste a Google Doc URL, `list active`, `activate <name>`) | Read briefing docs, create profiles, activate/pause/complete onboardees, view analytics and reports |
| **New starters** | `hello`, `let's go`, `next task`, `done with X` | Walk through their personalised onboarding journey, mark tasks complete, ask questions, view progress |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        Slack                                │
│  User: [onboarding] <message>                               │
└───────────────┬─────────────────────────────────────────────┘
                │
                ▼
┌───────────────────────────┐
│   rival-agent-runtime     │  Routes [onboarding] prefix
│   (central router)        │  to this agent's /v1/run
└───────────────┬───────────┘
                │
                ▼
┌───────────────────────────────────────────────────────────┐
│              rival-agent-onboard                          │
│                                                           │
│  ┌──────────┐  ┌──────────────┐  ┌────────────────────┐  │
│  │ Intent   │  │ Agent Router │  │ Handlers            │  │
│  │ Classify │→ │ (agent.py)   │→ │ ├── admin.py        │  │
│  │          │  │              │  │ ├── tasks.py         │  │
│  └──────────┘  └──────────────┘  │ ├── greeting.py     │  │
│                                  │ ├── conversation.py  │  │
│  ┌──────────┐  ┌──────────────┐  │ ├── progress.py     │  │
│  │ Template │  │ Renderer     │  │ ├── contacts.py     │  │
│  │ (YAML)   │  │ (Slack fmt)  │  │ └── schedule.py     │  │
│  └──────────┘  └──────────────┘  └────────────────────┘  │
│                                                           │
│  ┌──────────┐  ┌──────────────┐  ┌────────────────────┐  │
│  │ Firestore│  │ Google Drive │  │ Verifier           │  │
│  │ (state)  │  │ (briefings)  │  │ (auto-complete)    │  │
│  └──────────┘  └──────────────┘  └────────────────────┘  │
└───────────────────────────────────────────────────────────┘
                │                           │
                ▼                           ▼
     Cloud Firestore                Google Docs/Drive
     (agentic-rival DB)             (briefing docs)
```

### Key dependencies

| Service | Purpose |
|---------|---------|
| **Cloud Firestore** | All profile state, task progress, sessions, interactions, link clicks |
| **Google Docs API** | Reading admin-prepared briefing documents |
| **Google Drive API** | Generating personalised onboarding documents (copy from template) |
| **Secret Manager** | `GEMINI_API_KEY`, `SLACK_BOT_TOKEN`, `DRIVE_SA_JSON` |
| **Gemini (LLM)** | Intent classification fallback, conversational Q&A |
| **rival-agent-internal** | Forwarded company knowledge questions the onboarding agent can't answer |

---

## The Onboarding Journey

The journey is defined in `templates/onboarding_v2.yaml` — a structured YAML file with **4 phases, 10 groups, and ~25 tasks** plus dynamic groups that expand based on each person's briefing.

### Phase overview

| Phase | Unlocks | Groups | Description |
|-------|---------|--------|-------------|
| **Day 1 — Getting Set Up** | Day 0 | 🛠️ Tool Setup, 🤖 Meet RI, 📧 Email | Get all accounts and tools configured |
| **Week 1 — Finding Your Feet** | Day 1 | 🤝 Manager & Team Meetings, 👋 Team Introductions | Meet your manager, attend standup, get introduced to the team |
| **Month 1 — Going Deeper** | Day 7 | 📚 Onboarding Sessions, 📖 Reading & Listening, ✨ Fun Stuff | Deep dives, handbooks, culture activities |
| **Reviews & Goal Setting** | Day 25 | 🔭 30-Day Check-in, 🎯 90-Day Check-in | Formal reviews and goal setting |

### Example tasks

- Set up Google Drive access
- Set up your Slack profile (auto-verified via Slack API)
- Complete your CharlieHR profile
- Set up 1Password
- Log into Productive and explore time tracking
- Visit #rival-intelligence and ask your first question
- Read the Rival Handbook
- Create your Get To Know You card
- 30-day check-in with your manager

### Dynamic groups

Two groups expand based on the briefing document:

- **👋 Team Introductions** — one task per person listed in the briefing's "Team Introductions" section
- **📚 Onboarding Sessions** — one task per session scheduled in the briefing

### Task links

Every task can have associated links (e.g. a Google Drive folder URL, a CharlieHR portal link, a Slack deep link). These are rendered as tracked redirect URLs so the system knows when a new starter has clicked them.

---

## Admin Flow

Admins are identified by their Slack user ID, configured in the `ONBOARDING_AGENT_ADMIN_SLACK_IDS` environment variable.

### Step-by-step admin workflow

```
1. Admin prepares a Google Doc briefing
   (start date, role, department, team intros, sessions, tool access notes)

2. Admin pastes the Google Doc URL in Slack:
   [onboarding] https://docs.google.com/document/d/1abc.../edit

3. Agent reads & parses the doc via Google Docs API
   → Extracts: name, role, department, start date, team intros, sessions, reviews

4. Agent creates a Firestore profile (status: pending)
   → Optionally generates a personalised onboarding doc in Google Drive

5. Admin activates the profile:
   [onboarding] activate <name>
   → Status changes to "active", default sessions are created

6. On the new starter's first day, they message:
   [onboarding] hello
   → Agent recognises them and starts the guided journey
```

### Admin commands

| Command | Intent | What it does |
|---------|--------|-------------|
| Paste a Google Doc URL | `admin_read_briefing` | Reads briefing, creates profile |
| `activate <name>` | `admin_activate` | Sets profile to active, creates sessions |
| `pause <name>` | `admin_pause` | Pauses an onboarding (e.g. if someone is away) |
| `complete <name>` | `admin_complete` | Marks onboarding as graduated |
| `list active` | `admin_list` | Shows all profiles with progress bars |
| `analytics` | `admin_analytics` | Aggregate statistics across all onboardees |
| `report` | `admin_report` | Generates a detailed admin digest report |

---

## New Starter Flow

Once activated, a new starter interacts conversationally:

```
Day 1:
  User: "hello"
  Agent: Welcome message with journey overview (4 phases, what to expect)

  User: "let's go" / "next task"
  Agent: Shows first task group (Tool Setup) as a checklist with tracked links

  User: "done with slack setup"
  Agent: ✅ Marks task complete, shows progress (1/25 tasks)

  User: "I've set up 1password and productive"
  Agent: ✅ Marks both tasks complete, shows updated progress

  User: "what should I do next?"
  Agent: Shows the next incomplete task or group

  User: "who can help me with finance?"
  Agent: Shows relevant support contacts

  User: "show my progress"
  Agent: Full dashboard with per-phase progress bars, overall %, overdue alerts

Day 7+:
  Agent (daily check-in): Morning DM with progress update and today's focus area
  Agent (verification): Auto-completes Slack profile task if photo + name detected
```

### Supported new starter intents

| Intent | Example messages |
|--------|-----------------|
| `greeting` | "hello", "hi", "hey" |
| `get_started` | "let's go", "start", "begin" |
| `next_task` | "what's next", "next task" |
| `mark_complete` | "done with slack setup", "finished 1password" |
| `skip_task` | "skip the calendar task" |
| `show_progress` | "how am I doing", "my progress" |
| `show_schedule` | "what sessions do I have" |
| `session_prep` | "prep for my 1:1 tomorrow" |
| `who_is` | "who can help with finance" |
| `ask_question` | "what's the dress code?", any general question |

---

## Intent Classification

Intent classification uses a **two-tier system**:

### Tier 1: Fast pattern matching

Regex and keyword matching for common patterns — zero-latency, no LLM call:

- Greeting words → `greeting`
- Google Doc URLs → `admin_read_briefing`
- `list active` → `admin_list`
- `activate <name>` → `admin_activate`
- `done with <keyword>` → `mark_complete` (with task resolution via keyword map)
- `skip <keyword>` → `skip_task`
- Progress-related words → `show_progress`

A keyword-to-task-ID map resolves natural language to specific tasks:

```
"slack"       → slack_setup
"1password"   → onepassword
"productive"  → productive_setup
"charlie"     → charliehr
"handbook"    → read_handbook
"gtky"        → gtky_card
...
```

### Tier 2: LLM fallback (Gemini)

If pattern matching doesn't produce a high-confidence result, the message is sent to Gemini with:

- The full list of 20 intents with descriptions
- The user's profile context (current phase, recent tasks)
- Admin status flag
- Recent conversation history

The LLM returns a structured `OnboardingIntent` with intent, confidence, resolved task IDs/keywords, and any entity (e.g. a person's name for `who_is`).

---

## Task Completion & Verification

Tasks can be completed in three ways:

### 1. Self-reporting (conversational)

The user says "done with X" → agent resolves the task → marks it complete in Firestore.

**Task resolution chain:**
1. Direct task ID match (from intent classification)
2. Keyword map lookup (e.g. "slack" → `slack_setup`)
3. If in a group with one remaining task, assume that's the one

### 2. Automated verification (Slack API)

The `/v1/run-verifications` endpoint (triggered by Cloud Scheduler) checks:

| Check | What it verifies |
|-------|-----------------|
| `slack_profile_photo` | User has a non-default avatar |
| `slack_display_name` | Display name or real name is set |
| `slack_status` | Custom status text is set |
| `slack_profile_complete` | Photo + name both set → auto-completes `slack_setup` |

When a task is auto-verified, the user gets a DM notification.

### 3. Link-click auto-completion

When a task has associated links and the `auto_complete` flag is set in the template, the system auto-marks the task as complete once all links have been clicked (tracked via the redirect endpoint).

---

## Link Click Tracking

Every link rendered in task checklists goes through a tracking redirect:

```
Original URL:  https://drive.google.com/drive/folders/0ABB7ZgQp2H9SUk9PVA
Tracked URL:   https://rival-agent-onboard-..../v1/track/{user_id}/{task_id}/0

User clicks → GET /v1/track/{user_id}/{task_id}/0
           → Records click in Firestore (onboarding_profiles/{user_id}/link_clicks)
           → 302 redirect to the original URL
```

This enables:
- **Progress analytics** — which links are being visited, which are ignored
- **Auto-completion** — tasks with `auto_complete: true` are marked done when all links are clicked
- **Engagement tracking** — admins can see link click data in analytics

---

## Scheduled Jobs

Four Cloud Scheduler endpoints run automated processes:

| Endpoint | Schedule | Purpose |
|----------|----------|---------|
| `POST /v1/daily-checkins` | Every morning | Sends personalised DMs to active onboardees with progress bars, overdue tasks, and today's focus |
| `POST /v1/daily-admin-report` | Every morning | Sends admin digest to configured Slack channel with per-person progress |
| `POST /v1/session-prep` | Every afternoon | Sends prep reminders for sessions happening tomorrow |
| `POST /v1/run-verifications` | Periodic | Runs Slack API checks and link-click verification, auto-completes tasks |

### Daily check-in message includes

- Day number ("Day 3 of your onboarding!")
- Progress bar and percentage
- Overdue tasks (tasks from past phases still incomplete)
- Today's focus (next incomplete group)
- Day 30 graduation message when applicable

---

## API Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/v1/run` | Via runtime | Core conversational endpoint — receives `AgentInvocationRequest`, returns `AgentInvocationResponse` |
| `GET` | `/v1/track/{user_id}/{task_id}/{link_index}` | None | Link click tracking — records click, 302 redirects to actual URL |
| `POST` | `/v1/daily-checkins` | Cloud Scheduler | Sends morning check-in DMs |
| `POST` | `/v1/daily-admin-report` | Cloud Scheduler | Sends admin digest report |
| `POST` | `/v1/session-prep` | Cloud Scheduler | Sends session prep reminders |
| `POST` | `/v1/run-verifications` | Cloud Scheduler | Runs automated task verifications |
| `GET` | `/health` | None | Health check |

### Request/Response format

```json
// POST /v1/run — Request (AgentInvocationRequest)
{
  "text": "done with slack setup",
  "user_id": "U09B8E7F7GX",
  "channel_id": "C_CHANNEL_ID",
  "provider": "gemini",
  "model": "gemini-2.5-flash"
}

// Response (AgentInvocationResponse)
{
  "text": "✅ *Set up your Slack profile* — marked as complete!\n\n📊 Progress: 1/25 tasks complete (4%)",
  "agent_id": "onboarding"
}
```

---

## Data Model

### Firestore structure

```
agentic-rival (database)
└── onboarding_profiles (collection)
    └── {user_id} (document)
        ├── name, role, department, status, start_date, ...
        ├── template_version, line_manager, ...
        │
        ├── progress (subcollection)
        │   └── {task_id} → status, completed_at, verified_by, ...
        │
        ├── sessions (subcollection)
        │   └── {session_id} → title, type, day_number, completed, ...
        │
        ├── introductions (subcollection)
        │   └── {intro_id} → person_name, role, completed, ...
        │
        ├── interactions (subcollection)
        │   └── {auto_id} → action, details, timestamp
        │
        └── link_clicks (subcollection)
            └── {auto_id} → task_id, link_index, url, clicked_at
```

### Profile statuses

| Status | Meaning |
|--------|---------|
| `pending` | Created from briefing, not yet activated |
| `active` | Onboarding in progress |
| `paused` | Temporarily paused (e.g. leave) |
| `completed` | Graduated — onboarding finished |

### Task progress statuses

| Status | Meaning |
|--------|---------|
| `not_started` | Default |
| `in_progress` | Started but not finished |
| `completed` | Done (self-reported, auto-verified, or link-click verified) |
| `skipped` | Explicitly skipped by user |
| `verified` | Auto-verified via API check |

---

## Configuration

All settings use the `ONBOARDING_AGENT_` env var prefix (via Pydantic BaseSettings).

| Variable | Description |
|----------|-------------|
| `ONBOARDING_AGENT_GCP_PROJECT` | GCP project ID (`rival-agents`) |
| `ONBOARDING_AGENT_FIRESTORE_DATABASE` | Firestore database name (`agentic-rival`) |
| `ONBOARDING_AGENT_GEMINI_API_KEY` | Gemini API key (from Secret Manager) |
| `ONBOARDING_AGENT_SLACK_BOT_TOKEN` | Slack bot token (from Secret Manager) |
| `ONBOARDING_AGENT_DRIVE_SA_JSON` | Drive service account JSON key (from Secret Manager) |
| `ONBOARDING_AGENT_INTERNAL_AGENT_URL` | URL of `rival-agent-internal` for forwarded questions |
| `ONBOARDING_AGENT_ADMIN_SLACK_IDS` | Comma-separated Slack user IDs for admin access |
| `ONBOARDING_AGENT_DRIVE_FOLDER_ID` | Google Drive folder for generated onboarding docs |
| `ONBOARDING_AGENT_TEMPLATE_DOC_ID` | Google Doc template ID |
| `ONBOARDING_AGENT_REPORT_CHANNEL` | Slack channel for admin reports |
| `ONBOARDING_AGENT_SERVICE_URL` | Public URL of this service (for link tracking redirects) |
| `ONBOARDING_AGENT_ONBOARDING_DURATION_DAYS` | Journey duration (default: 30) |
| `ONBOARDING_AGENT_CHECKIN_HOUR` | Hour for daily check-ins (default: 8) |

---

## Deployment

### Infrastructure

- **Platform:** Google Cloud Run
- **Region:** `europe-west1`
- **Service account:** `agentic-firestore@rival-agents.iam.gserviceaccount.com`
- **Resources:** 1 CPU, 1Gi memory, 0–3 instances, 120s request timeout
- **Container:** Python 3.12-slim, uvicorn on port 8080

### Build & deploy

```bash
# From the repo root
gcloud builds submit --config=cloudbuild.yaml --project=rival-agents .
```

This runs Cloud Build which:
1. Builds the Docker image
2. Pushes to `gcr.io/rival-agents/rival-agent-onboard`
3. Deploys to Cloud Run with all env vars and secrets

### Secrets (from Secret Manager)

| Secret | Mounted as |
|--------|-----------|
| `GEMINI_API_KEY` | `ONBOARDING_AGENT_GEMINI_API_KEY` |
| `SLACK_BOT_TOKEN` | `ONBOARDING_AGENT_SLACK_BOT_TOKEN` |
| `DRIVE_SA_JSON` | Loaded at runtime via Secret Manager API |

### Runtime routing

For the runtime to route `[onboarding]` messages to this agent, an entry must exist in the runtime's `agents.yml`:

```yaml
onboarding:
  url: https://rival-agent-onboard-730268527569.europe-west1.run.app
  description: Employee onboarding assistant
```

---

## Local Development

```bash
# Install dependencies
pip install -e ".[dev]"

# Run locally
uvicorn app.main:app --reload --port 8084

# Health check
curl http://localhost:8084/health
```

---

## Project Structure

```
rival-agent-onboard/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI app, all endpoints
│   ├── agent.py             # Core OnboardingAgent — intent routing
│   ├── intent.py            # Two-tier intent classification
│   ├── models.py            # Pydantic models (profiles, tasks, sessions)
│   ├── state.py             # Firestore CRUD operations
│   ├── config.py            # Pydantic settings / env vars
│   ├── template.py          # YAML template loader + phase logic
│   ├── briefing.py          # Google Docs parsing + doc generation
│   ├── renderer.py          # Slack message formatting
│   ├── verifier.py          # Automated task verification
│   ├── scheduler.py         # Daily check-ins + session prep
│   └── handlers/
│       ├── __init__.py
│       ├── admin.py         # Admin commands (briefing, activate, list, etc.)
│       ├── tasks.py         # Task completion + skip
│       ├── greeting.py      # Welcome messages
│       ├── conversation.py  # LLM Q&A with fallback to internal agent
│       ├── progress.py      # Progress dashboard
│       ├── contacts.py      # Support contact lookup
│       └── schedule.py      # Session schedule + prep
├── templates/
│   └── onboarding_v2.yaml   # The onboarding journey definition
├── pyproject.toml            # Dependencies
├── Dockerfile                # Container build
├── cloudbuild.yaml           # Cloud Build + Cloud Run deploy
└── README.md                 # This file
```
