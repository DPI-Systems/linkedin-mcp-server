import datetime as dt
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class EventOut(ORM):
    id: int
    contact_id: int
    category: str
    detail: str
    post_url: str | None
    first_seen_at: dt.datetime


class EventWithContact(EventOut):
    contact_name: str
    contact_company: str | None


class ContactOut(ORM):
    id: int
    linkedin_username: str | None
    full_name: str
    headline: str | None
    title: str | None
    company: str | None
    company_since: dt.date | None
    company_since_text: str | None
    location: str | None
    profile_url: str | None
    sales_nav_url: str | None
    source: str
    company_id: int | None
    email: str | None
    phone: str | None
    website: str | None
    notes: str | None
    profile_synced_at: dt.datetime | None
    contact_info_synced_at: dt.datetime | None
    created_at: dt.datetime
    updated_at: dt.datetime


class ContactDetail(ContactOut):
    events: list[EventOut]
    contact_info_text: str | None


class ContactPatch(BaseModel):
    email: str | None = Field(default=None, max_length=300)
    phone: str | None = Field(default=None, max_length=100)
    website: str | None = Field(default=None, max_length=500)
    notes: str | None = None


class Page[T](BaseModel):
    items: list[T]
    total: int


class JobOut(ORM):
    id: int
    kind: str
    trigger: str
    status: str
    params: dict[str, Any]
    stats: dict[str, Any]
    log: str
    error: str | None
    created_at: dt.datetime
    started_at: dt.datetime | None
    finished_at: dt.datetime | None


class CompanyCandidate(BaseModel):
    company_id: str
    name: str
    industry: str | None = None
    employees: Any = None
    location: str | None = None
    description: str | None = None
    sales_nav_url: str | None = None
    linkedin_url: str | None = None


class LeadFilters(BaseModel):
    keywords: str | None = None
    titles: list[str] = []
    seniority: list[str] = []
    functions: list[str] = []
    regions: list[str] = []
    posted_recently: bool = False
    changed_jobs_recently: bool = False


class CompanyImportIn(BaseModel):
    companies: list[CompanyCandidate] = Field(min_length=1, max_length=10)
    max_results: int = Field(default=25, ge=1, le=100)
    filters: LeadFilters = LeadFilters()


class NetworkSyncIn(BaseModel):
    categories: list[str] | None = None
    read_profiles: bool = True
