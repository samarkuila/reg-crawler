"""source.fetch_engine, wait_for_selector, wait_timeout_s (M5: how a page is fetched)

"Browser" stops being a way of reading and becomes a way of fetching:

- source.type BROWSER  ->  type HTML_LIST + fetch_engine 'browser'
- everything else      ->  fetch_engine 'http'
- source.requires_js is no longer used. The column stays (NOT NULL, now defaults to 0) so old
  databases and old code paths keep working; every row is set to 0. A stale requires_js = 1 on a
  source that was not a BROWSER source was already ignored by crawls, so it is cleared, never
  turned into a browser fetch (an engine is only chosen by a person).

Each converted source gets an audit event (insert only: audit_event stays append-only).
Old source_version rows are left as they are; Source reads type 'BROWSER' and 'requires_js' from
them and maps them the same way.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-26 10:00:00
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACTOR = "migration-0010"


def upgrade() -> None:
    # Plain ADD COLUMN. The allowed values are enforced by the models and services (SQLite cannot
    # add a CHECK constraint to an existing table).
    op.add_column(
        "source",
        sa.Column("fetch_engine", sa.String(11), server_default="http", nullable=False),
    )
    op.add_column("source", sa.Column("wait_for_selector", sa.Text(), nullable=True))
    op.add_column(
        "source",
        sa.Column("wait_timeout_s", sa.Integer(), server_default="15", nullable=False),
    )

    bind = op.get_bind()
    source = sa.table(
        "source",
        sa.column("id", sa.String),
        sa.column("type", sa.String),
        sa.column("fetch_engine", sa.String),
        sa.column("requires_js", sa.Boolean),
    )
    audit = sa.table(
        "audit_event",
        sa.column("id", sa.String),
        sa.column("ts", sa.DateTime(timezone=True)),
        sa.column("actor", sa.String),
        sa.column("action", sa.String),
        sa.column("entity", sa.String),
        sa.column("entity_id", sa.String),
        sa.column("before", sa.JSON(none_as_null=True)),
        sa.column("after", sa.JSON(none_as_null=True)),
    )
    converted = bind.execute(
        sa.select(source.c.id, source.c.type, source.c.requires_js).where(
            sa.or_(source.c.type == "BROWSER", source.c.requires_js.is_(True))
        )
    ).all()
    events = []
    for source_id, kind, requires_js in converted:
        to_browser = kind == "BROWSER"
        events.append(
            {
                "id": f"aud-{uuid.uuid4().hex[:12]}",
                "ts": datetime.now(UTC),
                "actor": ACTOR,
                "action": "source.fetch_engine_migrated",
                "entity": "source",
                "entity_id": source_id,
                "before": {"type": kind, "requires_js": bool(requires_js)},
                "after": {
                    "type": "HTML_LIST" if to_browser else kind,
                    "fetch_engine": "browser" if to_browser else "http",
                    "requires_js": False,
                },
            }
        )
    bind.execute(
        source.update()
        .where(source.c.type == "BROWSER")
        .values(type="HTML_LIST", fetch_engine="browser")
    )
    bind.execute(source.update().values(requires_js=False))
    if events:
        bind.execute(audit.insert(), events)


def downgrade() -> None:
    bind = op.get_bind()
    source = sa.table(
        "source",
        sa.column("type", sa.String),
        sa.column("fetch_engine", sa.String),
        sa.column("requires_js", sa.Boolean),
    )
    bind.execute(
        source.update()
        .where(source.c.fetch_engine == "browser")
        .values(type="BROWSER", requires_js=True)
    )
    with op.batch_alter_table("source", schema=None) as batch_op:
        batch_op.drop_column("wait_timeout_s")
        batch_op.drop_column("wait_for_selector")
        batch_op.drop_column("fetch_engine")
