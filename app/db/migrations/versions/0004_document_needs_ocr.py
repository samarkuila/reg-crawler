"""document.needs_ocr

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-23 20:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A plain ADD COLUMN (not a table rebuild), so the append-only triggers on `document` stay.
    with op.batch_alter_table("document", schema=None, recreate="never") as batch_op:
        batch_op.add_column(
            sa.Column("needs_ocr", sa.Boolean(), server_default="0", nullable=False)
        )


def downgrade() -> None:
    with op.batch_alter_table("document", schema=None) as batch_op:
        batch_op.drop_column("needs_ocr")
