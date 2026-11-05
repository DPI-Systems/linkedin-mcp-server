import datetime as dt

import pytest
from sqlalchemy import select

from app.crud import contacts as crud
from app.models.contact import Contact
from app.models.network_event import NetworkEvent
from app.services.automations import company_import, network_sync
from app.services.linkedin import LinkedInError
from tests.conftest import FakeClient, entry, profile, stop_error

LOG: list[str] = []


def log(line):
    LOG.append(line)


async def test_network_sync_stores_people_events_and_profiles(db):
    client = FakeClient(
        updates={
            "categories": {
                "birthdays": [entry("ana")],
                "job_changes": [entry("bob", "job_changes", "New job at Acme")],
            }
        },
        profiles={"ana": profile("Ana E"), "bob": profile("Bob E", company="Beta")},
    )
    stats = await network_sync(db, client, log, {})
    assert stats == {
        "updates": 2,
        "new_contacts": 2,
        "new_events": 2,
        "profiles_read": 2,
        "profile_errors": 0,
        "contact_info_read": 0,
        "contact_info_errors": 0,
        "contact_fields_filled": 0,
    }
    bob = db.scalar(select(Contact).where(Contact.linkedin_username == "bob"))
    assert (bob.full_name, bob.company, bob.company_since) == (
        "Bob E",
        "Beta",
        dt.date(2025, 11, 1),
    )
    assert bob.profile_synced_at is not None


async def test_second_sync_adds_nothing_and_skips_fresh_profiles(db):
    client = FakeClient(
        updates={"categories": {"birthdays": [entry("ana")]}},
        profiles={"ana": profile("Ana E")},
    )
    await network_sync(db, client, log, {})
    client.calls.clear()
    stats = await network_sync(db, client, log, {})
    assert stats["new_contacts"] == 0 and stats["new_events"] == 0
    assert stats["profiles_read"] == 0
    assert client.calls == [("network_updates", None)]
    assert db.scalar(select(NetworkEvent).where(NetworkEvent.category == "birthdays"))


async def test_a_stale_profile_is_read_again(db):
    client = FakeClient(
        updates={"categories": {"birthdays": [entry("ana")]}},
        profiles={"ana": profile("Ana E")},
    )
    await network_sync(db, client, log, {})
    ana = db.scalar(select(Contact))
    ana.profile_synced_at = dt.datetime.now(dt.UTC) - dt.timedelta(days=31)
    db.commit()
    stats = await network_sync(db, client, log, {})
    assert stats["profiles_read"] == 1


async def test_automations_never_overwrite_what_people_typed(db):
    client = FakeClient(
        updates={"categories": {"birthdays": [entry("ana")]}},
        profiles={"ana": profile("Ana E")},
    )
    contact, _ = crud.upsert_from_network(db, entry("ana"))
    contact.email, contact.phone, contact.notes = "ana@x.com", "600", "met at event"
    db.commit()
    await network_sync(db, client, log, {})
    db.refresh(contact)
    assert (contact.email, contact.phone, contact.notes) == ("ana@x.com", "600", "met at event")
    assert contact.company == "Acme"


async def test_a_profile_error_is_counted_and_the_rest_continue(db):
    client = FakeClient(
        updates={"categories": {"birthdays": [entry("ana"), entry("bob")]}},
        profiles={"ana": LinkedInError("t", "page_unreadable", "x"), "bob": profile("Bob E")},
    )
    stats = await network_sync(db, client, log, {})
    assert stats["profile_errors"] == 1 and stats["profiles_read"] == 1


async def test_linkedin_refusing_stops_the_sync_and_keeps_what_was_stored(db):
    client = FakeClient(
        updates={"categories": {"birthdays": [entry("ana"), entry("bob")]}},
        profiles={"ana": stop_error(), "bob": profile("Bob E")},
    )
    with pytest.raises(LinkedInError):
        await network_sync(db, client, log, {})
    assert ("person_profile", "bob") not in client.calls
    assert len(db.scalars(select(Contact)).all()) == 2


