from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.contact import Contact
from app.models.network_event import NetworkEvent
from app.schemas.crm import EventWithContact, Page

router = APIRouter(prefix="/events", tags=["events"])


@router.get("", response_model=Page[EventWithContact])
def list_events(
    category: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    stmt = select(NetworkEvent, Contact).join(Contact)
    if category:
        stmt = stmt.where(NetworkEvent.category == category)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(
        stmt.order_by(NetworkEvent.first_seen_at.desc(), NetworkEvent.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    items = [
        EventWithContact(
            **{
                c: getattr(e, c)
                for c in ("id", "contact_id", "category", "detail", "post_url", "first_seen_at")
            },
            contact_name=contact.full_name,
            contact_company=contact.company,
        )
        for e, contact in rows
    ]
    return {"items": items, "total": total}
