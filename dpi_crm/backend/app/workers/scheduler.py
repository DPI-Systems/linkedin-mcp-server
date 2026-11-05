from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import get_settings
from app.workers.runner import JobRunner

JOB_ID = "network_sync"


def build_scheduler(runner: JobRunner) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()

    def tick() -> None:
        if not runner.has_active("network_sync"):
            runner.enqueue("network_sync", {}, trigger="cron")

    scheduler.add_job(tick, CronTrigger.from_crontab(get_settings().sync_cron), id=JOB_ID)
    return scheduler
