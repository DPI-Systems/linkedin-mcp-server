import datetime as dt

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_client, get_runner
from app.core.config import get_settings
from app.db.session import get_db
from app.models.contact import Contact
from app.models.job import Job
from app.models.network_event import NetworkEvent
from app.schemas.crm import JobOut
from app.services.linkedin import LinkedInClient
from app.workers.runner import JobRunner
from app.workers.scheduler import JOB_ID

router = APIRouter(tags=["status"])


@router.get("/status")
async def status(
    request: Request,
    db: Session = Depends(get_db),
    client: LinkedInClient = Depends(get_client),
    runner: JobRunner = Depends(get_runner),
):
    since = dt.datetime.now(dt.UTC) - dt.timedelta(hours=24)
    last = db.scalar(select(Job).order_by(Job.id.desc()).limit(1))
    scheduler = getattr(request.app.state, "scheduler", None)
    cron_job = scheduler.get_job(JOB_ID) if scheduler else None
    return {
        "mcp_url": client.url,
        "mcp_reachable": await client.reachable(),
        "runner_busy": runner.busy,
        "sync_cron": get_settings().sync_cron,
        "next_sync_at": cron_job.next_run_time if cron_job else None,
        "contacts": db.scalar(select(func.count(Contact.id))),
        "events": db.scalar(select(func.count(NetworkEvent.id))),
        "events_last_24h": db.scalar(
            select(func.count(NetworkEvent.id)).where(NetworkEvent.first_seen_at >= since)
        ),
        "last_job": JobOut.model_validate(last) if last else None,
    }
