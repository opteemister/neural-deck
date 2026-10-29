"""Media generation and image lookup over existing services."""

from __future__ import annotations

import base64
import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..errors import AppError
from ..proxy.comfy_provider import ComfyProviderService
from .store import ChatStore


class MediaArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    model: str = Field(min_length=1, max_length=256)
    prompt: str = Field(min_length=1, max_length=20_000)
    size: str | None = Field(default=None, pattern=r"^[1-9]\d{0,4}x[1-9]\d{0,4}$")
    background: Literal["opaque", "transparent"] | None = None
    seconds: int | None = Field(default=None, ge=1, le=600)
    source_id: str | None = Field(default=None, min_length=1, max_length=64)
    mask_id: str | None = Field(default=None, min_length=1, max_length=64)


class ImageLookup(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    image_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class ChatTools:
    def __init__(self, comfy: ComfyProviderService, store: ChatStore) -> None:
        self.comfy = comfy
        self.store = store

    def definitions(self) -> list[dict[str, Any]]:
        definitions = [
            {
                "type": "function",
                "function": {
                    "name": "get_image",
                    "description": (
                        "Retrieve an existing image for visual analysis. Use an image ID from this "
                        "conversation's generation/edit results or attachments. "
                        "The actual image is "
                        "provided as image input after the tool replies. This does not generate or "
                        "modify media. Use before answering questions about an image "
                        "you have not seen."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "image_id": {
                                "type": "string",
                                "description": "Exact image ID from this conversation.",
                            }
                        },
                        "required": ["image_id"],
                        "additionalProperties": False,
                    },
                },
            }
        ]
        models = self.comfy.get_settings()["models"]
        for name, kind, operation, description in (
            ("generate_image", "image", "generate", "Create an image from a prompt."),
            (
                "edit_image",
                "image",
                "edit",
                "Edit an existing image using its source_id; optional mask_id.",
            ),
            (
                "generate_video",
                "video",
                "generate",
                "Create a video, optionally from an image source_id.",
            ),
        ):
            choices = {
                alias: model
                for alias, model in models.items()
                if model["type"] == kind
                and (
                    operation in model.get("workflows", {})
                    or operation == "edit"
                    and "edit_masked" in model.get("workflows", {})
                )
            }
            if not choices:
                continue
            prompt_guidance = (
                "Before calling, make the requested edits precise while preserving all "
                "unrequested content."
                if name == "edit_image"
                else "Before calling, enrich brief prompts with coherent composition, lighting "
                "and style details"
                + (", plus natural subject and camera motion." if kind == "video" else ".")
            )
            limits = {
                alias: (
                    {
                        "max_megapixels": model.get("max_megapixels", 4),
                        "operations": list(model.get("workflows", {})),
                    }
                    if kind == "image"
                    else {
                        "max_seconds": model.get("max_seconds", 15),
                        "max_resolution": model.get("max_resolution", 768),
                        "operations": list(model.get("workflows", {})),
                    }
                )
                for alias, model in choices.items()
            }
            properties: dict[str, Any] = {
                "model": {"type": "string", "enum": sorted(choices)},
                "prompt": {
                    "type": "string",
                    "description": "Complete generation or editing instructions.",
                },
                "size": {
                    "type": "string",
                    "description": "Optional WIDTHxHEIGHT; omit to use saved chat settings.",
                },
            }
            if kind == "image":
                properties["background"] = {"type": "string", "enum": ["opaque", "transparent"]}
            else:
                properties["seconds"] = {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Optional duration; omit to use saved chat settings.",
                }
            if name != "generate_image":
                properties["source_id"] = {
                    "type": "string",
                    "description": "An image ID from this conversation's attachments or results.",
                }
            if name == "edit_image":
                properties["mask_id"] = {
                    "type": "string",
                    "description": "Optional mask image ID, if a mask was supplied by the user.",
                }
            definitions.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": (
                            f"{description} Model limits: {limits}. "
                            "Only call for a user request to create/edit media. "
                            f"{prompt_guidance} Preserve the user's intent "
                            "and explicit constraints; "
                            "do not embellish when an exact prompt is requested."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": properties,
                            "required": ["model", "prompt"]
                            + (["source_id"] if name == "edit_image" else []),
                            "additionalProperties": False,
                        },
                    },
                }
            )
        return definitions

    def parameters(
        self,
        name: str,
        raw: dict[str, Any],
        defaults: dict[str, Any],
    ) -> dict[str, Any]:
        args = MediaArguments.model_validate(raw)
        allowed = next(
            (item["function"] for item in self.definitions() if item["function"]["name"] == name),
            None,
        )
        if (
            allowed is None
            or args.model not in allowed["parameters"]["properties"]["model"]["enum"]
        ):
            raise AppError("chat_tool_unavailable", "Инструмент или модель недоступны")
        if set(raw) - set(allowed["parameters"]["properties"]):
            raise AppError("invalid_tool_arguments", "Инструмент не поддерживает эти параметры")
        if name == "edit_image" and not args.source_id:
            raise AppError("invalid_tool_arguments", "Для редактирования требуется source_id")
        model = self.comfy.get_settings()["models"][args.model]
        if name == "generate_video" and args.source_id and "edit" not in model.get("workflows", {}):
            raise AppError(
                "invalid_tool_arguments",
                "Для видео из изображения выберите алиас с настроенным workflow Edit",
            )
        saved = defaults.get(args.model, {})
        if name == "generate_video":
            maximum = model.get("max_resolution", 768)
            short = max(32, math.floor(min(640, maximum) / 32) * 32)
            size = args.size or saved.get("size") or f"{short}x{short}"
            seconds = (
                args.seconds
                if args.seconds is not None
                else saved.get("seconds", min(2, model.get("max_seconds", 15)))
            )
            if (
                not isinstance(seconds, int)
                or isinstance(seconds, bool)
                or not 1 <= seconds <= model.get("max_seconds", 15)
            ):
                raise AppError("invalid_tool_arguments", "Длительность превышает лимит алиаса")
        else:
            maximum = model.get("max_megapixels", 4) * 1024 * 1024
            side = max(64, math.floor(math.sqrt(maximum / 2) / 64 + 0.5) * 64)
            size = args.size or saved.get("size") or f"{side}x{side}"
        try:
            width, height = (int(part) for part in size.split("x"))
            if min(width, height) < 1 or max(width, height) > 16384:
                raise ValueError
        except (ValueError, TypeError, AttributeError) as exc:
            raise AppError(
                "invalid_tool_arguments", "Размер должен иметь вид WIDTHxHEIGHT"
            ) from exc
        if name == "generate_video":
            if min(width, height) > maximum or max(width, height) > maximum * 16 / 9:
                raise AppError("invalid_tool_arguments", "Разрешение превышает лимит видео-алиаса")
        elif width * height > maximum * 1.1:
            # Existing image settings round dimensions to the nearest 64-pixel grid.
            raise AppError("invalid_tool_arguments", "Разрешение превышает лимит image-алиаса")
        params: dict[str, Any] = {"model": args.model, "prompt": args.prompt, "size": size}
        if name == "generate_video":
            params["seconds"] = seconds
        else:
            params["background"] = args.background or saved.get("background", "opaque")
            if params["background"] not in {"opaque", "transparent"}:
                raise AppError("invalid_tool_arguments", "Неподдерживаемый фон")
        return params

    async def asset(self, conversation_id: str, asset_id: str) -> tuple[bytes, str, str]:
        row = await self.store.attachment(conversation_id, asset_id)
        if row["content"] is not None:
            return row["content"], row["media_type"], row["name"]
        return await self.comfy.asset(row["provider_id"])

    async def image_input(self, conversation_id: str, asset_id: str) -> str:
        content, media_type, _ = await self.asset(conversation_id, asset_id)
        if not media_type.startswith("image/"):
            raise AppError("invalid_tool_arguments", "Требуется изображение из этого диалога")
        return f"data:{media_type};base64,{base64.b64encode(content).decode('ascii')}"

    async def image(
        self,
        conversation_id: str,
        name: str,
        raw: dict[str, Any],
        params: dict[str, Any],
        request_id: str,
        *,
        operation: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        operation = operation or "generate"
        if operation == "variations":
            params["image"] = await self.image_input(conversation_id, raw["source_id"])
        if name == "edit_image":
            params["image"] = await self.image_input(conversation_id, raw["source_id"])
            operation = "edit"
            if raw.get("mask_id"):
                params["mask"] = await self.image_input(conversation_id, raw["mask_id"])
                operation = "edit_masked"
        result = await self.comfy.images.generate(
            params,
            operation=operation,
            request_id=request_id,
            url_prefix="/api/proxy",
        )
        assets = []
        for item in result["data"]:
            prefix = "/api/proxy/images/"
            if not isinstance(item.get("url"), str) or not item["url"].startswith(prefix):
                raise AppError(
                    "invalid_tool_result", "Генератор не вернул идентификатор изображения"
                )
            asset_id = await self.store.attach(
                conversation_id, "image.png", "image/png", provider_id=item["url"][len(prefix) :]
            )
            assets.append({"id": asset_id, "url": self.store.asset_url(conversation_id, asset_id)})
        if not assets:
            raise AppError("invalid_tool_result", "Генератор не вернул изображения")
        return {"images": assets}, {"kind": "image", "assets": assets}
