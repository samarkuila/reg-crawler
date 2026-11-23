"""initial schema

Every table in docs/architecture.md section 4, plus the internal reference counter, and the
triggers that make snapshot, document and audit_event append-only (rule 7).

Revision ID: 0001
Revises:
Create Date: 2026-11-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

APPEND_ONLY = ("snapshot", "document", "audit_event")

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _create_append_only_triggers() -> None:
    """The database itself refuses UPDATE and DELETE on evidence tables.

    The same SQL for PostgreSQL is in docs/postgres-migration.md. A database we have no trigger
    SQL for must not be migrated silently without this protection, so anything else fails.
    """
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        for table in APPEND_ONLY:
            for action in ("UPDATE", "DELETE"):
                op.execute(
                    f"CREATE TRIGGER trg_{table}_no_{action.lower()} BEFORE {action} ON {table} "
                    f"BEGIN SELECT RAISE(ABORT, '{table} is append-only: "
                    f"{action} is not allowed'); END"
                )
    elif dialect == "postgresql":
        op.execute(
            "CREATE FUNCTION reject_append_only_change() RETURNS trigger AS $$ BEGIN "
            "RAISE EXCEPTION '% is append-only: % is not allowed', TG_TABLE_NAME, TG_OP "
            "USING ERRCODE = 'integrity_constraint_violation'; END; $$ LANGUAGE plpgsql"
        )
        for table in APPEND_ONLY:
            for action in ("UPDATE", "DELETE"):
                op.execute(
                    f"CREATE TRIGGER trg_{table}_no_{action.lower()} BEFORE {action} ON {table} "
                    "FOR EACH ROW EXECUTE FUNCTION reject_append_only_change()"
                )
            op.execute(
                f"CREATE TRIGGER trg_{table}_no_truncate BEFORE TRUNCATE ON {table} "
                "FOR EACH STATEMENT EXECUTE FUNCTION reject_append_only_change()"
            )
    else:
        raise NotImplementedError(f"No append-only trigger SQL for {dialect!r}")


def _drop_append_only_triggers() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        for table in APPEND_ONLY:
            for action in ("update", "delete"):
                op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_no_{action}")
    elif dialect == "postgresql":
        for table in APPEND_ONLY:
            for action in ("update", "delete", "truncate"):
                op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_no_{action} ON {table}")
        op.execute("DROP FUNCTION IF EXISTS reject_append_only_change()")


def upgrade() -> None:
    op.create_table(
        "app_user",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("email", sa.String(length=200), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "viewer",
                "reviewer",
                "approver",
                "admin",
                name="app_user_role",
                native_enum=False,
                create_constraint=True,
                length=12,
            ),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_app_user")),
        sa.UniqueConstraint("email", name=op.f("uq_app_user_email")),
    )
    op.create_table(
        "audit_event",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor", sa.String(length=100), nullable=False),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("entity", sa.String(length=100), nullable=False),
        sa.Column("entity_id", sa.String(length=100), nullable=False),
        sa.Column("before", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("after", sa.JSON(none_as_null=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_event")),
    )
    with op.batch_alter_table("audit_event", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_audit_event_actor"), ["actor"], unique=False)
        batch_op.create_index(
            "ix_audit_event_entity_entity_id", ["entity", "entity_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_audit_event_ts"), ["ts"], unique=False)

    op.create_table(
        "crawl_run",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("cycle_date", sa.Date(), nullable=False),
        sa.Column(
            "cycle",
            sa.Enum(
                "MORNING",
                "EOD",
                "ADHOC",
                "URGENT",
                name="crawl_run_cycle",
                native_enum=False,
                create_constraint=True,
                length=11,
            ),
            nullable=False,
        ),
        sa.Column("triggered_by", sa.String(length=100), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "RUNNING",
                "COMPLETED",
                "PARTIAL",
                name="crawl_run_status",
                native_enum=False,
                create_constraint=True,
                length=13,
            ),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_crawl_run")),
    )
    with op.batch_alter_table("crawl_run", schema=None) as batch_op:
        batch_op.create_index("ix_crawl_run_cycle_date", ["cycle_date"], unique=False)

    op.create_table(
        "daily_update",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("cycle_date", sa.Date(), nullable=False),
        sa.Column(
            "cycle",
            sa.Enum(
                "MORNING",
                "EOD",
                "ADHOC",
                "URGENT",
                name="daily_update_cycle",
                native_enum=False,
                create_constraint=True,
                length=11,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "DRAFT",
                "APPROVED",
                "EXPORTED",
                name="daily_update_status",
                native_enum=False,
                create_constraint=True,
                length=12,
            ),
            nullable=False,
        ),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("html", sa.Text(), nullable=False),
        sa.Column("sources_ok", sa.JSON(), nullable=False),
        sa.Column("sources_failed", sa.JSON(), nullable=False),
        sa.Column("approved_by", sa.String(length=100), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("prepared_by", sa.JSON(), nullable=False),
        sa.Column("exported_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_daily_update")),
        sa.UniqueConstraint("cycle_date", "cycle", name=op.f("uq_daily_update_cycle_date")),
    )
    op.create_table(
        "email_template",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("subject_pattern", sa.Text(), nullable=False),
        sa.Column("greeting", sa.Text(), nullable=False),
        sa.Column("intro", sa.Text(), nullable=False),
        sa.Column("disclaimer", sa.Text(), nullable=False),
        sa.Column("no_update_text", sa.Text(), nullable=False),
        sa.Column("sign_off", sa.Text(), nullable=False),
        sa.Column("sender_name", sa.String(length=100), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_email_template")),
    )
    op.create_table(
        "internal_ref_counter",
        sa.Column("year", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("last_value", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("year", name=op.f("pk_internal_ref_counter")),
    )
    op.create_table(
        "recipient",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("email", sa.String(length=200), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "TO",
                "CC",
                name="recipient_kind",
                native_enum=False,
                create_constraint=True,
                length=6,
            ),
            nullable=False,
        ),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_recipient")),
    )
    op.create_table(
        "setting",
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("updated_by", sa.String(length=100), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("pending", sa.JSON(none_as_null=True), nullable=True),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_setting")),
    )
    op.create_table(
        "sme_function",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("head_name", sa.String(length=100), nullable=True),
        sa.Column("head_email", sa.String(length=200), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sme_function")),
    )
    op.create_table(
        "source",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("authority", sa.String(length=100), nullable=False),
        sa.Column("page_name", sa.String(length=200), nullable=False),
        sa.Column(
            "type",
            sa.Enum(
                "RSS",
                "HTML_LIST",
                "STRUCTURED_DATA",
                "BROWSER",
                name="source_type",
                native_enum=False,
                create_constraint=True,
                length=19,
            ),
            nullable=False,
        ),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("rss_url", sa.Text(), nullable=True),
        sa.Column("adapter_config", sa.JSON(), nullable=False),
        sa.Column("include_keywords", sa.JSON(), nullable=False),
        sa.Column("exclude_keywords", sa.JSON(), nullable=False),
        sa.Column(
            "priority",
            sa.Enum(
                "NORMAL",
                "URGENT",
                name="source_priority",
                native_enum=False,
                create_constraint=True,
                length=10,
            ),
            nullable=False,
        ),
        sa.Column("requires_js", sa.Boolean(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("planned", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source")),
    )
    op.create_table(
        "regulatory_item",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("internal_ref", sa.String(length=24), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("ref_no", sa.String(length=200), nullable=True),
        sa.Column("displayed_date", sa.Date(), nullable=True),
        sa.Column(
            "channel",
            sa.Enum(
                "RSS",
                "HTML",
                "BOTH",
                name="regulatory_item_channel",
                native_enum=False,
                create_constraint=True,
                length=8,
            ),
            nullable=False,
        ),
        sa.Column("channel_mismatch", sa.Boolean(), nullable=False),
        sa.Column("relevance_score", sa.Integer(), nullable=False),
        sa.Column("relevance_reason", sa.Text(), nullable=True),
        sa.Column(
            "priority",
            sa.Enum(
                "NORMAL",
                "URGENT",
                name="regulatory_item_priority",
                native_enum=False,
                create_constraint=True,
                length=10,
            ),
            nullable=False,
        ),
        sa.Column(
            "state",
            sa.Enum(
                "DETECTED",
                "NEEDS_MANUAL_FETCH",
                "ENRICHED",
                "IN_REVIEW",
                "DECIDED",
                "PUBLISHED",
                name="regulatory_item_state",
                native_enum=False,
                create_constraint=True,
                length=22,
            ),
            nullable=False,
        ),
        sa.Column("first_seen_run_id", sa.String(length=36), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["first_seen_run_id"],
            ["crawl_run.id"],
            name=op.f("fk_regulatory_item_first_seen_run_id_crawl_run"),
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["source.id"], name=op.f("fk_regulatory_item_source_id_source")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_regulatory_item")),
        sa.UniqueConstraint("fingerprint", name=op.f("uq_regulatory_item_fingerprint")),
        sa.UniqueConstraint("internal_ref", name=op.f("uq_regulatory_item_internal_ref")),
    )
    with op.batch_alter_table("regulatory_item", schema=None) as batch_op:
        batch_op.create_index(
            "ix_regulatory_item_source_id_first_seen_at",
            ["source_id", "first_seen_at"],
            unique=False,
        )
        batch_op.create_index("ix_regulatory_item_state", ["state"], unique=False)

    op.create_table(
        "snapshot",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=False),
        sa.Column(
            "channel",
            sa.Enum(
                "RSS",
                "HTML",
                "XML",
                name="snapshot_channel",
                native_enum=False,
                create_constraint=True,
                length=8,
            ),
            nullable=False,
        ),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_id"], ["source.id"], name=op.f("fk_snapshot_source_id_source")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_snapshot")),
    )
    with op.batch_alter_table("snapshot", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_snapshot_sha256"), ["sha256"], unique=False)
        batch_op.create_index(batch_op.f("ix_snapshot_source_id"), ["source_id"], unique=False)

    op.create_table(
        "source_version",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("before", sa.JSON(), nullable=False),
        sa.Column("after", sa.JSON(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "APPROVED",
                "REJECTED",
                name="source_version_status",
                native_enum=False,
                create_constraint=True,
                length=12,
            ),
            nullable=False,
        ),
        sa.Column("requested_by", sa.String(length=100), nullable=False),
        sa.Column("approved_by", sa.String(length=100), nullable=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reject_reason", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["source_id"], ["source.id"], name=op.f("fk_source_version_source_id_source")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source_version")),
        sa.UniqueConstraint("source_id", "version", name=op.f("uq_source_version_source_id")),
    )
    with op.batch_alter_table("source_version", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_source_version_source_id"), ["source_id"], unique=False
        )

    op.create_table(
        "document",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("item_id", sa.String(length=36), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("path", sa.Text(), nullable=True),
        sa.Column("mime", sa.String(length=100), nullable=True),
        sa.Column("text_path", sa.Text(), nullable=True),
        sa.Column(
            "fetch_status",
            sa.Enum(
                "OK",
                "BLOCKED",
                "FAILED",
                "MANUAL",
                name="document_status",
                native_enum=False,
                create_constraint=True,
                length=11,
            ),
            nullable=False,
        ),
        sa.Column("uploaded_by", sa.String(length=100), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("filename", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(
            ["item_id"], ["regulatory_item.id"], name=op.f("fk_document_item_id_regulatory_item")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document")),
    )
    with op.batch_alter_table("document", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_document_item_id"), ["item_id"], unique=False)

    op.create_table(
        "item_observation",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("item_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column(
            "channel",
            sa.Enum(
                "RSS",
                "HTML",
                "XML",
                name="item_observation_channel",
                native_enum=False,
                create_constraint=True,
                length=8,
            ),
            nullable=False,
        ),
        sa.Column(
            "change_type",
            sa.Enum(
                "NEW",
                "MODIFIED",
                "BACKDATED",
                "REAPPEARED",
                name="item_observation_change_type",
                native_enum=False,
                create_constraint=True,
                length=14,
            ),
            nullable=False,
        ),
        sa.Column("prior_hash", sa.String(length=64), nullable=True),
        sa.Column("current_hash", sa.String(length=64), nullable=False),
        sa.Column("snapshot_id", sa.String(length=36), nullable=True),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["regulatory_item.id"],
            name=op.f("fk_item_observation_item_id_regulatory_item"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["crawl_run.id"], name=op.f("fk_item_observation_run_id_crawl_run")
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"], ["snapshot.id"], name=op.f("fk_item_observation_snapshot_id_snapshot")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_item_observation")),
    )
    with op.batch_alter_table("item_observation", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_item_observation_item_id"), ["item_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_item_observation_run_id"), ["run_id"], unique=False)

    op.create_table(
        "review_decision",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("item_id", sa.String(length=36), nullable=False),
        sa.Column("reviewer", sa.String(length=100), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "treatment",
            sa.Enum(
                "INFO_ONLY",
                "OTHER_FUNCTION",
                "FUTURE_POTENTIAL",
                "IMPLEMENTATION_REQUIRED",
                "NOT_RELEVANT",
                name="review_decision_treatment",
                native_enum=False,
                create_constraint=True,
                length=27,
            ),
            nullable=True,
        ),
        sa.Column("include_in_update", sa.Boolean(), nullable=False),
        sa.Column(
            "applicability",
            sa.Enum(
                "YES",
                "NO",
                "TBC",
                name="review_decision_applicability",
                native_enum=False,
                create_constraint=True,
                length=7,
            ),
            nullable=True,
        ),
        sa.Column("ref_no_and_date", sa.Text(), nullable=True),
        sa.Column("authority", sa.String(length=100), nullable=True),
        sa.Column("guideline_title", sa.Text(), nullable=True),
        sa.Column("issue_date", sa.Date(), nullable=True),
        sa.Column("publish_date", sa.Date(), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=True),
        sa.Column(
            "status_colour",
            sa.Enum(
                "NOT_APPLICABLE",
                "ACTION_OVER",
                "IN_PROGRESS",
                "NOT_AS_PER_PLAN",
                "YET_TO_START",
                name="review_decision_status_colour",
                native_enum=False,
                create_constraint=True,
                length=19,
            ),
            nullable=True,
        ),
        sa.Column("impact_text", sa.Text(), nullable=True),
        sa.Column("sme_function", sa.String(length=100), nullable=True),
        sa.Column("dismiss_reason", sa.Text(), nullable=True),
        sa.Column("fields_edited", sa.JSON(), nullable=False),
        sa.Column("is_draft", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["regulatory_item.id"],
            name=op.f("fk_review_decision_item_id_regulatory_item"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_review_decision")),
    )
    with op.batch_alter_table("review_decision", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_review_decision_item_id"), ["item_id"], unique=False)

    op.create_table(
        "source_check",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=False),
        sa.Column(
            "channel",
            sa.Enum(
                "RSS",
                "HTML",
                "XML",
                name="source_check_channel",
                native_enum=False,
                create_constraint=True,
                length=8,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "OK",
                "FAILED",
                "BLOCKED",
                "PARSE_ERROR",
                "ZERO_ITEMS",
                name="source_check_status",
                native_enum=False,
                create_constraint=True,
                length=15,
            ),
            nullable=False,
        ),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("items_found", sa.Integer(), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("snapshot_id", sa.String(length=36), nullable=True),
        sa.ForeignKeyConstraint(
            ["run_id"], ["crawl_run.id"], name=op.f("fk_source_check_run_id_crawl_run")
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"], ["snapshot.id"], name=op.f("fk_source_check_snapshot_id_snapshot")
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["source.id"], name=op.f("fk_source_check_source_id_source")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source_check")),
        sa.UniqueConstraint("run_id", "source_id", "channel", name=op.f("uq_source_check_run_id")),
    )
    with op.batch_alter_table("source_check", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_source_check_run_id"), ["run_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_source_check_source_id"), ["source_id"], unique=False)

    op.create_table(
        "daily_update_item",
        sa.Column("update_id", sa.String(length=36), nullable=False),
        sa.Column("item_id", sa.String(length=36), nullable=False),
        sa.Column("decision_id", sa.String(length=36), nullable=False),
        sa.Column("serial_no", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["decision_id"],
            ["review_decision.id"],
            name=op.f("fk_daily_update_item_decision_id_review_decision"),
        ),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["regulatory_item.id"],
            name=op.f("fk_daily_update_item_item_id_regulatory_item"),
        ),
        sa.ForeignKeyConstraint(
            ["update_id"],
            ["daily_update.id"],
            name=op.f("fk_daily_update_item_update_id_daily_update"),
        ),
        sa.PrimaryKeyConstraint("update_id", "item_id", name=op.f("pk_daily_update_item")),
        sa.UniqueConstraint("update_id", "serial_no", name=op.f("uq_daily_update_item_update_id")),
    )
    op.create_table(
        "manual_check",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source_check_id", sa.String(length=36), nullable=False),
        sa.Column("user", sa.String(length=100), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_check_id"],
            ["source_check.id"],
            name=op.f("fk_manual_check_source_check_id_source_check"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_manual_check")),
        sa.UniqueConstraint("source_check_id", name=op.f("uq_manual_check_source_check_id")),
    )
    _create_append_only_triggers()


def downgrade() -> None:
    _drop_append_only_triggers()
    op.drop_table("manual_check")
    op.drop_table("daily_update_item")
    with op.batch_alter_table("source_check", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_source_check_source_id"))
        batch_op.drop_index(batch_op.f("ix_source_check_run_id"))

    op.drop_table("source_check")
    with op.batch_alter_table("review_decision", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_review_decision_item_id"))

    op.drop_table("review_decision")
    with op.batch_alter_table("item_observation", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_item_observation_run_id"))
        batch_op.drop_index(batch_op.f("ix_item_observation_item_id"))

    op.drop_table("item_observation")
    with op.batch_alter_table("document", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_document_item_id"))

    op.drop_table("document")
    with op.batch_alter_table("source_version", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_source_version_source_id"))

    op.drop_table("source_version")
    with op.batch_alter_table("snapshot", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_snapshot_source_id"))
        batch_op.drop_index(batch_op.f("ix_snapshot_sha256"))

    op.drop_table("snapshot")
    with op.batch_alter_table("regulatory_item", schema=None) as batch_op:
        batch_op.drop_index("ix_regulatory_item_state")
        batch_op.drop_index("ix_regulatory_item_source_id_first_seen_at")

    op.drop_table("regulatory_item")
    op.drop_table("source")
    op.drop_table("sme_function")
    op.drop_table("setting")
    op.drop_table("recipient")
    op.drop_table("internal_ref_counter")
    op.drop_table("email_template")
    op.drop_table("daily_update")
    with op.batch_alter_table("crawl_run", schema=None) as batch_op:
        batch_op.drop_index("ix_crawl_run_cycle_date")

    op.drop_table("crawl_run")
    with op.batch_alter_table("audit_event", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_audit_event_ts"))
        batch_op.drop_index("ix_audit_event_entity_entity_id")
        batch_op.drop_index(batch_op.f("ix_audit_event_actor"))

    op.drop_table("audit_event")
    op.drop_table("app_user")
