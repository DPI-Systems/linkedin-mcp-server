"""Runs queued jobs one at a time: there is one LinkedIn browser."""

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models._common import utcnow
from app.models.job import Job
from app.services.automations import AUTOMATIONS
from app.services.linkedin import LinkedInClient, LinkedInError

logger = logging.getLogger(__name__)
ACTIVE = ("queued", "running")


class JobRunner:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        client_factory: Callable[[], LinkedInClient] = LinkedInClient,
    ) -> None:
        self._sessions = session_factory
        self._client = client_factory
        self._queue: asyncio.Queue[int] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None
        self.busy = False

    def start(self) -> None:
        with self._sessions() as db:
            # A job left running by a stopped server did not finish.
            for job in db.scalars(select(Job).where(Job.status.in_(ACTIVE))):
                job.status = "failed"
                job.error = "El servidor se detuvo durante la ejecución."
                job.finished_at = utcnow()
            db.commit()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    def enqueue(self, kind: str, params: dict[str, Any], trigger: str) -> Job:
        if kind not in AUTOMATIONS:
            raise ValueError(f"Unknown automation {kind!r}")
        with self._sessions() as db:
            job = Job(kind=kind, trigger=trigger, params=params, status="queued")
            db.add(job)
            db.commit()
            db.refresh(job)
        self._queue.put_nowait(job.id)
        return job

    def has_active(self, kind: str) -> bool:
        with self._sessions() as db:
            return (
                db.scalar(select(Job.id).where(Job.kind == kind, Job.status.in_(ACTIVE)))
                is not None
            )

    async def run_one(self, job_id: int) -> None:
        with self._sessions() as db:
            job = db.get(Job, job_id)
            if job is None:
                return
            job.status, job.started_at = "running", utcnow()
            db.commit()

            def log(line: str) -> None:
                stamp = utcnow().strftime("%H:%M:%S")
                job.log = (job.log or "") + f"[{stamp}] {line}\n"
                db.commit()

            try:
                job.stats = await AUTOMATIONS[job.kind](db, self._client(), log, job.params)
                job.status = "succeeded"
            except LinkedInError as exc:
                job.status = "failed"
                job.error = (
                    f"LinkedIn ha parado la automatización ({exc.code}). "
                    "No se reintenta: revisa la cuenta en el navegador."
                    if exc.must_stop
                    else str(exc)
                )
            except Exception as exc:  # recorded on the job, never lost
                logger.exception("job %s failed", job_id)
                job.status, job.error = "failed", f"{type(exc).__name__}: {exc}"
            job.finished_at = utcnow()
            db.commit()

    async def _loop(self) -> None:
        while True:
            job_id = await self._queue.get()
            self.busy = True
            try:
                await self.run_one(job_id)
            finally:
                self.busy = False
