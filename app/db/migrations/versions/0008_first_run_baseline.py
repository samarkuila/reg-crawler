"""first-crawl window: source baseline, seen_fingerprint, item origin, per-run counters

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-24 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Plain ADD COLUMN (no table rebuild: other tables point at source / regulatory_item).
    # The allowed values are enforced by the models and services; SQLite cannot add a CHECK later.
    op.add_column("source", sa.Column("first_run_mode", sa.String(17), nullable=True))
    op.add_column("source", sa.Column("first_run_days", sa.Integer(), nullable=True))
    op.add_column(
        "source",
        sa.Column("sorted_newest_first", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    op.add_column(
        "source",
        sa.Column(
            "baseline_status",
            sa.String(17),
            server_default="NOT_BASELINED",
            nullable=False,
        ),
    )
    op.add_column("source", sa.Column("baseline_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("source", sa.Column("baseline_window_start", sa.Date(), nullable=True))
    op.add_column(
        "regulatory_item",
        sa.Column("origin", sa.String(12), server_default="CRAWL", nullable=False),
    )
    op.add_column(
        "regulatory_item",
        sa.Column("flags", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
    )
    op.add_column(
        "source_check",
        sa.Column("stats", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
    )

    op.create_table(
        "seen_fingerprint",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("source_id", sa.String(36), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("published_date", sa.Date(), nullable=True),
        sa.Column("first_seen_run_id", sa.String(36), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(24), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_seen_fingerprint")),
        sa.ForeignKeyConstraint(
            ["source_id"], ["source.id"], name=op.f("fk_seen_fingerprint_source_id_source")
        ),
        sa.ForeignKeyConstraint(
            ["first_seen_run_id"],
            ["crawl_run.id"],
            name=op.f("fk_seen_fingerprint_first_seen_run_id_crawl_run"),
        ),
        sa.UniqueConstraint("source_id", "fingerprint", name=op.f("uq_seen_fingerprint_source_id")),
    )
    op.create_index(
        op.f("ix_seen_fingerprint_source_id"), "seen_fingerprint", ["source_id"], unique=False
    )
    # Insert-only: the app only ever inserts. These triggers make that a database rule too.
    if op.get_bind().dialect.name == "sqlite":
        for action in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER seen_fingerprint_no_{action.lower()} BEFORE {action} ON "
                "seen_fingerprint BEGIN SELECT RAISE(ABORT, "
                "'seen_fingerprint is insert-only'); END;"
            )


def downgrade() -> None:
    op.drop_table("seen_fingerprint")
    op.drop_column("source_check", "stats")
    op.drop_column("regulatory_item", "flags")
    op.drop_column("regulatory_item", "origin")
    for name in (
        "baseline_window_start",
        "baseline_at",
        "baseline_status",
        "sorted_newest_first",
        "first_run_days",
        "first_run_mode",
    ):
        op.drop_column("source", name)
