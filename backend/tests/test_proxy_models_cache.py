import asyncio
from datetime import UTC, datetime

import httpx
from app.process.registry import ModelRegistry
from app.proxy.service import ProxyService
from app.schemas.instances import Instance, InstanceList


class Manager:
    def __init__(self):
        self.registry = ModelRegistry()
        self.instance = Instance(
            id="managed/profile/10-20", source="managed", profile_name="profile",
            engine_id="vllm", pid=10, process_start_time_ns=20,
            port=8000, model_name="public-model", state="ready", types=["text"],
            api_protocol="openai", api_base_url="http://127.0.0.1:8000/v1",
            observed_at=datetime.now(UTC),
        )
        self.registry.register_instance(self.instance)
        self.refreshes = 0

    async def refresh(self, *, probe_models=False):
        assert probe_models is False
        self.refreshes += 1
        return InstanceList(items=[self.instance])

    async def get(self, instance_id):
        assert instance_id == self.instance.id
        return self.instance

    def model_registry(self):
        return self.registry


async def test_cache_refreshes_upstream_names_after_ten_minutes(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr("app.proxy.service.time.monotonic", lambda: clock[0])
    manager = Manager()
    upstream = ["actual-first"]
    probes = []

    async def handler(request):
        probes.append(request.url.path)
        await asyncio.sleep(0)
        return httpx.Response(200, json={"data": [
            {"id": upstream[0], "max_model_len": 32768},
        ]})

    service = ProxyService(manager, client_factory=lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ))
    results = await asyncio.gather(*(service.models() for _ in range(5)))
    assert probes == ["/v1/models"]
    assert manager.refreshes == 5
    assert all(result["data"][0]["id"] == "public-model" for result in results)
    assert results[0]["data"][0]["context_length"] == 32768
    assert manager.registry.get("public-model").upstream_model == "actual-first"
    results[0]["data"].clear()
    upstream[0] = "actual-second"
    clock[0] += 599
    assert (await service.models())["data"]
    assert len(probes) == 1
    clock[0] += 1
    await service.models()
    assert len(probes) == 2
    assert manager.registry.get("public-model").upstream_model == "actual-second"


async def test_registry_changes_invalidate_cache_and_unregistered_process_is_probed():
    manager = Manager()
    manager.registry.clear_instance(manager.instance.id)
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"data": [{"id": "actual"}]})

    service = ProxyService(manager, client_factory=lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ))
    assert (await service.models())["data"][0]["id"] == "public-model"
    assert calls == ["/v1/models"]
    manager.instance.state = "exited"
    manager.registry.clear_instance(manager.instance.id)
    assert (await service.models())["data"] == []
    assert calls == ["/v1/models"]


async def test_empty_and_failed_probes_retry_until_nonempty_response():
    manager = Manager()
    replies = [httpx.Response(503), httpx.Response(200, json={"data": []}),
               httpx.Response(200, json={"data": [{"id": "actual"}]})]
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return replies.pop(0)

    service = ProxyService(manager, client_factory=lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ))
    assert (await service.models())["data"] == []
    assert (await service.models())["data"] == []
    assert (await service.models())["data"][0]["id"] == "public-model"
    assert (await service.models())["data"][0]["id"] == "public-model"
    assert len(calls) == 3


async def test_restart_with_changed_identity_invalidates_cache_without_waiting():
    manager = Manager()
    upstream = ["first"]
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"data": [{"id": upstream[0]}]})

    service = ProxyService(manager, client_factory=lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ))
    await service.models()
    upstream[0] = "second"
    manager.instance.pid = 11
    manager.instance.process_start_time_ns = 30
    manager.instance.id = "managed/profile/11-30"
    manager.instance.model_name = "new-public-model"
    manager.registry.clear_instance("managed/profile/10-20")
    manager.registry.register_instance(manager.instance)
    assert (await service.models())["data"][0]["id"] == "new-public-model"
    assert manager.registry.get("new-public-model").upstream_model == "second"
    assert len(calls) == 2


async def test_external_models_keep_separate_metadata_with_one_probe():
    manager = Manager()
    manager.instance.source = "external"
    manager.instance.identity_verified = True
    manager.registry.clear_instance(manager.instance.id)
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"data": [
            {"id": "first", "max_model_len": 4096},
            {"id": "second", "max_model_len": 8192},
        ]})

    service = ProxyService(manager, client_factory=lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ))
    result = await service.models()
    assert {item["id"]: item["context_length"] for item in result["data"]} == {
        "first": 4096, "second": 8192,
    }
    assert calls == ["/v1/models"]


async def test_starting_engine_retries_without_reprobing_cached_ready_engine():
    manager = Manager()
    starting = manager.instance.model_copy(update={
        "id": "managed/starting/11-30", "pid": 11, "process_start_time_ns": 30,
        "port": 8001, "api_base_url": "http://127.0.0.1:8001/v1", "model_name": "starting-model",
    })

    async def refresh(*, probe_models):
        assert probe_models is False
        return InstanceList(items=[manager.instance, starting])

    manager.refresh = refresh
    calls = []

    def handler(request):
        port = request.url.port
        calls.append(port)
        if port == 8001 and calls.count(8001) == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"data": [{"id": f"actual-{port}"}]})

    service = ProxyService(manager, client_factory=lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ))
    assert [item["id"] for item in (await service.models())["data"]] == ["public-model"]
    assert {item["id"] for item in (await service.models())["data"]} == {
        "public-model", "starting-model",
    }
    await service.models()
    assert calls.count(8000) == 1
    assert calls.count(8001) == 2
