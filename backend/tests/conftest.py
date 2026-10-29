from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from app.config import Settings
from app.main import create_app


@pytest.fixture
def data_root(tmp_path: Path) -> Path:
    return tmp_path / "persistent"


@pytest.fixture
def settings(data_root: Path) -> Settings:
    return Settings(data_root=data_root, host="127.0.0.1", port=3000, openai_port=8080)


@pytest.fixture
def app(settings: Settings):
    return create_app(settings)


@pytest.fixture
def client(app) -> Iterator:
    from fastapi.testclient import TestClient

    with TestClient(app) as test_client:
        yield test_client
