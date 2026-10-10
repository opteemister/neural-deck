"""Добавить постоянную идентичность профиля в историю и журнал."""

import sqlalchemy as sa
from alembic import op

revision = "0010_profile_ids"
down_revision = "0009_profile_statistics"
branch_labels = None
depends_on = None


def upgrade():
    for table in (
        "request_journal",
        "metric_samples",
        "metric_launch_extrema",
        "profile_statistics",
        "metric_daily",
        "metric_concurrency_daily",
    ):
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("profile_id", sa.String(36), nullable=True))
            batch.create_index(f"ix_{table}_profile_id", ["profile_id"])


def downgrade():
    for table in (
        "metric_concurrency_daily",
        "metric_daily",
        "profile_statistics",
        "metric_launch_extrema",
        "metric_samples",
        "request_journal",
    ):
        with op.batch_alter_table(table) as batch:
            batch.drop_index(f"ix_{table}_profile_id")
            batch.drop_column("profile_id")
