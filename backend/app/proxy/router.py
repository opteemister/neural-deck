"""OpenAI v1 wire routes."""

from __future__ import annotations

import logging
from typing import Any, Literal, cast

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.datastructures import UploadFile

from ..api.dependencies import get_proxy_service
from ..errors import AppError
from ..schemas.provider import (
    ChatCompletionsRequest,
    CohereEmbedRequest,
    DecisionsRequest,
    OCRRequest,
    OpenAIEmbeddingsRequest,
    ResponsesRequest,
    SpeechRequest,
    SystemOneRequest,
)
from ..schemas.provider_responses import (
    ChatCompletionResponse,
    CohereEmbedResponse,
    DecisionsResponse,
    EmbeddingsResponse,
    OCRResponse,
    ResponsesResponse,
    SystemOneResponse,
    provider_binary_responses,
    provider_error_responses,
    provider_json_responses,
    transcription_responses,
)
from ..schemas.proxy import OpenAIModel, OpenAIModelList
from .service import ProxyService

router = APIRouter(prefix="/v1", tags=["openai"])
v2_router = APIRouter(prefix="/v2", tags=["cohere"])
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
        description=("Base64 PNG, JPEG, or WebP data URI, for example data:image/png;base64,..."),
    )

    @model_validator(mode="after")
    def exactly_one_modality(self) -> MediaEmbeddingInput:
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
    all_types: bool = False,
    service: ProxyService = Depends(get_proxy_service),  # noqa: B008
) -> dict[str, Any]:
    logger.info("GET %s: request_id=%s", request.url.path, request.state.request_id)
    result = await service.models()
    if request.url.path.startswith("/api/proxy/") and not all_types:
        result = {
            **result,
            "data": [item for item in result["data"] if "text" in item.get("types", [])],
        }
    logger.info(
        "GET %s: models=%s request_id=%s",
        request.url.path,
        len(result["data"]),
        request.state.request_id,
    )
    return result


@router.get("/models/{model_id:path}", response_model=OpenAIModel, response_model_exclude_none=True)
async def model_detail(
    request: Request,
    model_id: str,
    service: ProxyService = Depends(get_proxy_service),  # noqa: B008
) -> dict[str, Any]:
    """Return one public model entry using the same catalogue as GET /models."""

    result = await service.models()
    item = next((value for value in result.get("data", []) if value.get("id") == model_id), None)
    if item is None or (
        request.url.path.startswith("/api/proxy/") and "text" not in item.get("types", [])
    ):
        raise AppError(
            "model_not_found", "Модель не найдена", status_code=404, details={"model": model_id}
        )
    return cast(dict[str, Any], item)


