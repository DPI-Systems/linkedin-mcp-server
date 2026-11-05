from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def import_models() -> None:
    """Register every model on ``Base.metadata`` (Alembic, tests)."""
    from app.models import company, contact, job, network_event  # noqa: F401