async def test_company_import_links_leads_to_the_chosen_company(db):
    lead = {
        "full_name": "Carla",
        "title": "CTO",
        "company": "Seanto",
        "sales_profile_id": "ACw1",
        "position_started_on": "2021-03",
        "tenure_at_company": {"years": 4, "months": 8},
        "sales_nav_url": "https://www.linkedin.com/sales/lead/ACw1,NAME_SEARCH,x",
    }
    client = FakeClient(people={"11749066": {"leads": [lead], "total_available": 93}})
    params = {
        "companies": [{"company_id": "11749066", "name": "Seanto SL"}],
        "max_results": 25,
        "filters": {"titles": ["CTO"]},
    }
    stats = await company_import(db, client, log, params)
    assert stats == {"companies": 1, "leads": 1, "new_contacts": 1, "errors": 0}
    assert client.calls == [("company_people", "11749066", 25, {"titles": ["CTO"]})]
    carla = db.scalar(select(Contact))
    assert (carla.company_since, carla.company_since_text) == (
        dt.date(2021, 3, 1),
        "4 años 8 meses en la empresa",
    )
    assert carla.company_ref.name == "Seanto SL" and carla.source == "salesnav"
    await company_import(db, client, log, params)
    assert len(db.scalars(select(Contact)).all()) == 1


async def test_a_parser_fix_reaches_stored_profiles_without_linkedin(db):
    from app.services.automations import reparse_profiles

    client = FakeClient(
        updates={"categories": {"birthdays": [entry("ana")]}},
        profiles={"ana": profile("Ana E", company="Acme")},
    )
    await network_sync(db, client, log, {})
    ana = db.scalar(select(Contact))
    ana.company = "wrong"
    db.commit()
    assert reparse_profiles(db) == 1
    db.refresh(ana)
    assert ana.company == "Acme"


INFO = {
    "emails": ["ana@acme.com"],
    "phones": [{"number": "600 11 22 33", "kind": "móvil"}],
    "websites": ["https://ana.example/"],
    "text": "Información de contacto\n\nCumpleaños\n\n4 de noviembre",
}


async def test_contact_info_fills_only_empty_fields_and_is_read_once(db):
    client = FakeClient(
        updates={"categories": {"birthdays": [entry("ana")]}},
        profiles={"ana": profile("Ana E")},
        contacts={"ana": INFO},
    )
    contact, _ = crud.upsert_from_network(db, entry("ana"))
    contact.email = "typed@by.team"
    db.commit()
    stats = await network_sync(db, client, log, {})
    db.refresh(contact)
    assert (contact.email, contact.phone, contact.website) == (
        "typed@by.team",
        "600 11 22 33",
        "https://ana.example/",
    )
    assert stats["contact_info_read"] == 1 and stats["contact_fields_filled"] == 2
    assert "Cumpleaños" in contact.contact_info_text
    client.calls.clear()
    await network_sync(db, client, log, {})
    assert ("contact_info", "ana") not in client.calls


async def test_a_connection_without_contact_info_is_not_asked_again(db):
    client = FakeClient(
        updates={"categories": {"birthdays": [entry("ana")]}},
        profiles={"ana": profile("Ana E")},
    )
    await network_sync(db, client, log, {})
    assert ("contact_info", "ana") in client.calls
    client.calls.clear()
    await network_sync(db, client, log, {})
    assert client.calls == [("network_updates", None)]


async def test_a_profile_and_a_sales_nav_import_share_one_company_row(db):
    from app.models.company import Company

    client = FakeClient(
        updates={"categories": {"birthdays": [entry("ana")]}},
        profiles={"ana": profile("Ana E", company="Acme")},  # /company/42/
        people={"42": {"leads": [{"full_name": "Carla", "sales_profile_id": "ACw1"}]}},
    )
    await network_sync(db, client, log, {})
    await company_import(db, client, log, {"companies": [{"company_id": "42", "name": "ACME SA"}]})
    companies = db.scalars(select(Company)).all()
    assert [(c.linkedin_company_id, c.name) for c in companies] == [("42", "ACME SA")]
    assert {c.company_id for c in db.scalars(select(Contact))} == {companies[0].id}


async def test_employers_without_a_linkedin_page_are_grouped_by_name(db):
    from app.models.company import Company

    nolink = profile("Ana E", company="Taller Pepe")
    nolink["references"]["experience"] = [
        {"kind": "company", "url": "/company/99/", "text": "Becario Otra Empresa"}
    ]
    bob = profile("Bob E", company="taller pepe")
    bob["references"]["experience"] = []
    client = FakeClient(
        updates={"categories": {"birthdays": [entry("ana"), entry("bob")]}},
        profiles={"ana": nolink, "bob": bob},
    )
    await network_sync(db, client, log, {})
    companies = db.scalars(select(Company)).all()
    # The older job's link (99) is not taken as the current employer's.
    assert [(c.linkedin_company_id, c.name) for c in companies] == [(None, "Taller Pepe")]