async def _forward(
    request: Request,
    route: str,
    payload: dict[str, Any],
    service: ProxyService,
    *,
    multipart_data: dict[str, Any] | None = None,
    multipart_files: dict[str, Any] | list[tuple[str, Any]] | None = None,
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
                    "param": exc.details.get("field"),
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


@router.post(
    "/chat/completions",
    responses=provider_json_responses(
        ChatCompletionResponse,
        streaming=True,
        description="OpenAI-compatible chat completion or SSE stream.",
    ),
)
async def chat_completions(
    request: Request,
    payload: ChatCompletionsRequest,
    service: ProxyService = Depends(get_proxy_service),  # noqa: B008
) -> Response:
    return await _forward(
        request, "/v1/chat/completions", payload.model_dump(exclude_unset=True), service
    )


@router.post(
    "/responses",
    responses=provider_json_responses(
        ResponsesResponse,
        streaming=True,
        description="OpenAI Responses result or SSE stream.",
    ),
)
async def responses(
    request: Request,
    payload: ResponsesRequest,
    service: ProxyService = Depends(get_proxy_service),  # noqa: B008
) -> Response:
    return await _forward(request, "/v1/responses", payload.model_dump(exclude_unset=True), service)


@router.post(
    "/systemone",
    response_model=None,
    responses=provider_json_responses(
        SystemOneResponse,
        description="Native System One response forwarded from the selected serving.",
    ),
)
async def systemone(
    request: Request,
    payload: SystemOneRequest,
    service: ProxyService = Depends(get_proxy_service),  # noqa: B008
) -> Response:
    """Forward the native System One contract without Decisions adaptation."""

    return await _forward(
        request,
        "/v1/systemone",
        payload.model_dump(exclude_unset=True),
        service,
    )


@router.post(
    "/embeddings",
    responses=provider_json_responses(
        EmbeddingsResponse,
        description="OpenAI-compatible embedding vectors.",
    ),
)
async def embeddings(
    request: Request,
    payload: OpenAIEmbeddingsRequest,
    service: ProxyService = Depends(get_proxy_service),  # noqa: B008
) -> Response:
    return await _forward(
        request, "/v1/embeddings", payload.model_dump(exclude_unset=True), service
    )


@v2_router.post(
    "/embed",
    response_model=None,
    responses=provider_json_responses(
        CohereEmbedResponse,
        description="Cohere-compatible Embed v2 response.",
    ),
)
async def embed(
    request: Request,
    payload: CohereEmbedRequest,
    service: ProxyService = Depends(get_proxy_service),  # noqa: B008
) -> Response:
    """Forward Cohere-compatible Embed v2 input/output unchanged."""

    return await _forward(
        request,
        "/v2/embed",
        payload.model_dump(exclude_unset=True),
        service,
    )


@router.post(
    "/embeddings/media",
    response_model=MediaEmbeddingResponse,
    summary="Create text or image embeddings",
    responses=provider_error_responses(),
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


@router.post(
    "/audio/speech",
    response_class=Response,
    responses=provider_binary_responses(
        media_types=(
            "audio/mpeg",
            "audio/wav",
            "audio/opus",
            "audio/aac",
            "audio/flac",
            "audio/pcm",
            "application/octet-stream",
        ),
        description="Generated audio bytes in the format requested by response_format.",
    ),
)
async def speech(
    request: Request,
    payload: SpeechRequest,
    service: ProxyService = Depends(get_proxy_service),  # noqa: B008
) -> Response:
    return await _forward(
        request, "/v1/audio/speech", payload.model_dump(exclude_unset=True), service
    )


@router.post(
    "/ocr",
    response_model=None,
    responses=provider_json_responses(
        OCRResponse,
        description="OCR pages and extracted document content.",
    ),
)
async def ocr(
    request: Request,
    payload: OCRRequest,
    service: ProxyService = Depends(get_proxy_service),  # noqa: B008
) -> Response:
    """Forward the selected Mistral-compatible OCR serving contract."""

    return await _forward(
        request,
        "/v1/ocr",
        payload.model_dump(exclude_unset=True),
        service,
    )


async def _audio_multipart(
    request: Request,
    route: str,
    service: ProxyService,
) -> Response:
    # Starlette spools uploads to disk; retain the file handle instead of
    # reading the audio into a bytes object. Bound the count of text fields.
    async with request.form(max_files=1, max_fields=32) as form:
        upload = form.get("file")
        model = form.get("model")
        if not isinstance(upload, UploadFile) or not isinstance(model, str) or not model:
            return JSONResponse(
                status_code=400,
                content={
                    "error": {
                        "message": "Multipart file and model are required",
                        "type": "invalid_request_error",
                        "param": None,
                        "code": "invalid_request_error",
                    }
                },
            )
        data: dict[str, Any] = {}
        for key, value in form.multi_items():
            if key == "file":
                continue
            if not isinstance(value, str):
                return JSONResponse(
                    status_code=400,
                    content={
                        "error": {
                            "message": "Only file may contain an upload",
                            "type": "invalid_request_error",
                            "param": key,
                            "code": "invalid_request_error",
                        }
                    },
                )
            if key in data:
                existing = data[key]
                data[key] = [*existing, value] if isinstance(existing, list) else [existing, value]
            else:
                data[key] = value
        if isinstance(data.get("model"), list):
            return JSONResponse(
                status_code=400,
                content={
                    "error": {
                        "message": "model must occur once",
                        "type": "invalid_request_error",
                        "param": "model",
                        "code": "invalid_request_error",
                    }
                },
            )
        await upload.seek(0)
        return await _forward(
            request,
            route,
            {"model": model},
            service,
            multipart_data=data,
            multipart_files={
                "file": (
                    upload.filename or "audio",
                    upload.file,
                    upload.content_type or "application/octet-stream",
                )
            },
        )


_AUDIO_MULTIPART_OPENAPI = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["file", "model"],
                    "properties": {
                        "file": {"type": "string", "format": "binary"},
                        "model": {"type": "string", "example": "whisper-model"},
                        "response_format": {"type": "string", "example": "json"},
                    },
                    "example": {
                        "model": "whisper-model",
                        "response_format": "json",
                        "file": "<audio file>",
                    },
                    "additionalProperties": True,
                }
            }
        },
    }
}


@router.post(
    "/audio/transcriptions",
    openapi_extra=_AUDIO_MULTIPART_OPENAPI,
    responses=transcription_responses(),
)
async def transcriptions(
    request: Request,
    service: ProxyService = Depends(get_proxy_service),  # noqa: B008
) -> Response:
    return await _audio_multipart(request, "/v1/audio/transcriptions", service)


@router.post(
    "/audio/translations",
    openapi_extra=_AUDIO_MULTIPART_OPENAPI,
    responses=transcription_responses(),
)
async def translations(
    request: Request,
    service: ProxyService = Depends(get_proxy_service),  # noqa: B008
) -> Response:
    return await _audio_multipart(request, "/v1/audio/translations", service)


@router.post("/decisions", responses=provider_json_responses(DecisionsResponse))
async def decisions(
    request: Request,
    payload: DecisionsRequest,
    service: ProxyService = Depends(get_proxy_service),  # noqa: B008
) -> Response:
    return await _forward(request, "/v1/decisions", payload.model_dump(exclude_unset=True), service)


@router.post("/images/generations")
async def images(
    request: Request,
    payload: dict[str, Any],
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
internal_router.add_api_route(
    "/models/{model_id:path}",
    model_detail,
    methods=["GET"],
    response_model=OpenAIModel,
    response_model_exclude_none=True,
)
internal_router.add_api_route("/chat/completions", chat_completions, methods=["POST"])
internal_router.add_api_route("/responses", responses, methods=["POST"])
internal_router.add_api_route("/systemone", systemone, methods=["POST"])
internal_router.add_api_route("/ocr", ocr, methods=["POST"])
internal_router.add_api_route("/embed", embed, methods=["POST"])
internal_router.add_api_route("/images/generations", images, methods=["POST"])
internal_router.add_api_route("/audio/speech", speech, methods=["POST"])
internal_router.add_api_route("/audio/transcriptions", transcriptions, methods=["POST"])
internal_router.add_api_route("/audio/translations", translations, methods=["POST"])
internal_router.add_api_route("/decisions", decisions, methods=["POST"])

__all__ = ["embed", "get_proxy_service", "internal_router", "router", "v2_router"]
