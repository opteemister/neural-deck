from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.schemas.instances import Instance
from app.schemas.system import InstanceData


@pytest.mark.version_zero
def test_system_endpoint_returns_only_observable_data(client) -> None:
    # spec: version-zero
    response = client.get("/api/v1/system", headers={"X-Request-ID": "req_test"})

    assert response.status_code == 200
    body = response.json()
    assert body["request_id"] == "req_test"
    assert body["data"]["cpu"]["cores"] >= 1
    assert body["data"]["ram"]["total_bytes"] > 0
    assert body["data"]["instances"] == []


def test_system_instance_contract_accepts_runtime_api_metadata() -> None:
    instance = Instance(
        id="managed/ComfyUI/75-1",
        source="managed",
        profile_name="ComfyUI",
        engine_id="comfyui",
        pid=75,
        process_start_time_ns=1,
        identity_verified=True,
        can_stop=True,
        port=8188,
        model_name="comfy",
        types=["image", "video"],
        api_protocol="comfyui",
        api_base_url="http://127.0.0.1:8188",
        ui_url="http://127.0.0.1:8188",
        state="ready",
        observed_at=datetime.now(UTC),
    )

    embedded = InstanceData.model_validate(instance.model_dump())

    assert embedded.types == ["image", "video"]
    assert embedded.api_protocol == "comfyui"


@pytest.mark.version_zero
def test_status_endpoint_checks_sqlite_and_reports_proxy_contract(client) -> None:
    # spec: version-zero
    response = client.get("/api/v1/status")

    assert response.status_code == 200
    body = response.json()["data"]
    components = {item["name"]: item for item in body["components"]}
    assert components["backend"]["state"] == "ok"
    assert components["sqlite"]["state"] == "ok"
    assert components["openai_api"]["state"] == "degraded"
    assert "моделей" in components["openai_api"]["message"]
    assert body["overall"] == "degraded"


@pytest.mark.version_zero
def test_status_refreshes_registry_and_reports_current_model_count(client) -> None:
    runtime = client.app.state.runtime

    async def keep_registry() -> None:
        return None

    runtime.process_manager.refresh = keep_registry  # type: ignore[method-assign]
    runtime.model_registry.register_instance(
        Instance(
            id="external/vllm/999-1",
            source="external",
            engine_id="vllm",
            pid=999,
            process_start_time_ns=1,
            identity_verified=True,
            can_stop=False,
            state="ready",
            observed_at=datetime.now(UTC),
        ),
        upstream_models=["model"],
    )
    response = client.get("/api/v1/status")
    assert response.status_code == 200
    component = next(
        item for item in response.json()["data"]["components"] if item["name"] == "openai_api"
    )
    assert component["state"] == "ok"


@pytest.mark.version_zero
def test_error_envelope_is_stable_for_invalid_route_input(client) -> None:
    # spec: version-zero
    response = client.get("/api/v1/does-not-exist")

    assert response.status_code == 404
    body = response.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == "not_found"
    assert body["error"]["request_id"].startswith("req_")


@pytest.mark.version_zero
def test_openai_routes_keep_openai_wire_errors(client) -> None:
    # spec: version-zero
    invalid_body = client.post("/v1/chat/completions", json=[])
    assert invalid_body.status_code == 422
    assert set(invalid_body.json()) == {"error"}
    assert invalid_body.json()["error"] == {
        "message": "Invalid request body",
        "type": "invalid_request_error",
        "param": None,
        "code": "invalid_request_error",
    }

    unknown_route = client.get("/v1/does-not-exist")
    assert unknown_route.status_code == 404
    assert set(unknown_route.json()) == {"error"}
    assert unknown_route.json()["error"]["type"] == "invalid_request_error"


@pytest.mark.version_zero
def test_root_health_endpoint_uses_the_same_envelope(client) -> None:
    # spec: version-zero
    response = client.get("/healthz")

    assert response.status_code == 200
    assert set(response.json()) == {"data", "request_id"}


@pytest.mark.version_zero
def test_settings_endpoint_returns_effective_paths(client, settings) -> None:
    # spec: version-zero
    response = client.get("/api/v1/settings")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["data_root"] == str(settings.data_root)
    assert data["models_root"] == str(settings.data_root / "models")
    assert data["log_root"] == str(settings.data_root / "logs")
