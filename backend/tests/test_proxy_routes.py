from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from app.errors import AppError
from app.process.registry import ModelRegistry
from app.proxy.routes import RouteManager, RuntimeModel
from app.proxy.service import ProxyService
from app.schemas.instances import Instance, InstanceList
from app.schemas.profiles import ProfileData, ProfileListData


class ProfileRepositoryStub:
    def __init__(self, profiles: list[ProfileData]) -> None:
        self._profiles = profiles

    def list(self) -> ProfileListData:
        return ProfileListData(items=self._profiles, issues=[])


class ProcessManagerStub:
    def __init__(self, instances: list[Instance]) -> None:
        self.instances = instances
        self.registry = ModelRegistry()
        self.refreshes = 0

    async def refresh(self, *, probe_models: bool = False) -> InstanceList:
        assert probe_models is False
        self.refreshes += 1
        return InstanceList(items=self.instances)

    async def get(self, instance_id: str) -> Instance:
        return next(instance for instance in self.instances if instance.id == instance_id)

    def model_registry(self) -> ModelRegistry:
        return self.registry


class JournalStub:
    def __init__(self) -> None:
        self.records: list[dict[str, object]] = []

    async def identify_client(self, authorization: str | None, ip: str | None) -> None:
        return None

    async def record_safely(self, **values: object) -> bool:
        self.records.append(values)
        return True


def profile(name: str, model_name: str = "public-model") -> ProfileData:
    return ProfileData(
        version=1,
        name=name,
        model_name=model_name,
        engine={"id": "vllm", "installation_id": "vllm-default"},
        model={"path": f"/models/{name}"},
        command="vllm serve {{model}}",
        environment={},
        autostart=False,
        enable_metrics=True,
    )


def instance(profile_name: str, port: int, *, state: str = "ready") -> Instance:
    return Instance(
        id=f"managed/{profile_name}/{port}",
        source="managed",
        profile_name=profile_name,
        engine_id="vllm",
        pid=port,
        process_start_time_ns=port,
        identity_verified=True,
        can_stop=True,
        port=port,
        model_name="public-model",
        state=state,
        observed_at=datetime.now(UTC),
    )


def test_auto_routes_group_profiles_and_report_runtime_availability(tmp_path: Path) -> None:
    first = profile("first")
    second = profile("second")
    other = profile("other", "other-model")
    manager = RouteManager(tmp_path, ProfileRepositoryStub([first, second, other]))

    routes = manager.snapshot(
        [first, second, other],
        {
            "first": RuntimeModel(
                instance("first", 8000), {"upstream-first": {"context_length": 4096}}
            ),
        },
    )

    public = next(route for route in routes if route.name == "public-model")
    assert public.kind == "auto"
    assert public.candidate_profile_ids == ("first", "second")
    assert public.available is True
    assert public.candidates[0].upstream_model == "upstream-first"
    assert public.candidates[1].reason == "profile_not_running"


def test_alias_order_is_persisted_and_collisions_are_rejected(tmp_path: Path) -> None:
    first = profile("first")
    second = profile("second")
    repository = ProfileRepositoryStub([first, second])
    manager = RouteManager(tmp_path, repository)

    manager.create_alias("chat", ["first", "second"])
    manager.update_order("chat", ["second", "first"])
    route = next(route for route in manager.snapshot([first, second], {}) if route.name == "chat")
    assert route.candidate_profile_ids == ("second", "first")

    with pytest.raises(AppError, match="совпадает"):
        manager.create_alias("public-model", ["first"])
    with pytest.raises(AppError, match="ASCII"):
        manager.create_alias("русский", ["first"])


@pytest.mark.asyncio
async def test_route_failover_substitutes_upstream_model_and_uses_final_profile(
    tmp_path: Path,
) -> None:
    first = profile("first")
    second = profile("second")
    instances = [instance("first", 8000), instance("second", 8001)]
    process_manager = ProcessManagerStub(instances)
    route_manager = RouteManager(tmp_path, ProfileRepositoryStub([first, second]))
    journal = JournalStub()
    requests: list[tuple[int, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        port = request.url.port
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": f"upstream-{port}"}]})
        requests.append((port or 0, request.content.decode()))
        if port == 8000:
            raise httpx.ConnectError("first candidate is down", request=request)
        return httpx.Response(200, json={"id": "ok", "model": "upstream-8001"})

    service = ProxyService(
        process_manager,
        journal=journal,  # type: ignore[arg-type]
        route_manager=route_manager,
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    response = await service.forward(
        route="/v1/chat/completions",
        payload={"model": "public-model", "messages": []},
        headers={},
        request_id="request-1",
    )

    assert response.status_code == 200
    assert [port for port, _ in requests] == [8000, 8001]
    assert '"model":"upstream-8001"' in requests[-1][1]
    assert journal.records[-1]["profile_name"] == "second"
    assert journal.records[-1]["instance_id"] == instances[1].id
