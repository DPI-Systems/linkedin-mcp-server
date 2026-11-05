import datetime as dt

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models._common import utcnow


class NetworkEvent(Base):
    """A "Catch up" entry: a birthday, a job change, an anniversary."""

    __tablename__ = "network_events"
    __table_args__ = (UniqueConstraint("contact_id", "category", "detail"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"))
    category: Mapped[str] = mapped_column(String(40))
    detail: Mapped[str] = mapped_column(Text, default="")
    post_url: Mapped[str | None] = mapped_column(String(500))
    first_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    contact = relationship("Contact", back_populates="events")
