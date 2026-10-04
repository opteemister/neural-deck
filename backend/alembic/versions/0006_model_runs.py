"""Только текущие управляемые запуски моделей."""

import sqlalchemy as sa
from alembic import op

revision = "0006_model_runs"
down_revision = "0005_metrics_history"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "model_runs",
        sa.Column("pid", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("process_start_time_ns", sa.BigInteger(), nullable=False),
        sa.Column("snapshot", sa.Text(), nullable=False),
    )


def downgrade():
    op.drop_table("model_runs")
