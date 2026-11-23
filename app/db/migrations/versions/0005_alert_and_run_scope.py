"""alert table and crawl_run.sources

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-23 21:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.db.types import TZDateTime

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KINDS = (
    "SOURCE_FAILED",
    "SOURCE_BLOCKED",
    "SOURCE_ZERO_ITEMS",
    "SOURCE_PARSE_ERROR",
    "SANITY_WARNING",
    "URGENT_ITEM",
    "RUN_PROBLEM",
    "RUN_SKIPPED",
    "RUN_MISSED",
)


def upgrade() -> None:
    op.create_table(
        "alert",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column(
            "kind",
            sa.Enum(
                *KINDS,
                name="alert_kind",
                native_enum=False,
                create_constraint=True,
                length=max(len(k) for k in KINDS) + 4,
            ),
            nullable=False,
        ),
        sa.Column("severity", sa.String(length=10), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=True),
        sa.Column("item_id", sa.String(length=36), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("created_at", TZDateTime(), nullable=False),
        sa.Column("delivered_at", TZDateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["run_id"], ["crawl_run.id"], name=op.f("fk_alert_run_id_crawl_run")
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["source.id"], name=op.f("fk_alert_source_id_source")
        ),
        sa.ForeignKeyConstraint(
            ["item_id"], ["regulatory_item.id"], name=op.f("fk_alert_item_id_regulatory_item")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alert")),
    )
    with op.batch_alter_table("alert", schema=None) as batch_op:
        batch_op.create_index("ix_alert_run_id", ["run_id"])
    with op.batch_alter_table("crawl_run", schema=None) as batch_op:
        batch_op.add_column(sa.Column("sources", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("crawl_run", schema=None) as batch_op:
        batch_op.drop_column("sources")
    with op.batch_alter_table("alert", schema=None) as batch_op:
        batch_op.drop_index("ix_alert_run_id")
    op.drop_table("alert")
