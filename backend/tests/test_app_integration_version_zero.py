from __future__ import annotations

from pathlib import Path
from threading import Event

import pytest
from app.config import Settings
from app.engines.service import EngineService
from app.main import create_app
from app.models.catalogue import ModelCatalogue
from app.process.manager import ProcessManager
from app.profiles.service import ProfileService
from fastapi.testclient import TestClient


async def wait_for_startup_scan(app) -> None:
    await app.state.runtime.startup_task


@pytest.mark.version_zero
def test_lifespan_composes_one_runtime_and_runs_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # spec: version-zero
    calls: list[str] = []

    async def fake_migrate(settings: Settings, paths: object) -> None:
        del settings, paths
        calls.append("migrate")

    def fake_engine_scan(self: EngineService) -> None:
        del self
        calls.append("engines")

    def fake_model_scan(self: ModelCatalogue) -> list[object]:
        del self
        calls.append("models")
        return []

    async def fake_process_refresh(self: ProcessManager):
        del self
        calls.append("processes")
        from app.schemas.instances import InstanceList

        return InstanceList(items=[])

    async def fake_autostart(self: ProfileService) -> list[object]:
        del self
        calls.append("autostart")
        return []

    monkeypatch.setattr("app.main.migrate_database", fake_migrate)
    monkeypatch.setattr(EngineService, "scan", fake_engine_scan)
    monkeypatch.setattr(ModelCatalogue, "scan", fake_model_scan)
    monkeypatch.setattr(ProcessManager, "refresh", fake_process_refresh)
    monkeypatch.setattr(ProfileService, "autostart_once", fake_autostart)

    settings = Settings(data_root=tmp_path / "data", host="127.0.0.1", port=8080)
    app = create_app(settings, frontend_dist=tmp_path / "missing-dist")
    with TestClient(app) as client:
        runtime = app.state.runtime
        assert runtime.engine_service is app.state.engine_service
        assert runtime.model_catalogue is app.state.model_catalogue
        assert runtime.profile_service is app.state.profile_service
        assert runtime.process_manager is app.state.process_manager
        client.portal.call(wait_for_startup_scan, app)

    assert calls == ["migrate", "engines", "models", "processes", "autostart"]


@pytest.mark.version_zero
def test_failed_optional_scanners_are_reported_as_degraded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # spec: version-zero
    async def fake_migrate(settings: Settings, paths: object) -> None:
        del settings, paths

    def fail_engine_scan(self: EngineService) -> None:
        del self
        raise RuntimeError("engine probe failed")

    def fail_model_scan(self: ModelCatalogue) -> list[object]:
        del self
        raise RuntimeError("model scan failed")

    async def fake_process_refresh(self: ProcessManager):
        del self
        from app.schemas.instances import InstanceList

        return InstanceList(items=[])

    monkeypatch.setattr("app.main.migrate_database", fake_migrate)
    monkeypatch.setattr(EngineService, "scan", fail_engine_scan)
    monkeypatch.setattr(ModelCatalogue, "scan", fail_model_scan)
    monkeypatch.setattr(ProcessManager, "refresh", fake_process_refresh)

    settings = Settings(data_root=tmp_path / "data", host="127.0.0.1", port=8080)
    app = create_app(settings, frontend_dist=tmp_path / "missing-dist")
    with TestClient(app) as client:
        client.portal.call(wait_for_startup_scan, app)
        response = client.get("/api/v1/status").json()["data"]["components"]
        components = {item["name"]: item for item in response}

    assert components["engine_discovery"]["state"] == "degraded"
    assert components["model_catalogue"]["state"] == "degraded"


