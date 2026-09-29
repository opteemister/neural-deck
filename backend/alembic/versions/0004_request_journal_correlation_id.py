"""Separate client request correlation IDs from journal row identity."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_request_journal_correlation_id"
down_revision = "0003_request_journal_token_stats"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ``id`` remains the internal unique primary key.  Older databases used
    # the client value in that column, so preserve it as request_id while new
    # rows use generated internal IDs.
    op.add_column(
        "request_journal",
        sa.Column("request_id", sa.String(length=100), nullable=True),
    )
    op.execute(sa.text("UPDATE request_journal SET request_id = id"))
    with op.batch_alter_table("request_journal") as batch:
        batch.alter_column(
            "request_id",
            existing_type=sa.String(length=100),
            nullable=False,
        )
    op.create_index("ix_request_journal_request_id", "request_journal", ["request_id"])


def downgrade() -> None:
    op.drop_index("ix_request_journal_request_id", table_name="request_journal")
    with op.batch_alter_table("request_journal") as batch:
        batch.drop_column("request_id")
