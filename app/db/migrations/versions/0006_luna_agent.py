"""Luna agent: ai_extraction (append-only), agent_flag, agent_run

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-23 22:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.db.types import TZDateTime

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _enum(name: str, *values: str) -> sa.Enum:
    return sa.Enum(
        *values,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=max(len(v) for v in values) + 4,
    )


def upgrade() -> None:
    op.create_table(
        "ai_extraction",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("item_id", sa.String(length=36), nullable=False),
        sa.Column(
            "task", _enum("ai_extraction_task", "SCREEN", "EXTRACT", "SWEEP"), nullable=False
        ),
        sa.Column("fields", sa.JSON(), nullable=False),
        sa.Column(
            "nbfc_relevance",
            _enum("ai_extraction_relevance", "HIGH", "MEDIUM", "LOW", "NONE"),
            nullable=True,
        ),
        sa.Column("relevance_score", sa.Float(), nullable=True),
        sa.Column("suggested_treatment", sa.String(length=40), nullable=True),
        sa.Column("suggested_sme_function", sa.String(length=60), nullable=True),
        sa.Column("model", sa.String(length=80), nullable=False),
        sa.Column("deployment", sa.String(length=120), nullable=True),
        sa.Column("prompt_version", sa.String(length=40), nullable=False),
        sa.Column("guidance_version", sa.Integer(), nullable=True),
        sa.Column("created_at", TZDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["regulatory_item.id"],
            name=op.f("fk_ai_extraction_item_id_regulatory_item"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_extraction")),
    )
    with op.batch_alter_table("ai_extraction", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_ai_extraction_item_id"), ["item_id"], unique=False)

    op.create_table(
        "agent_flag",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("source_id", sa.String(length=36), nullable=True),
        sa.Column("snapshot_id", sa.String(length=36), nullable=True),
        sa.Column("item_id", sa.String(length=36), nullable=True),
        sa.Column(
            "type", _enum("agent_flag_type", "POSSIBLE_MISS", "DISAGREEMENT"), nullable=False
        ),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "status", _enum("agent_flag_status", "OPEN", "RESOLVED", "FALSE_ALARM"), nullable=False
        ),
        sa.Column("resolved_by", sa.String(length=100), nullable=True),
        sa.Column("resolved_at", TZDateTime(), nullable=True),
        sa.Column("created_at", TZDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"], ["crawl_run.id"], name=op.f("fk_agent_flag_run_id_crawl_run")
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["source.id"], name=op.f("fk_agent_flag_source_id_source")
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"], ["snapshot.id"], name=op.f("fk_agent_flag_snapshot_id_snapshot")
        ),
        sa.ForeignKeyConstraint(
            ["item_id"], ["regulatory_item.id"], name=op.f("fk_agent_flag_item_id_regulatory_item")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_flag")),
    )
    with op.batch_alter_table("agent_flag", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_agent_flag_run_id"), ["run_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_agent_flag_source_id"), ["source_id"], unique=False)

    op.create_table(
        "agent_run",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("task", _enum("agent_run_task", "SCREEN", "EXTRACT", "SWEEP"), nullable=False),
        sa.Column("item_id", sa.String(length=36), nullable=True),
        sa.Column("snapshot_id", sa.String(length=36), nullable=True),
        sa.Column("model", sa.String(length=80), nullable=False),
        sa.Column("deployment", sa.String(length=120), nullable=True),
        sa.Column("prompt_version", sa.String(length=40), nullable=False),
        sa.Column("guidance_version", sa.Integer(), nullable=True),
        sa.Column("tokens_in", sa.Integer(), nullable=False),
        sa.Column("tokens_out", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("cost_inr", sa.Float(), nullable=False),
        sa.Column(
            "status",
            _enum("agent_run_status", "OK", "INVALID_JSON", "QUOTE_REJECTED", "ERROR", "BUDGET"),
            nullable=False,
        ),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("created_at", TZDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"], ["crawl_run.id"], name=op.f("fk_agent_run_run_id_crawl_run")
        ),
        sa.ForeignKeyConstraint(
            ["item_id"], ["regulatory_item.id"], name=op.f("fk_agent_run_item_id_regulatory_item")
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"], ["snapshot.id"], name=op.f("fk_agent_run_snapshot_id_snapshot")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_run")),
    )
    with op.batch_alter_table("agent_run", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_agent_run_run_id"), ["run_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_agent_run_created_at"), ["created_at"], unique=False)

    # ai_extraction is evidence: the database refuses UPDATE and DELETE (rule 7).
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        for action in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER trg_ai_extraction_no_{action.lower()} "
                f"BEFORE {action} ON ai_extraction "
                f"BEGIN SELECT RAISE(ABORT, "
                f"'ai_extraction is append-only: {action} is not allowed'); END"
            )
    elif dialect == "postgresql":  # the function was created by migration 0001
        for action in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER trg_ai_extraction_no_{action.lower()} "
                f"BEFORE {action} ON ai_extraction "
                "FOR EACH ROW EXECUTE FUNCTION reject_append_only_change()"
            )
        op.execute(
            "CREATE TRIGGER trg_ai_extraction_no_truncate BEFORE TRUNCATE ON ai_extraction "
            "FOR EACH STATEMENT EXECUTE FUNCTION reject_append_only_change()"
        )
    else:
        raise NotImplementedError(f"No append-only trigger SQL for {dialect!r}")


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    for action in ("update", "delete", "truncate"):
        if dialect == "sqlite" and action != "truncate":
            op.execute(f"DROP TRIGGER IF EXISTS trg_ai_extraction_no_{action}")
        elif dialect == "postgresql":
            op.execute(f"DROP TRIGGER IF EXISTS trg_ai_extraction_no_{action} ON ai_extraction")
    op.drop_table("agent_run")
    op.drop_table("agent_flag")
    op.drop_table("ai_extraction")
