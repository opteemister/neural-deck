import json
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from app.api.management import openai_endpoint
from app.errors import AppError


@pytest.mark.parametrize(
    ("local_ip", "expected"),
    [("192.168.1.50", "http://192.168.1.50:8080/v1"),
     ("2001:db8::1", "http://[2001:db8::1]:8080/v1")],
)
async def test_endpoint_uses_local_socket_not_proxy_domain(local_ip, expected):
    request = Request({
        "type": "http", "server": (local_ip, 3000),
        "headers": [(b"host", b"ai.example.com"), (b"x-forwarded-host", b"other.org")],
        "state": {"request_id": "req_test"},
    })
    response = await openai_endpoint(request, SimpleNamespace(openai_port=8080))
    assert json.loads(response.body)["data"]["url"] == expected


@pytest.mark.parametrize("server", [None, ("0.0.0.0", 3000), ("::", 3000), ("ai.example.com", 3000)])
async def test_endpoint_does_not_fall_back_when_ip_is_unavailable(server):
    request = Request({"type": "http", "server": server, "headers": []})
    with pytest.raises(AppError, match="Локальный IP") as error:
        await openai_endpoint(request, SimpleNamespace(openai_port=8080))
    assert error.value.code == "machine_ip_unavailable"
