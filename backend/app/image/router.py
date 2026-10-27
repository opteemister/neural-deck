"""OpenAI image routes and a scoped HTTP ComfyUI compatibility bridge."""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError
from starlette.responses import FileResponse

from ..api.dependencies import get_image_service
from ..errors import AppError
from ..schemas.image import ImageGenerationRequest
from .service import ImageGenerationService

router = APIRouter(prefix="/v1", tags=["openai-images"])
internal_router = APIRouter(prefix="/api/proxy", tags=["internal-images"])
native_router = APIRouter(prefix="/comfyui", tags=["comfyui-native"])


def _openai_error(exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {"message": exc.message, "type": exc.code, "param": None, "code": exc.code}
        },
    )


async def _generate(
    request: Request,
    payload: dict[str, Any],
    service: ImageGenerationService,
    prefix: str,
) -> Response:
    try:
        validated = ImageGenerationRequest.model_validate(payload)
        return JSONResponse(
            await service.generate(
                validated, request_id=request.state.request_id, url_prefix=prefix
            )
        )
    except ValidationError as exc:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "message": "Неверный запрос генерации изображения",
                    "type": "invalid_request_error",
                    "param": None,
                    "code": "invalid_request_error",
                    "details": exc.errors(),
                }
            },
        )
    except AppError as exc:
        return _openai_error(exc)


@router.post("/images/generations")
async def generations(
    request: Request,
    payload: dict[str, Any],
    service: ImageGenerationService = Depends(get_image_service),  # noqa: B008
) -> Response:
    return await _generate(request, payload, service, "/v1")


@internal_router.post("/images/generations")
async def internal_generations(
    request: Request,
    payload: dict[str, Any],
    service: ImageGenerationService = Depends(get_image_service),  # noqa: B008
) -> Response:
    return await _generate(request, payload, service, "/api/proxy")


@router.get("/images/{asset_id}")
@internal_router.get("/images/{asset_id}")
async def image_asset(
    asset_id: str,
    service: ImageGenerationService = Depends(get_image_service),  # noqa: B008
) -> Response:
    try:
        path, media_type = await service.asset(asset_id)
    except AppError as exc:
        return _openai_error(exc)
    return FileResponse(path, media_type=media_type, filename=path.name)


@native_router.api_route(
    "/{profile_name}/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"]
)
async def native_comfyui(
    profile_name: str,
    path: str,
    request: Request,
    service: ImageGenerationService = Depends(get_image_service),  # noqa: B008
) -> Response:
    try:
        upstream = await service.native_request(
            profile_name,
            request.method,
            path,
            query=dict(request.query_params),
            content=await request.body(),
            headers={
                key: value
                for key, value in request.headers.items()
                if key.lower()
                not in {
                    "host",
                    "content-length",
                    "connection",
                    "transfer-encoding",
                    "authorization",
                    "cookie",
                }
            },
        )
    except AppError as exc:
        return _openai_error(exc)
    headers = {
        key: value
        for key, value in upstream.headers.items()
        if key.lower()
        not in {
            "content-length",
            "connection",
            "transfer-encoding",
            "content-encoding",
            "set-cookie",
        }
    }
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=headers,
        media_type=upstream.headers.get("content-type"),
    )


@native_router.websocket("/{profile_name}/ws")
async def native_websocket(profile_name: str, websocket: WebSocket) -> None:
    """Bridge ComfyUI's progress websocket to the selected managed profile."""

    service: ImageGenerationService = websocket.app.state.runtime.image_service
    await websocket.accept()
    try:
        instance = await service.resolve_instance(profile_name=profile_name)
        if instance.port is None:
            raise AppError(
                "image_upstream_unavailable", "У ComfyUI отсутствует API-порт", status_code=503
            )
        from websockets.asyncio.client import connect

        query = urlencode(list(websocket.query_params.multi_items()))
        target = f"ws://127.0.0.1:{instance.port}/ws"
        if query:
            target = f"{target}?{query}"
        async with connect(target, open_timeout=5, close_timeout=2) as upstream:

            async def client_to_upstream() -> None:
                while True:
                    message = await websocket.receive()
                    if message.get("type") == "websocket.disconnect":
                        return
                    if message.get("text") is not None:
                        await upstream.send(message["text"])
                    elif message.get("bytes") is not None:
                        await upstream.send(message["bytes"])

            async def upstream_to_client() -> None:
                async for message in upstream:
                    if isinstance(message, bytes):
                        await websocket.send_bytes(message)
                    else:
                        await websocket.send_text(message)

            await asyncio.gather(client_to_upstream(), upstream_to_client())
    except (WebSocketDisconnect, AppError):
        return
    except Exception:
        # The native client will reconnect; do not expose a traceback or leak
        # the local ComfyUI address through the websocket protocol.
        return


__all__ = ["internal_router", "native_router", "router"]
