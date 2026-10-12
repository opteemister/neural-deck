"""Последнее измерение VRAM профиля независимо от срока хранения сырых метрик."""

import sqlalchemy as sa
from alembic import op

revision = "0008_profile_last_vram"
down_revision = "0007_daily_metrics"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "profile_last_vram",
        sa.Column("profile_name", sa.String(256), primary_key=True),
        sa.Column("value_bytes", sa.Float(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.execute("""
        INSERT INTO profile_last_vram (profile_name, value_bytes, observed_at)
        SELECT profile_name, vram_bytes, observed_at FROM (
            SELECT profile_name, vram_bytes, observed_at,
                   ROW_NUMBER() OVER (
                       PARTITION BY profile_name ORDER BY observed_at DESC, id DESC
                   ) AS row_number
            FROM metric_samples
            WHERE profile_name IS NOT NULL AND vram_bytes > 0
                  AND json_extract(metric_meta, '$.vram_bytes.state') = 'ok'
        ) WHERE row_number = 1
    """)


def downgrade():
    op.drop_table("profile_last_vram")
