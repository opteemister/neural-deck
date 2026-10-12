"""OpenAI v1 wire routes."""

from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.datastructures import UploadFile

from ..api.dependencies import get_proxy_service
from ..errors import AppError
from ..schemas.proxy import OpenAIModelList
from .service import ProxyService

router = APIRouter(prefix="/v1", tags=["openai"])
logger = logging.getLogger(__name__)
MAX_MEDIA_IMAGE_DATA_URI_CHARS = 8 * 1024 * 1024 * 4 // 3 + 64


class MediaEmbeddingInput(BaseModel):
    """One text or base64 image item for Neural Deck's non-OpenAI media API."""

    model_config = ConfigDict(extra="forbid")

    text: str | None = Field(
        default=None, min_length=1, max_length=20_000, description="Plain text to embed."
    )
    image: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_MEDIA_IMAGE_DATA_URI_CHARS,
        description=(
            "Base64 PNG, JPEG, or WebP data URI, for example "
            "data:image/png;base64,..."
        ),
    )

    @model_validator(mode="after")
    def exactly_one_modality(self) -> "MediaEmbeddingInput":
        if (self.text is None) == (self.image is None):
            raise ValueError("Each media embedding input must contain exactly one of text or image")
        return self


class MediaEmbeddingRequest(BaseModel):
    """Neural Deck media-embedding request; distinct from OpenAI /v1/embeddings."""

    model_config = ConfigDict(extra="forbid")

    model: str = Field(min_length=1, description="Public embeddings profile or alias.")
    inputs: list[MediaEmbeddingInput] = Field(min_length=1, max_length=32)


class MediaEmbeddingItem(BaseModel):
    object: Literal["embedding"]
    index: int
    embedding: list[float]


class MediaEmbeddingResponse(BaseModel):
    """OpenAI-style vector envelope shared by text and image media inputs."""

    object: Literal["list"]
    model: str
    data: list[MediaEmbeddingItem]


@router.get("/models", response_model=OpenAIModelList, response_model_exclude_none=True)
async def models(
    request: Request,
    service: ProxyService = Depends(get_proxy_service),  # noqa: B008
) -> dict[str, Any]:
    logger.info("GET %s: request_id=%s", request.url.path, request.state.request_id)
    result = await service.models()
    if request.url.path.startswith("/api/proxy/"):
        result = {**result, "data": [item for item in result["data"]
                                    if "text" in item.get("types", [])]}
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
    *,
    multipart_data: dict[str, Any] | None = None,
    multipart_files: dict[str, Any] | None = None,
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
            multipart_data=multipart_data,
            multipart_files=multipart_files,
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


@router.post(
    "/embeddings/media",
    response_model=MediaEmbeddingResponse,
    summary="Create text or image embeddings",
    description=(
        "Neural Deck media endpoint for embeddings profiles using the OpenAI "
        "protocol and a serving adapter that implements this media contract. "
        "Each input contains exactly one of text or a base64 PNG/JPEG/WebP data URI. "
        "The standard OpenAI /v1/embeddings endpoint remains text-only."
    ),
)
async def media_embeddings(
    request: Request,
    payload: MediaEmbeddingRequest,
    service: ProxyService = Depends(get_proxy_service),  # noqa: B008
) -> Response:
    return await _forward(
        request,
        "/v1/embeddings/media",
        payload.model_dump(exclude_none=True),
        service,
    )


@router.post("/audio/speech")
async def speech(
    request: Request, payload: dict[str, Any],
    service: ProxyService = Depends(get_proxy_service),  # noqa: B008
) -> Response:
    return await _forward(request, "/v1/audio/speech", payload, service)


@router.post("/audio/transcriptions")
async def transcriptions(
    request: Request,
    service: ProxyService = Depends(get_proxy_service),  # noqa: B008
) -> Response:
    # Starlette spools uploads to disk; retain the file handle instead of
    # reading the audio into a bytes object. Bound the count of text fields.
    async with request.form(max_files=1, max_fields=32) as form:
        upload = form.get("file")
        model = form.get("model")
        if not isinstance(upload, UploadFile) or not isinstance(model, str) or not model:
            return JSONResponse(status_code=400, content={"error": {
                "message": "Multipart file and model are required", "type": "invalid_request_error",
                "param": None, "code": "invalid_request_error",
            }})
        data: dict[str, Any] = {}
        for key, value in form.multi_items():
            if key == "file":
                continue
            if not isinstance(value, str):
                return JSONResponse(status_code=400, content={"error": {
                    "message": "Only file may contain an upload", "type": "invalid_request_error",
                    "param": key, "code": "invalid_request_error",
                }})
            if key in data:
                existing = data[key]
                data[key] = [*existing, value] if isinstance(existing, list) else [existing, value]
            else:
                data[key] = value
        if isinstance(data.get("model"), list):
            return JSONResponse(status_code=400, content={"error": {
                "message": "model must occur once", "type": "invalid_request_error",
                "param": "model", "code": "invalid_request_error",
            }})
        await upload.seek(0)
        return await _forward(
            request, "/v1/audio/transcriptions", {"model": model}, service,
            multipart_data=data,
            multipart_files={"file": (upload.filename or "audio", upload.file,
                                      upload.content_type or "application/octet-stream")},
        )


@router.post("/decisions")
async def decisions(
    request: Request, payload: dict[str, Any],
    service: ProxyService = Depends(get_proxy_service),  # noqa: B008
) -> Response:
    return await _forward(request, "/v1/decisions", payload, service)


@router.post("/images/generations")
async def images(
    request: Request, payload: dict[str, Any],
    service: ProxyService = Depends(get_proxy_service),  # noqa: B008
) -> Response:
    return await _forward(request, "/v1/images/generations", payload, service)


internal_router = APIRouter(prefix="/api/proxy", tags=["internal-chat"])
internal_router.add_api_route(
    "/models",
    models,
    methods=["GET"],
    response_model=OpenAIModelList,
    response_model_exclude_none=True,
)
internal_router.add_api_route("/chat/completions", chat_completions, methods=["POST"])
internal_router.add_api_route("/images/generations", images, methods=["POST"])
internal_router.add_api_route("/audio/speech", speech, methods=["POST"])
internal_router.add_api_route("/audio/transcriptions", transcriptions, methods=["POST"])
internal_router.add_api_route("/decisions", decisions, methods=["POST"])

__all__ = ["get_proxy_service", "internal_router", "router"]
