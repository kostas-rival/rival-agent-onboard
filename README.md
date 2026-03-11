# Rival Onboarding Agent

A conversational onboarding assistant for Rival Intelligence that guides new starters through their first 30 days.

## Features

### Phase 1 — Core Agent
- Conversational onboarding walkthrough via Slack DM
- Task tracking with progress dashboard
- Google Doc briefing intake and personalised doc generation
- Intent classification (fast pattern matching + LLM fallback)

### Phase 2 — Intelligence Layer
- Context-aware freeform conversations (knows the new starter's role, day number, progress)
- Internal Agent fallback for company-specific knowledge
- People/contact lookup

### Phase 3 — Proactive Engagement
- Daily morning check-in DMs with progress summary
- Session prep reminders (1:1s, meetings, reviews)
- Overdue task nudges

### Phase 4 — Automated Verifications
- Slack profile photo verification
- Display name verification
- Auto-completion notifications

### Phase 5 — Admin Dashboard
- Briefing doc processing (`read briefing <url>`)
- Profile management (activate, pause, complete)
- Analytics and daily admin reports
- Multi-onboarding tracking

## Architecture

```
Slack → rival-slack-bot → rival-agent-runtime → rival-agent-onboard
                                                    ├── /v1/run (agent invocation)
                                                    ├── /v1/daily-checkins (Cloud Scheduler)
                                                    ├── /v1/daily-admin-report (Cloud Scheduler)
                                                    ├── /v1/session-prep (Cloud Scheduler)
                                                    ├── /v1/run-verifications (Cloud Scheduler)
                                                    └── /health
```

## Configuration

Environment variables (prefixed with `ONBOARDING_AGENT_`):

| Variable | Description |
|---|---|
| `PROJECT_ID` | GCP project ID |
| `FIRESTORE_DATABASE_ID` | Firestore database (default: `agentic-rival`) |
| `GEMINI_API_KEY` | Gemini API key |
| `SLACK_BOT_TOKEN` | Slack bot token for DMs |
| `INTERNAL_AGENT_URL` | URL of the internal agent for knowledge fallback |
| `DRIVE_FOLDER_ID` | Google Drive folder for generated docs |
| `TEMPLATE_DOC_ID` | Google Doc template ID |
| `ADMIN_SLACK_IDS` | Comma-separated admin Slack user IDs |
| `DAILY_REPORT_RECIPIENTS` | Comma-separated Slack IDs for daily reports |
| `ACTIVE_DURATION_DAYS` | Onboarding duration (default: 30) |

## Local Development

```bash
# Install dependencies
pip install -e ".[dev]"

# Run locally
uvicorn app.main:app --reload --port 8084

# Health check
curl http://localhost:8084/health
```

## Deployment

Deployed automatically via Cloud Build on push to `main`:

```bash
gcloud builds submit --config=cloudbuild.yaml
```

## Admin Commands

| Command | Description |
|---|---|
| `read briefing <url>` | Process a briefing Google Doc |
| `list onboardings` | Show all profiles |
| `activate <name>` | Activate an onboarding |
| `pause <name>` | Pause an onboarding |
| `complete <name>` | Mark onboarding complete |
| `analytics` | Show aggregate stats |
| `daily report` | Generate admin digest |

## User Commands

| Command | Description |
|---|---|
| `hi` / `hello` | Greeting / welcome |
| `next` | Next task |
| `done` / `completed` | Mark current task complete |
| `skip` | Skip current task |
| `progress` | Progress dashboard |
| `schedule` | Session schedule |
| `who handles X` | People lookup |
| `contacts` | All support contacts |
