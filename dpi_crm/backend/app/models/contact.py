import datetime as dt
from typing import Any

from sqlalchemy import JSON, Date, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models._common import Timestamps


class Contact(Timestamps, Base):
    """One person. LinkedIn fills the profile fields; email, phone and notes
    belong to the team and no automation writes them."""

    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    # A contact is known by its public profile, its Sales Navigator id, or both.
    linkedin_username: Mapped[str | None] = mapped_column(String(200), unique=True)
    salesnav_profile_id: Mapped[str | None] = mapped_column(String(100), unique=True)
    member_urn: Mapped[str | None] = mapped_column(String(100))

    full_name: Mapped[str] = mapped_column(String(300))
    headline: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(String(500))
    company: Mapped[str | None] = mapped_column(String(300))
    company_since: Mapped[dt.date | None] = mapped_column(Date)
    company_since_text: Mapped[str | None] = mapped_column(String(200))
    location: Mapped[str | None] = mapped_column(String(300))
    profile_url: Mapped[str | None] = mapped_column(String(500))
    sales_nav_url: Mapped[str | None] = mapped_column(String(500))
    source: Mapped[str] = mapped_column(String(30))  # network | salesnav
    profile_synced_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    # The profile text last read, so a better parser can re-read it offline.
    profile_raw: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    # Contact details. LinkedIn's fill only an empty field; a person's edit
    # is never overwritten.
    email: Mapped[str | None] = mapped_column(String(300))
    phone: Mapped[str | None] = mapped_column(String(100))
    website: Mapped[str | None] = mapped_column(String(500))
    notes: Mapped[str | None] = mapped_column(Text)
    contact_info_synced_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    # The "Contact info" dialog as LinkedIn rendered it (birthday, connected since).
    contact_info_text: Mapped[str | None] = mapped_column(Text)

    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"))
    company_ref = relationship("Company", back_populates="contacts")
    events = relationship(
        "NetworkEvent",
        back_populates="contact",
        order_by="NetworkEvent.first_seen_at.desc()",
        cascade="all, delete-orphan",
    )
