"""Shared test environment defaults."""

import os

os.environ.setdefault("APP_NAME", "LLM Studio tests")
os.environ.setdefault("APP_ENV", "local")
os.environ.setdefault("HOST", "127.0.0.1")
os.environ.setdefault("PORT", "3000")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("DATA_DIR", "tests/.data")
os.environ.setdefault("MODEL_ENGINES_DIR", "tests/.engines")
os.environ.setdefault("MODEL_SHUTDOWN_TIMEOUT_SECONDS", "30")
os.environ.setdefault("MODEL_LOG_DIR", "tests/.logs")
