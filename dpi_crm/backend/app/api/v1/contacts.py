from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.db.session import get_db
from app.models.contact import Contact
from app.schemas.crm import ContactDetail, ContactOut, ContactPatch, Page

router = APIRouter(prefix="/contacts", tags=["contacts"])


@router.get("", response_model=Page[ContactOut])
def list_contacts(
    q: str | None = None,
    source: str | None = None,
    company_id: int | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    stmt = select(Contact)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                Contact.full_name.ilike(like),
                Contact.company.ilike(like),
                Contact.title.ilike(like),
                Contact.email.ilike(like),
            )
        )
    if source:
        stmt = stmt.where(Contact.source == source)
    if company_id:
        stmt = stmt.where(Contact.company_id == company_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = db.scalars(
        stmt.order_by(Contact.updated_at.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return {"items": items, "total": total}


@router.get("/{contact_id}", response_model=ContactDetail)
def get_contact(contact_id: int, db: Session = Depends(get_db)):
    contact = db.scalar(
        select(Contact).options(selectinload(Contact.events)).where(Contact.id == contact_id)
    )
    if contact is None:
        raise HTTPException(404, "Contacto no encontrado")
    return contact


@router.patch("/{contact_id}", response_model=ContactDetail)
def patch_contact(contact_id: int, body: ContactPatch, db: Session = Depends(get_db)):
    contact = db.get(Contact, contact_id)
    if contact is None:
        raise HTTPException(404, "Contacto no encontrado")
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(contact, key, (value or "").strip() or None)
    db.commit()
    return get_contact(contact_id, db)
