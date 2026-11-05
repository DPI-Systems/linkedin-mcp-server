"""Writes to the CRM. Automations only touch LinkedIn's fields; email, phone
and notes are written by people through the API."""

import datetime as dt
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models._common import utcnow
from app.models.company import Company
from app.models.contact import Contact
from app.models.network_event import NetworkEvent

# Fields an automation may fill; anything a person typed is outside this set.
LINKEDIN_FIELDS = (
    "full_name",
    "headline",
    "title",
    "company",
    "company_since",
    "company_since_text",
    "location",
    "profile_url",
    "sales_nav_url",
    "member_urn",
)


def _apply(contact: Contact, values: dict[str, Any]) -> None:
    for key in LINKEDIN_FIELDS:
        value = values.get(key)
        if value not in (None, ""):
            setattr(contact, key, value)


def upsert_from_network(db: Session, entry: dict[str, Any]) -> tuple[Contact, bool]:
    username = entry["linkedin_username"]
    contact = db.scalar(select(Contact).where(Contact.linkedin_username == username))
    created = contact is None
    if contact is None:
        contact = Contact(
            linkedin_username=username,
            full_name=entry.get("name") or username,
            source="network",
        )
        db.add(contact)
    _apply(contact, {"profile_url": entry.get("profile_url")})
    db.flush()
    return contact, created


def record_event(db: Session, contact: Contact, entry: dict[str, Any]) -> bool:
    detail = entry.get("detail") or ""
    exists = db.scalar(
        select(NetworkEvent.id).where(
            NetworkEvent.contact_id == contact.id,
            NetworkEvent.category == entry["category"],
            NetworkEvent.detail == detail,
        )
    )
    if exists:
        return False
    db.add(
        NetworkEvent(
            contact_id=contact.id,
            category=entry["category"],
            detail=detail,
            post_url=entry.get("post_url"),
        )
    )
    db.flush()
    return True


def needs_profile(contact: Contact, refresh_days: int, now: dt.datetime | None = None) -> bool:
    if not contact.linkedin_username:
        return False
    if contact.profile_synced_at is None:
        return True
    now = now or utcnow()
    synced = contact.profile_synced_at
    if synced.tzinfo is None:
        synced = synced.replace(tzinfo=dt.UTC)
    return now - synced > dt.timedelta(days=refresh_days)


def apply_profile(
    contact: Contact, parsed: dict[str, Any], raw: dict[str, Any] | None = None
) -> None:
    _apply(contact, parsed)
    if raw is not None:
        contact.profile_raw = raw
    contact.profile_synced_at = utcnow()


def find_or_create_company(db: Session, linkedin_id: str | None, name: str) -> Company:
    """One row per employer: by LinkedIn id when known, else by name."""
    name = name.strip()
    company = None
    if linkedin_id:
        company = db.scalar(select(Company).where(Company.linkedin_company_id == linkedin_id))
    if company is None:
        # A row created from a name alone gets its id once one is seen.
        company = db.scalar(
            select(Company).where(
                func.lower(Company.name) == name.lower(), Company.linkedin_company_id.is_(None)
            )
        )
        if company is not None and linkedin_id:
            company.linkedin_company_id = linkedin_id
    if company is None:
        company = Company(linkedin_company_id=linkedin_id, name=name)
        db.add(company)
    db.flush()
    return company


def upsert_company(db: Session, data: dict[str, Any]) -> Company:
    """A company chosen in Sales Navigator, with what its search returned."""
    company = find_or_create_company(db, str(data["company_id"]), data["name"])
    for key in (
        "name",
        "industry",
        "employees",
        "location",
        "description",
        "sales_nav_url",
        "linkedin_url",
    ):
        if data.get(key) not in (None, ""):
            setattr(company, key, str(data[key]))
    db.flush()
    return company


def link_company(db: Session, contact: Contact, parsed: dict[str, Any]) -> None:
    """Point a profile-read contact at its current employer's row."""
    name = parsed.get("company")
    if name:
        contact.company_id = find_or_create_company(db, parsed.get("company_linkedin_id"), name).id


def apply_contact_info(contact: Contact, info: dict[str, Any]) -> list[str]:
    """Fill empty contact fields from LinkedIn; returns the ones filled."""
    filled = []
    candidates = {
        "email": (info.get("emails") or [None])[0],
        "phone": ((info.get("phones") or [{}])[0] or {}).get("number"),
        "website": (info.get("websites") or [None])[0],
    }
    for key, value in candidates.items():
        if value and not getattr(contact, key):
            setattr(contact, key, value)
            filled.append(key)
    contact.contact_info_text = info.get("text")
    contact.contact_info_synced_at = utcnow()
    return filled


def upsert_from_lead(db: Session, lead: dict[str, Any], company: Company) -> tuple[Contact, bool]:
    profile_id = lead.get("sales_profile_id")
    contact = None
    if profile_id:
        contact = db.scalar(select(Contact).where(Contact.salesnav_profile_id == profile_id))
    created = contact is None
    if contact is None:
        contact = Contact(
            salesnav_profile_id=profile_id,
            full_name=lead.get("full_name") or "?",
            source="salesnav",
        )
        db.add(contact)
    since = None
    started = lead.get("position_started_on")  # "2021-03" or "2021"
    if isinstance(started, str) and started[:4].isdigit():
        month = int(started[5:7]) if len(started) >= 7 else 1
        since = dt.date(int(started[:4]), month, 1)
    tenure = lead.get("tenure_at_company") or {}
    tenure_text = None
    if isinstance(tenure, dict) and (tenure.get("years") or tenure.get("months")):
        tenure_text = f"{tenure.get('years', 0)} años {tenure.get('months', 0)} meses en la empresa"
    _apply(
        contact,
        {
            "full_name": lead.get("full_name"),
            "title": lead.get("title"),
            "company": lead.get("company") or company.name,
            "company_since": since,
            "company_since_text": tenure_text,
            "location": lead.get("location"),
            "sales_nav_url": lead.get("sales_nav_url"),
            "member_urn": lead.get("member_urn"),
        },
    )
    contact.company_id = company.id
    db.flush()
    return contact, created
