"""raw_listing_item.description_text: plain text of the row's description / paragraph

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-24 15:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("raw_listing_item", sa.Column("description_text", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("raw_listing_item", schema=None) as batch_op:
        batch_op.drop_column("description_text")
