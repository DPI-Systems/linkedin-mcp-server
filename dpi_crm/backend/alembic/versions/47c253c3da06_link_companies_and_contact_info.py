"""link companies and contact info

Revision ID: 47c253c3da06
Revises: e4ec629823d0
Create Date: 2026-11-05 17:33:49.602757

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '47c253c3da06'
down_revision: Union[str, Sequence[str], None] = 'e4ec629823d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # A rename, not drop-and-add, so the companies already stored keep their id.
    op.alter_column(
        "companies", "salesnav_company_id", new_column_name="linkedin_company_id", nullable=True
    )
    op.add_column("contacts", sa.Column("website", sa.String(length=500), nullable=True))
    op.add_column(
        "contacts", sa.Column("contact_info_synced_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("contacts", sa.Column("contact_info_text", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("contacts", "contact_info_text")
    op.drop_column("contacts", "contact_info_synced_at")
    op.drop_column("contacts", "website")
    op.alter_column(
        "companies", "linkedin_company_id", new_column_name="salesnav_company_id", nullable=False
    )
