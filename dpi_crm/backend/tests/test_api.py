import time

import pytest
from fastapi.testclient import TestClient

from app.db.session import get_db
from app.main import create_app
from app.models.job import Job
from app.workers.runner import JobRunner
from tests.conftest import FakeClient, entry, profile, stop_error


@pytest.fixture
def setup(session_factory):
    fake = FakeClient(
        updates={"categories": {"birthdays": [entry("ana")]}},
        profiles={"ana": profile("Ana E")},
        companies=[
            {"company_id": "1", "name": "Seanto SL"},
            {"company_id": "2", "name": "Seanto UTE"},
        ],
    )
    app = create_app(session_factory, lambda: fake)

    def db_override():
        with session_factory() as s:
            yield s

    app.dependency_overrides[get_db] = db_override
    with TestClient(app) as http:
        yield http, app.state.runner, fake, session_factory


def wait_for(http, job_id):
    for _ in range(200):
        job = http.get(f"/api/v1/jobs/{job_id}").json()
        if job["status"] not in ("queued", "running"):
            return job
        time.sleep(0.02)
    raise AssertionError("job never finished")


def test_a_second_sync_is_refused_while_one_is_active(setup):
    http, _, _, sessions = setup
    with sessions() as s:
        s.add(Job(kind="network_sync", trigger="cron", status="running"))
        s.commit()
    assert http.post("/api/v1/jobs/network-sync", json={}).status_code == 409


def test_sync_job_then_contact_edit_round_trip(setup):
    http, *_ = setup
    job = http.post("/api/v1/jobs/network-sync", json={}).json()
    done = wait_for(http, job["id"])
    assert done["status"] == "succeeded" and done["stats"]["new_contacts"] == 1

    contacts = http.get("/api/v1/contacts", params={"q": "ana"}).json()
    assert contacts["total"] == 1
    cid = contacts["items"][0]["id"]
    patched = http.patch(
        f"/api/v1/contacts/{cid}", json={"email": " ana@x.com ", "phone": ""}
    ).json()
    assert patched["email"] == "ana@x.com" and patched["phone"] is None
    assert patched["company"] == "Acme" and patched["events"][0]["category"] == "birthdays"
    events = http.get("/api/v1/events", params={"category": "birthdays"}).json()
    assert events["items"][0]["contact_name"] == "Ana E"


def test_a_stop_from_linkedin_fails_the_job_with_a_plain_reason(setup):
    http, _, fake, _ = setup
    fake.profiles["ana"] = stop_error()
    job = http.post("/api/v1/jobs/network-sync", json={}).json()
    done = wait_for(http, job["id"])
    assert done["status"] == "failed" and "rate_limited" in done["error"]


def test_company_search_lists_every_match(setup):
    http, *_ = setup
    names = [
        c["name"] for c in http.get("/api/v1/companies/search", params={"name": "Seanto"}).json()
    ]
    assert names == ["Seanto SL", "Seanto UTE"]


def test_company_search_waits_for_a_running_automation(setup):
    http, runner, *_ = setup
    runner.busy = True
    assert http.get("/api/v1/companies/search", params={"name": "Seanto"}).status_code == 409


def test_a_job_left_running_is_marked_failed_on_start(session_factory):
    with session_factory() as s:
        s.add(Job(kind="network_sync", trigger="cron", status="running"))
        s.commit()

    async def go():
        runner = JobRunner(session_factory, FakeClient)
        runner.start()
        await runner.stop()

    import asyncio

    asyncio.run(go())
    with session_factory() as s:
        assert s.query(Job).one().status == "failed"
