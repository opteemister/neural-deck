"""Request schemas for provider-compatible proxy routes.

These models describe the stable fields used by the public contracts while
allowing provider-specific optional fields to pass through unchanged.  The
proxy is deliberately not a provider implementation: capability-specific
validation remains the responsibility of the selected serving.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ProviderJSONRequest(BaseModel):
    """Shared permissive base for native provider request bodies."""

    model_config = ConfigDict(extra="allow")

    model: str = Field(min_length=1, description="Public model or Neural Deck alias.")


class ChatCompletionsRequest(ProviderJSONRequest):
    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "examples": [
                {
                    "model": "local-model",
                    "messages": [{"role": "user", "content": "Hello"}],
                    "stream": True,
                }
            ]
        },
    )

    messages: list[dict[str, Any]] | None = None
    stream: bool | None = None


class ResponsesRequest(ProviderJSONRequest):
    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "examples": [
                {"model": "local-model", "input": "Write a short greeting.", "stream": True}
            ]
        },
    )

    input: Any = None
    stream: bool | None = None


class OpenAIEmbeddingsRequest(ProviderJSONRequest):
    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={"examples": [{"model": "embedding-model", "input": ["hello", "world"]}]},
    )

    input: Any = None
    encoding_format: str | None = None
    dimensions: int | None = Field(default=None, ge=1)


class SpeechRequest(ProviderJSONRequest):
    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "examples": [
                {
                    "model": "tts-model",
                    "input": "Hello from Neural Deck.",
                    "voice": "alloy",
                    "response_format": "mp3",
                }
            ]
        },
    )

    input: str | None = Field(default=None, min_length=1)
    voice: str | None = None
    response_format: str | None = None
    speed: float | None = Field(default=None, gt=0)
    instructions: str | None = None


class DecisionsRequest(ProviderJSONRequest):
    input: Any = Field(
        description="Text or OpenAI text input items to classify.",
        examples=["The package has arrived."],
    )
    questions: list[dict[str, Any]] = Field(
        min_length=1,
        description="Questions of type predicate, choice, or score.",
        examples=[
            [{"type": "predicate", "name": "delivered", "instructions": "Has the package arrived?"}]
        ],
    )


class SystemOneRequest(BaseModel):
    """Native System One request sent to a Jev-compatible serving."""

    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "examples": [
                {
                    "model": "decision-model",
                    "state": {"customer": "example"},
                    "questions": {"risk": {"type": "noul"}},
                }
            ]
        },
    )

    model: str = Field(min_length=1, description="Public model or Neural Deck alias.")
    state: Any = Field(description="Native System One state/context.")
    questions: Any = Field(description="Named native questions.")


class OCRDocument(BaseModel):
    """Mistral-compatible document reference (URL or data URI)."""

    model_config = ConfigDict(extra="allow")

    type: str = Field(
        min_length=1,
        description="document_url, image_url, or serving-specific type.",
    )
    document_url: str | None = None
    image_url: str | None = None


class OCRRequest(BaseModel):
    """Mistral-compatible OCR request forwarded to the chosen OCR serving."""

    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "examples": [
                {
                    "model": "ocr-model",
                    "document": {
                        "type": "image_url",
                        "image_url": "data:image/png;base64,...",
                    },
                }
            ]
        },
    )

    model: str = Field(min_length=1, description="Public model or Neural Deck alias.")
    document: OCRDocument | dict[str, Any] = Field(
        description="Document URL or image data URI reference."
    )
    include_image_base64: bool | None = None
    pages: list[int] | None = None
    table_format: str | None = None
    extract_header: bool | None = None
    extract_footer: bool | None = None


class CohereEmbedRequest(BaseModel):
    """Cohere Embed v2 fields, with provider extensions preserved."""

    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "examples": [
                {
                    "model": "embed-model",
                    "texts": ["hello", "world"],
                    "input_type": "search_query",
                }
            ]
        },
    )

    model: str = Field(min_length=1, description="Public model or Neural Deck alias.")
    texts: list[str] | None = None
    images: list[str] | None = None
    inputs: list[dict[str, Any]] | None = None
    input_type: (
        Literal["search_document", "search_query", "classification", "clustering", "image"] | None
    ) = None
    embedding_types: list[Literal["float", "int8", "uint8", "binary", "ubinary"]] | None = None
    truncate: Literal["NONE", "START", "END"] | None = None
    max_tokens: int | None = Field(default=None, ge=1)


__all__ = [
    "ChatCompletionsRequest",
    "CohereEmbedRequest",
    "DecisionsRequest",
    "OCRDocument",
    "OCRRequest",
    "OpenAIEmbeddingsRequest",
    "ProviderJSONRequest",
    "ResponsesRequest",
    "SpeechRequest",
    "SystemOneRequest",
]
