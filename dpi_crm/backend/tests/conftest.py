import os

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ["PROFILE_PAUSE_MIN"] = "0"
os.environ["PROFILE_PAUSE_MAX"] = "0"

from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base, import_models
from app.services.linkedin import LinkedInError


@pytest.fixture
def session_factory():
    import_models()
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture
def db(session_factory):
    with session_factory() as s:
        yield s


class FakeClient:
    """Stands in for the MCP server: scripted answers, recorded calls."""

    url = "http://fake/mcp"

    def __init__(self, updates=None, profiles=None, people=None, companies=None, contacts=None):
        self.updates = updates or {"categories": {}}
        self.profiles: dict[str, Any] = profiles or {}
        self.people: dict[str, Any] = people or {}
        self.companies = companies or []
        self.contacts: dict[str, Any] = contacts or {}
        self.calls: list[tuple[str, Any]] = []

    async def reachable(self):
        return True

    async def network_updates(self, categories=None):
        self.calls.append(("network_updates", categories))
        return self.updates

    async def person_profile(self, username):
        self.calls.append(("person_profile", username))
        value = self.profiles[username]
        if isinstance(value, Exception):
            raise value
        return value

    async def contact_info(self, username):
        self.calls.append(("contact_info", username))
        value = self.contacts.get(username, LinkedInError("t", "not_found", "no link"))
        if isinstance(value, Exception):
            raise value
        return value

    async def search_companies(self, name):
        self.calls.append(("search_companies", name))
        return {"companies": self.companies}

    async def company_people(self, company_id, max_results, filters):
        self.calls.append(("company_people", company_id, max_results, filters))
        value = self.people[company_id]
        if isinstance(value, Exception):
            raise value
        return value


def stop_error():
    return LinkedInError("get_person_profile", "rate_limited", "429")


def entry(username, category="birthdays", detail="Birthday today", name=None):
    return {
        "category": category,
        "name": name or username.title(),
        "detail": detail,
        "linkedin_username": username,
        "profile_url": f"https://www.linkedin.com/in/{username}/",
        "post_url": None,
    }


def profile(name, company="Acme", start="nov. de 2025"):
    return {
        "sections": {
            "main_profile": f"{name}\n\n· 1er\n\nCTO | AI\n\nSevilla y alrededores\n\n·",
            "experience": f"Experiencia\n\nCTO\n\n{company} · Jornada completa\n\n"
            f"{start} - actualidad · 1 año\n\nSevilla",
        },
        "references": {
            "experience": [{"kind": "company", "url": "/company/42/", "text": f"CTO {company}"}]
        },
    }