@pytest.mark.version_zero
def test_slow_startup_scan_does_not_block_health_or_trigger_another_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, release = Event(), Event()
    calls: list[str] = []

    def slow_scan(self: ModelCatalogue) -> list[object]:
        calls.append("scan")
        entered.set()
        assert release.wait(timeout=10), "test did not release the scanner"
        self._items = []
        return []

    monkeypatch.setattr(EngineService, "scan", lambda self: None)
    monkeypatch.setattr(ModelCatalogue, "scan", slow_scan)
    settings = Settings(data_root=tmp_path / "data", host="127.0.0.1", port=8080)
    app = create_app(settings, frontend_dist=tmp_path / "missing-dist")

    with TestClient(app) as client:
        try:
            assert entered.wait(timeout=5)
            assert not app.state.runtime.startup_task.done()
            assert client.get("/health").status_code == 200
            assert client.get("/api/v1/status").status_code == 200
            assert app.state.runtime.model_catalogue.items() == []
            assert client.get("/api/v1/models/local").json()["data"]["scanning"] is True
            assert calls == ["scan"]
        finally:
            release.set()
            client.portal.call(wait_for_startup_scan, app)


@pytest.mark.version_zero
def test_frontend_fallback_serves_assets_and_preserves_api_prefixes(tmp_path: Path) -> None:
    # spec: version-zero
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>neural-deck</html>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log('ok')", encoding="utf-8")
    settings = Settings(data_root=tmp_path / "data", host="127.0.0.1", port=8080)
    app = create_app(settings, frontend_dist=dist)

    with TestClient(app) as client:
        assert client.get("/").text == "<html>neural-deck</html>"
        assert client.get("/profiles/dashboard").text == "<html>neural-deck</html>"
        assert client.get("/assets/app.js").text == "console.log('ok')"
        unknown_api = client.get("/api/v1/unknown")
        assert unknown_api.status_code == 404
        assert unknown_api.json()["error"]["code"] == "not_found"
        assert client.get("/v1/unknown").status_code == 404
        assert client.get("/mcp/").headers["content-type"].startswith("application/json")


@pytest.mark.version_zero
def test_composed_services_and_mcp_mount_are_available_before_spa_fallback(
    tmp_path: Path,
) -> None:
    # spec: version-zero
    settings = Settings(data_root=tmp_path / "data", host="127.0.0.1", port=8080)
    app = create_app(settings, frontend_dist=tmp_path / "missing-dist")

    with TestClient(app) as client:
        runtime = app.state.runtime
        assert app.state.journal_service is runtime.journal_service
        assert app.state.proxy_service is runtime.proxy_service
        assert app.state.metrics_service is runtime.metrics_service

        assert client.get("/v1/models").status_code == 200
        assert client.get("/api/v1/token-stats").status_code == 200

        api_paths = app.openapi()["paths"]
        assert "/api/v1/instances/{instance_id}/metrics" in api_paths
        assert "/api/v1/token-stats" in api_paths
        assert any(
            getattr(route, "path", None) == "/mcp" and type(route).__name__ == "Mount"
            for route in app.routes
        )

        initialize = client.post(
            "/mcp/",
            headers={"accept": "application/json, text/event-stream"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "integration-test", "version": "1"},
                },
            },
        )
        assert initialize.status_code == 200
        assert initialize.json()["result"]["serverInfo"]["name"] == "Neural Deck"

        initialize_without_slash = client.post(
            "/mcp",
            headers={"accept": "application/json, text/event-stream"},
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "integration-test", "version": "1"},
                },
            },
        )
        assert initialize_without_slash.status_code == 200
        assert initialize_without_slash.json()["result"]["serverInfo"]["name"] == "Neural Deck"


@pytest.mark.asyncio
@pytest.mark.parametrize("autostart_fails", [False, True])
async def test_background_catalogue_scan_runs_after_core_startup(autostart_fails):
    from types import SimpleNamespace
    from app.main import _scan_startup

    calls = []
    diagnostics = []

    async def refresh():
        calls.append("processes")

    async def autostart():
        calls.append("autostart")
        if autostart_fails:
            raise RuntimeError("test autostart failure")

    runtime = SimpleNamespace(
        engine_service=SimpleNamespace(scan=lambda: calls.append("engines")),
        process_manager=SimpleNamespace(refresh=refresh, discovery_error=None),
        profile_service=SimpleNamespace(autostart_once=autostart, autostart_issues=[]),
        model_catalogue=SimpleNamespace(scan=lambda: calls.append("catalogue")),
        add_diagnostic=lambda *args: diagnostics.append(args),
    )
    await _scan_startup(runtime)
    assert calls == ["engines", "processes", "autostart", "catalogue"]
    assert bool(diagnostics) is autostart_fails
