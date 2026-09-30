"""A second HTTP listener sharing the application's service container."""

from __future__ import annotations

import asyncio
import contextlib
import socket
from collections.abc import AsyncIterator, Generator
from urllib.parse import urlsplit

import uvicorn
from fastapi import FastAPI
from starlette.datastructures import Headers
from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send


class ApplicationCORSMiddleware:
    """Permit the UI on the same hostname and its configured application port."""

    def __init__(self, app: ASGIApp, application_port: int) -> None:
        self.app = app
        self.application_port = application_port

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        allowed: list[str] = []
        if scope["type"] == "http":
            headers = Headers(scope=scope)
            origin = headers.get("origin")
            if origin:
                try:
                    source = urlsplit(origin)
                    target = urlsplit("http://" + headers.get("host", ""))
                    if (
                        source.scheme in {"http", "https"}
                        and source.hostname == target.hostname
                        and source.port == self.application_port
                    ):
                        allowed.append(origin)
                except ValueError:
                    pass
        middleware = CORSMiddleware(
            self.app, allow_origins=allowed, allow_methods=["GET", "POST"],
            allow_headers=["Content-Type", "Authorization", "X-Request-ID"],
            expose_headers=["X-Request-ID"],
        )
        await middleware(scope, receive, send)


class _SharedServer(uvicorn.Server):
    @contextlib.contextmanager
    def capture_signals(self) -> Generator[None, None, None]:
        # The application listener alone owns process signals and shutdown.
        yield


@contextlib.asynccontextmanager
async def model_api_listener(app: FastAPI, host: str, port: int) -> AsyncIterator[None]:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    listener = socket.create_server((host, port), family=family)
    server = _SharedServer(uvicorn.Config(
        app, host=host, port=port, lifespan="off", access_log=False, log_config=None,
    ))
    task = asyncio.create_task(server.serve(sockets=[listener]), name="model-api-listener")
    try:
        async with asyncio.timeout(10):
            while not server.started:
                if task.done():
                    await task
                    raise RuntimeError("Model API listener stopped during startup")
                await asyncio.sleep(0.01)
        yield
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(task, timeout=10)
        except TimeoutError:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        finally:
            listener.close()
