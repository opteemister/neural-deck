"""Общая таблица сохранённой статистики профиля."""

import sqlalchemy as sa
from alembic import op

revision = "0009_profile_statistics"
down_revision = "0008_profile_last_vram"
branch_labels = None
depends_on = None


def upgrade():
    op.rename_table("profile_last_vram", "profile_statistics")
    with op.batch_alter_table("profile_statistics") as batch:
        batch.alter_column("value_bytes", new_column_name="vram_bytes", existing_type=sa.Float())
        batch.alter_column(
            "observed_at", new_column_name="vram_observed_at",
            existing_type=sa.DateTime(timezone=True),
        )


def downgrade():
    with op.batch_alter_table("profile_statistics") as batch:
        batch.alter_column("vram_bytes", new_column_name="value_bytes", existing_type=sa.Float())
        batch.alter_column(
            "vram_observed_at", new_column_name="observed_at",
            existing_type=sa.DateTime(timezone=True),
        )
    op.rename_table("profile_statistics", "profile_last_vram")
