"""raw listing item

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-23 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.db.types import TZDateTime

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "raw_listing_item",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=False),
        sa.Column(
            "channel",
            sa.Enum(
                "RSS",
                "HTML",
                "XML",
                name="raw_listing_item_channel",
                native_enum=False,
                create_constraint=True,
                length=8,
            ),
            nullable=False,
        ),
        sa.Column("snapshot_id", sa.String(length=36), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("ref_no", sa.String(length=200), nullable=True),
        sa.Column("displayed_date", sa.Date(), nullable=True),
        sa.Column("raw_extra", sa.JSON(), nullable=False),
        sa.Column("fetched_at", TZDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"], ["crawl_run.id"], name=op.f("fk_raw_listing_item_run_id_crawl_run")
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["source.id"], name=op.f("fk_raw_listing_item_source_id_source")
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["snapshot.id"],
            name=op.f("fk_raw_listing_item_snapshot_id_snapshot"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_raw_listing_item")),
    )
    with op.batch_alter_table("raw_listing_item", schema=None) as batch_op:
        batch_op.create_index("ix_raw_listing_item_run_source", ["run_id", "source_id"])


def downgrade() -> None:
    with op.batch_alter_table("raw_listing_item", schema=None) as batch_op:
        batch_op.drop_index("ix_raw_listing_item_run_source")
    op.drop_table("raw_listing_item")
