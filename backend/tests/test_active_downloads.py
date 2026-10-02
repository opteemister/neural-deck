import json
from types import SimpleNamespace

from starlette.requests import Request

from app.api.management import operations


async def test_active_downloads_exclude_terminal_tasks_and_other_operations():
    values = [
        {
            "id": state, "kind": "download", "state": state,
            "target_id": f"hf:{state}", "created_at": "2026-10-01T00:00:00Z",
            "started_at": None, "finished_at": None,
            "stdout": "", "stderr": "", "exit_code": None, "error": None,
        }
        for state in ("queued", "running", "succeeded", "failed")
    ]
    values.append({**values[0], "id": "install", "kind": "install"})
    service = SimpleNamespace(operations=SimpleNamespace(all=lambda: values))
    request = Request({"type": "http", "state": {"request_id": "req_test"}})
    response = await operations(request, kind="download", active=True, service=service)
    assert {item["id"] for item in json.loads(response.body)["data"]["items"]} == {
        "queued", "running",
    }
    for value in values:
        value["state"] = "succeeded"
    response = await operations(request, kind="download", active=True, service=service)
    assert json.loads(response.body)["data"]["items"] == []
