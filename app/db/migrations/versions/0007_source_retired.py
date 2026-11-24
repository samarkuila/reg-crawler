"""source.retired: a deleted source is kept for history but leaves the register

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-23 18:50:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A plain ADD COLUMN, not a table rebuild: other tables point at `source`, and SQLite cannot
    # drop and re-create it while they do.
    op.add_column(
        "source", sa.Column("retired", sa.Boolean(), server_default=sa.false(), nullable=False)
    )


def downgrade() -> None:
    op.drop_column("source", "retired")
