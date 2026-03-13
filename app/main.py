"""FastAPI application — endpoints for the onboarding agent."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from rival_agent_shared import AgentInvocationRequest, AgentInvocationResponse

from .agent import OnboardingAgent
from .config import get_settings
from .scheduler import DailyCheckinGenerator, send_daily_admin_reports

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

agent: OnboardingAgent | None = None
checkin_generator: DailyCheckinGenerator | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    global agent, checkin_generator  # noqa: PLW0603
    log.info("Starting onboarding agent…")
    agent = OnboardingAgent()
    checkin_generator = DailyCheckinGenerator()
    yield
    log.info("Shutting down onboarding agent.")


app = FastAPI(
    title="Rival Onboarding Agent",
    version="1.0.0",
    lifespan=lifespan,
)


# ── Core agent endpoint ────────────────────────────────────────────────────


@app.post("/v1/run", response_model=AgentInvocationResponse)
async def run_agent(request: AgentInvocationRequest):
    """Handle an incoming agent invocation from the runtime."""
    if not agent:
        raise HTTPException(status_code=503, detail="Agent not initialized")

    try:
        response = await agent.run(request)
        return response
    except Exception:
        log.exception("Agent run failed")
        return AgentInvocationResponse(
            response_text=(
                "I'm sorry, something went wrong with the onboarding agent. "
                "Please try again or contact your line manager."
            ),
            steps=["Error"],
            citations=[],
            provider=request.provider,
            model=request.model,
            agent_id="onboarding",
        )


# ── Scheduled endpoints (Cloud Scheduler) ──────────────────────────────────


@app.post("/v1/daily-checkins")
async def daily_checkins(request: Request):
    """Triggered by Cloud Scheduler each morning to send daily check-in DMs."""
    if not checkin_generator:
        raise HTTPException(status_code=503, detail="Not initialized")

    try:
        results = await checkin_generator.send_daily_checkins()
        return JSONResponse(
            content={"status": "ok", "checkins_sent": len(results), "results": results}
        )
    except Exception:
        log.exception("Daily checkins failed")
        raise HTTPException(status_code=500, detail="Daily checkins failed")


@app.post("/v1/daily-admin-report")
async def daily_admin_report(request: Request):
    """Triggered by Cloud Scheduler to send the daily admin digest."""
    try:
        await send_daily_admin_reports()
        return JSONResponse(content={"status": "ok"})
    except Exception:
        log.exception("Daily admin report failed")
        raise HTTPException(status_code=500, detail="Report generation failed")


@app.post("/v1/session-prep")
async def session_prep_reminders(request: Request):
    """Triggered by Cloud Scheduler to send session prep reminders."""
    if not checkin_generator:
        raise HTTPException(status_code=503, detail="Not initialized")

    try:
        results = await checkin_generator.send_session_prep_reminders()
        return JSONResponse(
            content={"status": "ok", "reminders_sent": len(results), "results": results}
        )
    except Exception:
        log.exception("Session prep reminders failed")
        raise HTTPException(status_code=500, detail="Session prep failed")


# ── Verification endpoints ──────────────────────────────────────────────────


@app.post("/v1/run-verifications")
async def run_verifications(request: Request):
    """Triggered by Cloud Scheduler to run automated task verifications."""
    from .verifier import run_all_verifications

    try:
        results = await run_all_verifications()
        return JSONResponse(
            content={
                "status": "ok",
                "verifications_run": len(results),
                "results": results,
            }
        )
    except Exception:
        log.exception("Verifications failed")
        raise HTTPException(status_code=500, detail="Verifications failed")


# ── Link click tracking ─────────────────────────────────────────────────────


@app.get("/v1/track/{user_id}/{task_id}/{link_index}")
async def track_link_click(user_id: str, task_id: str, link_index: int):
    """Record a link click and redirect to the actual URL.

    The link_index maps to the position in the task's links array from the
    YAML template. After recording the click we 302-redirect to the real URL.
    """
    from .state import get_profile, record_link_click
    from .template import get_task_by_id, load_template

    profile = get_profile(user_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    template = load_template(profile.template_version)
    task = get_task_by_id(template, task_id)
    if not task or link_index >= len(task.links):
        raise HTTPException(status_code=404, detail="Link not found")

    link = task.links[link_index]
    record_link_click(
        user_id=user_id,
        task_id=task_id,
        link_index=link_index,
        link_url=link.url,
        link_label=link.label,
    )
    log.info("Tracked click: user=%s task=%s link=%s → %s", user_id, task_id, link.label, link.url)
    return RedirectResponse(url=link.url, status_code=302)


# ── Health check ────────────────────────────────────────────────────────────


@app.get("/health")
async def health():
    """Health check endpoint for Cloud Run."""
    return {"status": "healthy", "agent": "onboarding", "version": "1.0.0"}


@app.post("/v1/admin/drive-cleanup")
async def drive_cleanup():
    """Clean up old files from the Drive service account to free storage quota."""
    from .briefing import cleanup_sa_drive_storage

    try:
        result = cleanup_sa_drive_storage()
        return JSONResponse(content={"status": "ok", **result})
    except Exception:
        log.exception("Drive cleanup failed")
        raise HTTPException(status_code=500, detail="Drive cleanup failed")
