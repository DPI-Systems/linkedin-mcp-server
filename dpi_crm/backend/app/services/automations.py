"""The automations. Each one is a plain async function over a DB session and
a LinkedIn client, so the API, the cron and tests run the same code."""

import asyncio
import logging
import random
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.crud import contacts as crud
from app.services.linkedin import LinkedInClient, LinkedInError
from app.services.profile_parse import parse_profile, raw_for_storage

logger = logging.getLogger(__name__)
Log = Callable[[str], None]


async def _pause(settings: Any) -> None:
    await asyncio.sleep(random.uniform(settings.profile_pause_min, settings.profile_pause_max))


async def network_sync(
    db: Session, client: LinkedInClient, log: Log, params: dict[str, Any]
) -> dict[str, Any]:
    """Read "Catch up", store each person and event, then read the profiles
    that are new or stale."""
    settings = get_settings()
    stats = {
        "updates": 0,
        "new_contacts": 0,
        "new_events": 0,
        "profiles_read": 0,
        "profile_errors": 0,
        "contact_info_read": 0,
        "contact_info_errors": 0,
        "contact_fields_filled": 0,
    }
    seen = []
    data = await client.network_updates(params.get("categories"))
    for category, err in (data.get("errors") or {}).items():
        log(f"No se pudo leer {category}: {err}")
    to_read = []
    for category, entries in (data.get("categories") or {}).items():
        log(f"{category}: {len(entries)} novedades")
        for entry in entries:
            stats["updates"] += 1
            contact, created = crud.upsert_from_network(db, entry)
            if contact not in seen:
                seen.append(contact)
            stats["new_contacts"] += created
            stats["new_events"] += crud.record_event(db, contact, entry)
            if (
                crud.needs_profile(contact, settings.profile_refresh_days)
                and contact not in to_read
            ):
                to_read.append(contact)
    db.commit()

    if not params.get("read_profiles", True):
        return stats
    log(f"Perfiles por leer: {len(to_read)}")
    for i, contact in enumerate(to_read):
        if i:
            await _pause(settings)
        try:
            payload = await client.person_profile(contact.linkedin_username or "")
        except LinkedInError as exc:
            if exc.must_stop:
                db.commit()
                raise
            stats["profile_errors"] += 1
            log(f"Perfil de {contact.full_name}: {exc.code} {exc.message[:200]}")
            continue
        parsed = parse_profile(payload)
        crud.apply_profile(contact, parsed, raw_for_storage(payload))
        crud.link_company(db, contact, parsed)
        db.commit()
        stats["profiles_read"] += 1
        log(f"Perfil leído: {contact.full_name} ({contact.company or 'sin empresa'})")

    # Contact details: one more page per connection, read once.
    pending = [c for c in seen if c.linkedin_username and c.contact_info_synced_at is None]
    for contact in pending:
        await _pause(settings)
        try:
            info = await client.contact_info(contact.linkedin_username or "")
        except LinkedInError as exc:
            if exc.must_stop:
                db.commit()
                raise
            if exc.code == "not_found":
                contact.contact_info_synced_at = crud.utcnow()
                db.commit()
                continue
            stats["contact_info_errors"] += 1
            log(f"Contacto de {contact.full_name}: {exc.code} {exc.message[:200]}")
            continue
        filled = crud.apply_contact_info(contact, info)
        db.commit()
        stats["contact_info_read"] += 1
        if filled:
            stats["contact_fields_filled"] += len(filled)
            log(f"Datos de contacto de {contact.full_name}: {', '.join(filled)}")
    return stats
    log(f"Perfiles por leer: {len(to_read)}")
    for i, contact in enumerate(to_read):
        if i:
            await asyncio.sleep(
                random.uniform(settings.profile_pause_min, settings.profile_pause_max)
            )
        try:
            payload = await client.person_profile(contact.linkedin_username or "")
        except LinkedInError as exc:
            if exc.must_stop:
                db.commit()
                raise
            stats["profile_errors"] += 1
            log(f"Perfil de {contact.full_name}: {exc.code} {exc.message[:200]}")
            continue
        crud.apply_profile(contact, parse_profile(payload), raw_for_storage(payload))
        db.commit()
        stats["profiles_read"] += 1
        log(f"Perfil leído: {contact.full_name} ({contact.company or 'sin empresa'})")
    return stats


async def company_import(
    db: Session, client: LinkedInClient, log: Log, params: dict[str, Any]
) -> dict[str, Any]:
    """Store the chosen companies and every person Sales Navigator lists at each."""
    stats = {"companies": 0, "leads": 0, "new_contacts": 0, "errors": 0}
    filters = params.get("filters") or {}
    max_results = int(params.get("max_results") or 25)
    for i, data in enumerate(params.get("companies") or []):
        if i:
            await asyncio.sleep(random.uniform(10.0, 20.0))
        company = crud.upsert_company(db, data)
        db.commit()
        stats["companies"] += 1
        try:
            result = await client.company_people(str(data["company_id"]), max_results, filters)
        except LinkedInError as exc:
            if exc.must_stop:
                raise
            stats["errors"] += 1
            log(f"{company.name}: {exc.code} {exc.message[:200]}")
            continue
        leads = result.get("leads") or []
        for lead in leads:
            _, created = crud.upsert_from_lead(db, lead, company)
            stats["new_contacts"] += created
        stats["leads"] += len(leads)
        db.commit()
        log(f"{company.name}: {len(leads)} personas de {result.get('total_available', '?')}")
    return stats


AUTOMATIONS = {"network_sync": network_sync, "company_import": company_import}


def reparse_profiles(db: Session) -> int:
    """Parse every stored profile again; no LinkedIn access."""
    from sqlalchemy import select

    from app.models.contact import Contact

    count = 0
    for contact in db.scalars(select(Contact).where(Contact.profile_raw.is_not(None))):
        parsed = parse_profile(contact.profile_raw or {})
        crud._apply(contact, parsed)
        crud.link_company(db, contact, parsed)
        count += 1
    db.commit()
    return count
