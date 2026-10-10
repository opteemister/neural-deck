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
    assert public.candidate_profile_ids == ("first",)
    assert public.available is True
    assert public.candidates[0].upstream_model == "upstream-first"

    manager.update_order("public-model", ["second", "first"])
    ordered = next(
        route
        for route in manager.snapshot(
            [first, second, other],
            {
                "first": RuntimeModel(instance("first", 8000), {"upstream-first": {}}),
                "second": RuntimeModel(instance("second", 8001), {"upstream-second": {}}),
            },
        )
        if route.name == "public-model"
    )
    assert ordered.candidate_profile_ids == ("second", "first")


def test_alias_order_is_persisted_and_only_duplicate_manual_names_are_rejected(tmp_path: Path) -> None:
    first = profile("first")
    second = profile("second")
    repository = ProfileRepositoryStub([first, second])
    manager = RouteManager(tmp_path, repository)

    manager.create_alias("chat", ["first", "second"])
    manager.update_order("chat", ["second", "first"])
    route = next(route for route in manager.snapshot([first, second], {}) if route.name == "chat")
    assert route.candidate_profile_ids == ("second", "first")

    manager.create_alias("public-model", ["first"])
    with pytest.raises(AppError, match="уже существует"):
        manager.create_alias("public-model", ["second"])
    with pytest.raises(AppError, match="ASCII"):
        manager.create_alias("русский", ["first"])
    with pytest.raises(AppError, match="хотя бы один"):
        manager.create_alias("empty", [])


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


def test_alias_rename_and_membership_update_are_saved_together(tmp_path):
    repository = ProfileRepositoryStub([profile("first"), profile("second")])
    manager = RouteManager(tmp_path, repository)
    manager.create_alias("chat", ["first", "second"])
    manager.update_alias("chat", "smart", ["second", "first"])
    reloaded = RouteManager(tmp_path, repository)
    assert reloaded.config.load()["aliases"] == {"smart": ["second", "first"]}
    assert reloaded.resolve("chat", repository.list().items, {}) is None


@pytest.mark.parametrize("name", ["taken", "bad/name", ""])
def test_alias_rename_rejects_conflicts_without_modifying_original(tmp_path, name):
    manager = RouteManager(tmp_path, ProfileRepositoryStub([profile("first")]))
    manager.create_alias("chat", ["first"])
    manager.create_alias("taken", ["first"])
    before = manager.config.path.read_bytes()
    with pytest.raises(AppError):
        manager.update_alias("chat", name, ["first"])
    assert manager.config.path.read_bytes() == before


@pytest.mark.asyncio
async def test_later_profile_name_collision_is_consistent_in_catalogue_and_requests(tmp_path):
    repository = ProfileRepositoryStub([profile("first")])
    manager = RouteManager(tmp_path, repository)
    manager.create_alias("chat", ["first"])
    repository._profiles.append(profile("second", "chat"))
    process_manager = ProcessManagerStub([instance("first", 8000), instance("second", 8001)])

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "chat"}]})
        return httpx.Response(200, json={"model": "chat"})

    service = ProxyService(process_manager, route_manager=manager,
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    routes = (await service.route_data())["items"]
    conflicting = [route for route in routes if route["name"] == "chat"]
    assert len(conflicting) == 1
    assert conflicting[0]["candidate_profile_ids"] == ["first", "second"]
    assert "chat" in [item["id"] for item in (await service.models())["data"]]
    response = await service.forward(route="/v1/chat/completions", payload={"model": "chat", "messages": []},
                                     headers={}, request_id="collision")
    assert response.status_code == 200
    manager.update_alias("chat", "smart", ["first"])
    assert "chat" in [item["id"] for item in (await service.models())["data"]]


@pytest.mark.asyncio
async def test_management_api_updates_alias_name_and_candidates(tmp_path):
    from fastapi import FastAPI
    from app.api.dependencies import get_proxy_service
    from app.api.proxy_routes import router

    manager = RouteManager(tmp_path, ProfileRepositoryStub([profile("first"), profile("second")]))
    manager.create_alias("old", ["first"])
    service = ProxyService(ProcessManagerStub([]), route_manager=manager)
    app = FastAPI()

    @app.middleware("http")
    async def request_id(request, call_next):
        request.state.request_id = "alias-update-test"
        return await call_next(request)

    app.include_router(router)
    app.dependency_overrides[get_proxy_service] = lambda: service
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch("/api/v1/proxy/routes/aliases/old", json={
            "name": "new", "profile_ids": ["second", "first"],
        })
    assert response.status_code == 200
    assert response.json()["data"]["name"] == "new"
    assert manager.config.load()["aliases"] == {"new": ["second", "first"]}
