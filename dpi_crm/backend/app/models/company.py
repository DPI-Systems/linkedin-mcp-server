from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models._common import Timestamps


class Company(Timestamps, Base):
    """An employer. LinkedIn's numeric company id is the same in a profile's
    experience links and in Sales Navigator, so both paths land on one row;
    a company with no LinkedIn page is known by name only."""

    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(primary_key=True)
    linkedin_company_id: Mapped[str | None] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(300))
    industry: Mapped[str | None] = mapped_column(String(200))
    employees: Mapped[str | None] = mapped_column(String(100))
    location: Mapped[str | None] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text)
    sales_nav_url: Mapped[str | None] = mapped_column(String(500))
    linkedin_url: Mapped[str | None] = mapped_column(String(500))

    contacts = relationship("Contact", back_populates="company_ref")
