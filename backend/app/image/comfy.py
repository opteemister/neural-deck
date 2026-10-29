"""OpenAI-shaped image/video facade over :mod:`app.proxy.comfy_provider`."""

from __future__ import annotations

import asyncio
import base64
import time
from collections.abc import Mapping
from typing import Any

from ..errors import AppError
from ..proxy.comfy_provider import ComfyOutput, ComfyUIProvider
from ..schemas.image import ImageGenerationRequest
from .output import ImageOutputOptions


class ComfyImageService:
    """Thin response adapter; graph selection and execution live in provider."""

    def __init__(self, provider: ComfyUIProvider) -> None:
        self.provider = provider

    @staticmethod
    def _params(
        value: ImageGenerationRequest | Mapping[str, Any],
    ) -> tuple[dict[str, Any], set[str]]:
        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            params = model_dump(exclude_none=True)
            fields = set(getattr(value, "model_fields_set", params.keys()))
        elif isinstance(value, Mapping):
            params = dict(value)
            fields = set(params)
        else:
            raise AppError("invalid_request", "Некорректное тело image-запроса", status_code=400)
        for field in ("size", "n", "quality", "background", "output_format", "output_compression", "response_format", "seed"):
            if params.get(field) is None:
                params.pop(field, None)
                fields.discard(field)
        if not params.get("model"):
            raise AppError(
                "comfyui_model_required",
                "Для ComfyUI image API требуется model",
                status_code=400,
                details={"field": "model"},
            )
        if params.get("response_format", "url") not in {"url", "b64_json"}:
            raise AppError(
                "invalid_parameter",
                "response_format должен быть url или b64_json",
                status_code=400,
                details={"field": "response_format"},
            )
        return params, fields

    async def _response(
        self,
        outputs: list[ComfyOutput],
        *,
        response_format: str,
        url_prefix: str,
        prompt: str,
        options: ImageOutputOptions,
    ) -> dict[str, Any]:
        data: list[dict[str, Any]] = []
        for output in outputs:
            encoded = await asyncio.to_thread(options.encode, output)
            item: dict[str, Any] = {"revised_prompt": prompt}
            if response_format == "b64_json":
                item["b64_json"] = base64.b64encode(encoded.content).decode("ascii")
            else:
                if encoded.content != output.content or encoded.filename != output.filename:
                    encoded = await self.provider.persist_image_output(encoded)
                asset_id = await self.provider.register_asset(encoded)
                item["url"] = f"{url_prefix}/images/{asset_id}"
            data.append(item)
        return {"created": int(time.time()), "data": data}

    async def generate(
        self,
        request: ImageGenerationRequest | Mapping[str, Any],
        *,
        request_id: str | None = None,
        url_prefix: str = "/v1",
        operation: str | None = None,
    ) -> dict[str, Any]:
        params, fields = self._params(request)
        options = ImageOutputOptions.from_request(params)
        selected_operation = operation or (
            "edit_masked"
            if params.get("mask") is not None
            else "edit"
            if params.get("image") is not None
            else "generate"
        )
        outputs = await self.provider.run_image(
            str(params["model"]),
            selected_operation,
            params,
            request_id=request_id,
            provided_fields=fields - {"output_format", "output_compression"},
        )
        return await self._response(
            outputs,
            response_format=str(params.get("response_format", "url")),
            url_prefix=url_prefix,
            prompt=str(params.get("prompt", "")),
            options=options,
        )

    async def create_video(
        self,
        request: Mapping[str, Any],
        *,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        params = dict(request)
        model = params.get("model")
        if not isinstance(model, str) or not model:
            raise AppError(
                "comfyui_model_required",
                "Для ComfyUI video API требуется model",
                status_code=400,
                details={"field": "model"},
            )
        job = await self.provider.create_video(
            model, params, request_id=request_id, provided_fields=set(params)
        )
        return job.as_dict()

    async def video_status(self, video_id: str) -> dict[str, Any]:
        return (await self.provider.video_status(video_id)).as_dict()

    async def video_content(self, video_id: str) -> tuple[bytes, str]:
        return await self.provider.video_content(video_id)

    async def video_list(self) -> list[dict[str, Any]]:
        return [
            (await self.provider.video_status(job.id)).as_dict()
            for job in await self.provider.video_store.list()
        ]

    async def video_delete(self, video_id: str) -> bool:
        if await self.provider.video_store.get(video_id) is None:
            raise AppError("video_not_found", "Видео не найдено", status_code=404)
        return await self.provider.video_store.delete(video_id)

    async def asset(self, asset_id: str) -> tuple[bytes, str, str]:
        return await self.provider.asset(asset_id)

    async def close(self) -> None:
        await self.provider.close()


__all__ = ["ComfyImageService"]
