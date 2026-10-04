"""OpenAI v1 wire routes."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response

from ..api.dependencies import get_proxy_service
from ..errors import AppError
from ..schemas.proxy import OpenAIModelList
from .service import ProxyService

router = APIRouter(prefix="/v1", tags=["openai"])
logger = logging.getLogger(__name__)


@router.get("/models", response_model=OpenAIModelList, response_model_exclude_none=True)
async def models(
    request: Request,
    service: ProxyService = Depends(get_proxy_service),  # noqa: B008
) -> dict[str, Any]:
    logger.info("GET %s: request_id=%s", request.url.path, request.state.request_id)
    result = await service.models()
    logger.info(
        "GET %s: models=%s request_id=%s",
        request.url.path,
        len(result["data"]),
        request.state.request_id,
    )
    return result


async def _forward(
    request: Request,
    route: str,
    payload: dict[str, Any],
    service: ProxyService,
) -> Response:
    headers = dict(request.headers)
    internal = request.url.path.startswith("/api/proxy/")
    if internal:
        headers["authorization"] = "Bearer l-chat"
    try:
        result = await service.forward(
            route=route,
            payload=payload,
            headers=headers,
            request_id=request.state.request_id,
            client_ip=request.client.host if request.client else None,
            force_usage=internal,
        )
    except AppError as exc:
        # OpenAI clients expect their own error object, separate from the
        # management API envelope used under /api/v1.
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "message": exc.message,
                    "type": exc.code,
                    "param": None,
                    "code": exc.code,
                }
            },
        )
    if isinstance(result, Response):
        return result
    # ``httpx.Response`` is an implementation detail of the forwarding seam.
    return Response(
        content=result.content,
        status_code=result.status_code,
        headers=dict(result.headers),
        media_type=result.headers.get("content-type"),
    )


@router.post("/chat/completions")
async def chat_completions(
    request: Request,
    payload: dict[str, Any],
    service: ProxyService = Depends(get_proxy_service),  # noqa: B008
) -> Response:
    return await _forward(request, "/v1/chat/completions", payload, service)


@router.post("/embeddings")
async def embeddings(
    request: Request,
    payload: dict[str, Any],
    service: ProxyService = Depends(get_proxy_service),  # noqa: B008
) -> Response:
    return await _forward(request, "/v1/embeddings", payload, service)


internal_router = APIRouter(prefix="/api/proxy", tags=["internal-chat"])
internal_router.add_api_route(
    "/models",
    models,
    methods=["GET"],
    response_model=OpenAIModelList,
    response_model_exclude_none=True,
)
internal_router.add_api_route("/chat/completions", chat_completions, methods=["POST"])

__all__ = ["get_proxy_service", "internal_router", "router"]
