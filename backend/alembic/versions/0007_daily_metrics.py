"""Дневной архив и исходные распределения метрик."""

import sqlalchemy as sa
from alembic import op

revision = "0007_daily_metrics"
down_revision = "0006_model_runs"
branch_labels = None
depends_on = None


def upgrade():
    # Checkpoints use sample IDs: SQLite must never reuse IDs after pruning.
    with op.batch_alter_table(
        "metric_samples", recreate="always", table_kwargs={"sqlite_autoincrement": True}
    ) as batch:
        batch.add_column(sa.Column("distributions", sa.JSON(), nullable=True))
    for table in ("metric_daily", "metric_concurrency_daily"):
        columns = [
            sa.Column("day", sa.String(10), primary_key=True),
            sa.Column("profile_name", sa.String(256), primary_key=True),
            sa.Column("model_name", sa.String(512), primary_key=True),
            sa.Column("engine_id", sa.String(80), primary_key=True),
            sa.Column("statistics", sa.JSON(), nullable=False),
        ]
        if table == "metric_daily":
            columns.append(sa.Column("distributions", sa.JSON(), nullable=False))
        else:
            columns.append(sa.Column("requests", sa.Integer(), primary_key=True))
        op.create_table(table, *columns)
    op.create_table(
        "metric_archive_checkpoint",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("last_sample_id", sa.Integer(), nullable=False),
        sa.Column("baselines", sa.JSON(), nullable=False),
    )


def downgrade():
    op.drop_table("metric_archive_checkpoint")
    op.drop_table("metric_concurrency_daily")
    op.drop_table("metric_daily")
    op.drop_column("metric_samples", "distributions")
