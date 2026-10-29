"""Удалить старые тестовые чаты из основной базы без переноса данных."""

from alembic import op

revision = "0011_drop_old_chat_tables"
down_revision = "0010_profile_ids"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS chat_attachments")
    op.execute("DROP TABLE IF EXISTS chat_conversations")


def downgrade() -> None:
    # Старая версия приложения сама создаёт пустые чатовые таблицы при запуске.
    pass
