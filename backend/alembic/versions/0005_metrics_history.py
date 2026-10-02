"""Engine history, API clients and per-request performance."""

import sqlalchemy as sa
from alembic import op

revision = "0005_metrics_history"
down_revision = "0004_request_journal_correlation_id"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "api_clients",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("api_key", sa.Text(), unique=True),
        sa.Column("anonymous_ip", sa.String(64), unique=True),
        sa.Column("ip_addresses", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sqlite_autoincrement=True,
    )
    op.add_column("request_journal", sa.Column("pid", sa.Integer, nullable=True))
    op.add_column("request_journal", sa.Column("client_id", sa.Integer, nullable=True))
    op.add_column("request_journal", sa.Column("client_ip", sa.String(64), nullable=True))
    op.add_column("request_journal", sa.Column("cached_input_tokens", sa.Integer, nullable=True))
    op.add_column(
        "request_journal", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "request_journal", sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "request_journal", sa.Column("usage_received_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("request_journal", sa.Column("usage_source", sa.String(80), nullable=True))
    op.add_column("request_journal", sa.Column("metric_meta", sa.JSON, nullable=True))
    op.add_column("request_journal", sa.Column("ttft_ms", sa.Float, nullable=True))
    op.add_column("request_journal", sa.Column("queue_time_ms", sa.Float, nullable=True))
    op.add_column("request_journal", sa.Column("prefill_time_ms", sa.Float, nullable=True))
    op.add_column("request_journal", sa.Column("generation_time_ms", sa.Float, nullable=True))
    op.add_column("request_journal", sa.Column("prefill_tps", sa.Float, nullable=True))
    op.add_column("request_journal", sa.Column("decode_tps", sa.Float, nullable=True))
    op.add_column("request_journal", sa.Column("prefix_cache_hit_percent", sa.Float, nullable=True))
    op.add_column("request_journal", sa.Column("spec_acceptance_percent", sa.Float, nullable=True))
    op.create_index("ix_journal_client_time", "request_journal", ["client_id", "started_at"])
    op.create_index(
        "ix_journal_profile_pid_time", "request_journal", ["profile_name", "pid", "started_at"]
    )
    op.create_table(
        "metric_samples",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("profile_name", sa.String(256)),
        sa.Column("pid", sa.Integer(), nullable=False),
        sa.Column("engine_id", sa.String(80), nullable=False),
        sa.Column("model_name", sa.String(512)),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metric_meta", sa.JSON(), nullable=False),
        sa.Column("decode_tps", sa.Float()),
        sa.Column("ttft_mean_ms", sa.Float()),
        sa.Column("prefill_tps", sa.Float()),
        sa.Column("active_requests", sa.Float()),
        sa.Column("vram_bytes", sa.Float()),
        sa.Column("power_watts", sa.Float()),
        sa.Column("kv_cache_percent", sa.Float()),
        sa.Column("queue_requests", sa.Float()),
        sa.Column("uptime_s", sa.Float()),
        sa.Column("prompt_tokens_total", sa.Float()),
        sa.Column("completion_tokens_total", sa.Float()),
        sa.Column("inflight_completion_tokens", sa.Float()),
        sa.Column("completed_requests", sa.Float()),
        sa.Column("energy_kwh", sa.Float()),
        sa.Column("queue_time_mean_ms", sa.Float()),
        sa.Column("cached_prompt_tokens_total", sa.Float()),
        sa.Column("prefix_cache_hit_percent", sa.Float()),
        sa.Column("prefix_cache_hit_mean_percent", sa.Float()),
        sa.Column("spec_acceptance_percent", sa.Float()),
    )
    op.create_index(
        "ix_metrics_profile_pid_time", "metric_samples", ["profile_name", "pid", "observed_at"]
    )
    op.create_index("ix_metrics_profile_time", "metric_samples", ["profile_name", "observed_at"])
    op.create_index("ix_metrics_time", "metric_samples", ["observed_at"])
    op.create_table(
        "metric_launch_extrema",
        sa.Column("profile_name", sa.String(256), primary_key=True),
        sa.Column("pid", sa.Integer(), primary_key=True),
        sa.Column("values", sa.JSON(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("metric_launch_extrema")
    op.drop_table("metric_samples")
    op.drop_index("ix_journal_client_time", table_name="request_journal")
    op.drop_index("ix_journal_profile_pid_time", table_name="request_journal")
    with op.batch_alter_table("request_journal") as batch:
        batch.drop_column("spec_acceptance_percent")
        batch.drop_column("prefix_cache_hit_percent")
        batch.drop_column("decode_tps")
        batch.drop_column("prefill_tps")
        batch.drop_column("generation_time_ms")
        batch.drop_column("prefill_time_ms")
        batch.drop_column("queue_time_ms")
        batch.drop_column("ttft_ms")
        batch.drop_column("metric_meta")
        batch.drop_column("usage_source")
        batch.drop_column("usage_received_at")
        batch.drop_column("finished_at")
        batch.drop_column("started_at")
        batch.drop_column("cached_input_tokens")
        batch.drop_column("client_ip")
        batch.drop_column("client_id")
        batch.drop_column("pid")
    op.drop_table("api_clients")
