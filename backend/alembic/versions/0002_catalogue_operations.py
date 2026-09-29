"""Persist operation and local model catalogue records."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_catalogue_operations"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "operations",
        sa.Column("id", sa.String(length=80), nullable=False),
        sa.Column("kind", sa.String(length=80), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("target_id", sa.String(length=512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stdout", sa.Text(), nullable=False),
        sa.Column("stderr", sa.Text(), nullable=False),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("resolved_packages", sa.JSON(), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_operations_target_id", "operations", ["target_id"])
    op.create_table(
        "model_artifacts",
        sa.Column("id", sa.String(length=80), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("format", sa.String(length=80), nullable=False),
        sa.Column("compatibility", sa.String(length=80), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("hf_status", sa.String(length=20), nullable=False),
        sa.Column("hf_repo_id", sa.String(length=256), nullable=True),
        sa.Column("hf_revision", sa.String(length=128), nullable=True),
        sa.Column("scanned_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("path"),
    )


def downgrade() -> None:
    op.drop_table("model_artifacts")
    op.drop_index("ix_operations_target_id", table_name="operations")
    op.drop_table("operations")
