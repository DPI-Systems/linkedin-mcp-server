from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_client, get_runner
from app.db.session import get_db
from app.models.company import Company
from app.models.contact import Contact
from app.schemas.crm import CompanyCandidate
from app.services.linkedin import LinkedInClient, LinkedInError
from app.workers.runner import JobRunner

router = APIRouter(prefix="/companies", tags=["companies"])


@router.get("/search", response_model=list[CompanyCandidate])
async def search_companies(
    name: str = Query(min_length=2, max_length=120),
    client: LinkedInClient = Depends(get_client),
    runner: JobRunner = Depends(get_runner),
):
    """Live Sales Navigator search: every match, for a person to choose."""
    if runner.busy:
        raise HTTPException(409, "Hay una automatización usando LinkedIn; prueba al terminar")
    try:
        data = await client.search_companies(name)
    except LinkedInError as exc:
        raise HTTPException(502, f"LinkedIn: {exc.code}: {exc.message[:300]}") from exc
    return data.get("companies") or []


@router.get("/{company_id}")
def get_company(company_id: int, db: Session = Depends(get_db)):
    company = db.get(Company, company_id)
    if company is None:
        raise HTTPException(404, "Empresa no encontrada")
    return next(c for c in list_companies(None, db) if c["id"] == company_id)


@router.get("")
def list_companies(q: str | None = None, db: Session = Depends(get_db)):
    stmt = select(Company, func.count(Contact.id)).outerjoin(Contact).group_by(Company.id)
    if q:
        stmt = stmt.where(Company.name.ilike(f"%{q}%"))
    rows = db.execute(stmt.order_by(func.count(Contact.id).desc(), Company.name)).all()
    return [
        {
            "id": c.id,
            "linkedin_company_id": c.linkedin_company_id,
            "name": c.name,
            "industry": c.industry,
            "employees": c.employees,
            "location": c.location,
            "sales_nav_url": c.sales_nav_url,
            "linkedin_url": c.linkedin_url
            or (
                f"https://www.linkedin.com/company/{c.linkedin_company_id}/"
                if c.linkedin_company_id
                else None
            ),
            "contacts": n,
        }
        for c, n in rows
    ]
