"""Persist OpenAI proxy request metadata and token observations."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_request_journal_token_stats"
down_revision = "0002_catalogue_operations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "request_journal",
        sa.Column("id", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("route", sa.String(length=120), nullable=False),
        sa.Column("model_name", sa.String(length=512), nullable=False),
        sa.Column("profile_name", sa.String(length=256), nullable=True),
        sa.Column("instance_id", sa.String(length=512), nullable=True),
        sa.Column("engine_id", sa.String(length=80), nullable=True),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Float(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("streaming", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_request_journal_created_at", "request_journal", ["created_at"])
    op.create_index("ix_request_journal_profile_name", "request_journal", ["profile_name"])
    op.create_index("ix_request_journal_instance_id", "request_journal", ["instance_id"])


def downgrade() -> None:
    op.drop_index("ix_request_journal_instance_id", table_name="request_journal")
    op.drop_index("ix_request_journal_profile_name", table_name="request_journal")
    op.drop_index("ix_request_journal_created_at", table_name="request_journal")
    op.drop_table("request_journal")
