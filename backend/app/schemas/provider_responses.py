"""Permissive response schemas for the provider-compatible proxy routes.

The proxy deliberately returns the selected serving's wire response unchanged,
so these models are documentation schemas rather than response validation
contracts.  ``extra='allow'`` keeps the OpenAPI description useful without
pretending to implement every upstream provider extension.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ProviderError(BaseModel):
    """OpenAI-compatible error object returned by the proxy boundary."""

    model_config = ConfigDict(extra="allow")

    message: str = Field(description="Human-readable error message.")
    type: str | None = Field(default=None, description="Stable provider or proxy error type.")
    param: str | None = Field(default=None, description="Request parameter related to the error.")
    code: str | None = Field(default=None, description="Stable provider or proxy error code.")


class ProviderErrorResponse(BaseModel):
    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "examples": [
                {
                    "error": {
                        "message": "Selected model is unavailable.",
                        "type": "upstream_unavailable",
                        "param": None,
                        "code": "upstream_unavailable",
                    }
                }
            ]
        },
    )

    error: ProviderError


class ChatCompletionChoice(BaseModel):
    model_config = ConfigDict(extra="allow")

    index: int | None = None
    message: dict[str, Any] | None = None
    delta: dict[str, Any] | None = None
    finish_reason: str | None = None


class ChatCompletionResponse(BaseModel):
    """OpenAI chat completion or one non-streaming-compatible response."""

    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "examples": [
                {
                    "id": "chatcmpl-example",
                    "object": "chat.completion",
                    "created": 1730000000,
                    "model": "local-model",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "Hello."},
                            "finish_reason": "stop",
                        }
                    ],
                }
            ]
        },
    )

    id: str | None = None
    object: str | None = None
    created: int | None = None
    model: str | None = None
    choices: list[ChatCompletionChoice] = Field(default_factory=list)
    usage: dict[str, Any] | None = None


class ResponsesResponse(BaseModel):
    """Permissive OpenAI Responses API result."""

    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "examples": [
                {
                    "id": "resp-example",
                    "object": "response",
                    "created_at": 1730000000,
                    "model": "local-model",
                    "status": "completed",
                    "output_text": "Hello.",
                }
            ]
        },
    )

    id: str | None = None
    object: str | None = None
    created_at: int | None = None
    model: str | None = None
    status: str | None = None
    output: list[dict[str, Any]] = Field(default_factory=list)
    output_text: str | None = None
    usage: dict[str, Any] | None = None


class EmbeddingItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    object: str | None = "embedding"
    index: int | None = None
    embedding: list[float] = Field(default_factory=list)


class EmbeddingsResponse(BaseModel):
    """OpenAI-compatible text embedding response."""

    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "examples": [
                {
                    "object": "list",
                    "model": "embedding-model",
                    "data": [{"object": "embedding", "index": 0, "embedding": [0.1, -0.2]}],
                    "usage": {"prompt_tokens": 2, "total_tokens": 2},
                }
            ]
        },
    )

    object: str | None = "list"
    model: str | None = None
    data: list[EmbeddingItem] = Field(default_factory=list)
    usage: dict[str, Any] | None = None


class CohereEmbedResponse(BaseModel):
    """Permissive Cohere Embed v2 response."""

    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "examples": [
                {
                    "id": "embed-example",
                    "response_type": "embeddings",
                    "embeddings": {"float": [[0.1, -0.2]]},
                }
            ]
        },
    )

    id: str | None = None
    response_type: str | None = None
    embeddings: Any = None
    texts: list[str] | None = None
    meta: dict[str, Any] | None = None


class OCRPage(BaseModel):
    model_config = ConfigDict(extra="allow")

    index: int | None = None
    markdown: str | None = None
    images: list[dict[str, Any]] | None = None
    tables: list[dict[str, Any]] | None = None


class OCRResponse(BaseModel):
    """Mistral-compatible OCR result with permissive page extensions."""

    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "examples": [
                {
                    "model": "ocr-model",
                    "pages": [{"index": 0, "markdown": "# Extracted text"}],
                }
            ]
        },
    )

    model: str | None = None
    pages: list[OCRPage] = Field(default_factory=list)
    usage: dict[str, Any] | None = None


class DecisionsResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    model: str
    answers: list[dict[str, Any]]
    usage: dict[str, Any] | None = None


class SystemOneResponse(BaseModel):
    """Native System One result; answer payloads remain serving-defined."""

    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "examples": [
                {
                    "answers": {"question-1": {"type": "noul", "noul": 0.7}},
                }
            ]
        },
    )

    answers: dict[str, Any] | None = None
    state: Any = None
    questions: Any = None


class TranscriptionResponse(BaseModel):
    """JSON form of an audio transcription or translation result."""

    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={"examples": [{"text": "Hello from the audio file."}]},
    )

    text: str | None = None
    language: str | None = None
    duration: float | None = None
    segments: list[dict[str, Any]] | None = None
    words: list[dict[str, Any]] | None = None
    task: str | None = None


def provider_error_responses() -> dict[int | str, dict[str, Any]]:
    """Return common provider-boundary error metadata without shared mutation."""

    return {
        400: {
            "description": "Invalid provider request or unsupported input.",
            "model": ProviderErrorResponse,
        },
        502: {
            "description": "Selected serving is unavailable or returned an unsupported response.",
            "model": ProviderErrorResponse,
        },
    }


def provider_json_responses(
    model: type[BaseModel],
    *,
    streaming: bool = False,
    description: str = "Provider response.",
) -> dict[int | str, dict[str, Any]]:
    responses: dict[int | str, dict[str, Any]] = {
        200: {"description": description, "model": model},
        **provider_error_responses(),
    }
    if streaming:
        responses[200]["content"] = {
            "text/event-stream": {
                "schema": {
                    "type": "string",
                    "description": (
                        "Server-sent events. Each event contains a provider chunk; "
                        "the stream ends with the provider's terminal event."
                    ),
                }
            }
        }
    return responses


def provider_binary_responses(
    *,
    media_types: tuple[str, ...],
    description: str,
) -> dict[int | str, dict[str, Any]]:
    content = {
        media_type: {
            "schema": {"type": "string", "format": "binary"},
        }
        for media_type in media_types
    }
    return {
        200: {"description": description, "content": content},
        **provider_error_responses(),
    }


def transcription_responses() -> dict[int | str, dict[str, Any]]:
    return {
        200: {
            "description": "Transcription or translation result.",
            "model": TranscriptionResponse,
            "content": {
                "text/plain": {"schema": {"type": "string", "example": "Hello from audio."}},
                "text/vtt": {"schema": {"type": "string"}},
                "text/srt": {"schema": {"type": "string"}},
            },
        },
        **provider_error_responses(),
    }


__all__ = [
    "ChatCompletionChoice",
    "ChatCompletionResponse",
    "CohereEmbedResponse",
    "EmbeddingItem",
    "EmbeddingsResponse",
    "OCRPage",
    "OCRResponse",
    "ProviderError",
    "ProviderErrorResponse",
    "ResponsesResponse",
    "SystemOneResponse",
    "TranscriptionResponse",
    "provider_binary_responses",
    "provider_error_responses",
    "provider_json_responses",
    "transcription_responses",
]
