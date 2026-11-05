from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_runner
from app.db.session import get_db
from app.models.job import Job
from app.schemas.crm import CompanyImportIn, JobOut, NetworkSyncIn
from app.workers.runner import JobRunner

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("", response_model=list[JobOut])
def list_jobs(limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db)):
    return db.scalars(select(Job).order_by(Job.id.desc()).limit(limit)).all()


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: int, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Ejecución no encontrada")
    return job


@router.post("/network-sync", response_model=JobOut, status_code=202)
def start_network_sync(body: NetworkSyncIn, runner: JobRunner = Depends(get_runner)):
    if runner.has_active("network_sync"):
        raise HTTPException(409, "Ya hay una sincronización de novedades en curso")
    return runner.enqueue("network_sync", body.model_dump(), trigger="manual")


@router.post("/company-import", response_model=JobOut, status_code=202)
def start_company_import(body: CompanyImportIn, runner: JobRunner = Depends(get_runner)):
    return runner.enqueue("company_import", body.model_dump(), trigger="manual")
