"""Managed ComfyUI HTTP bridge and OpenAI image-generation adapter."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from ..errors import AppError
from ..process.manager import ProcessManager
from ..profiles.repository import ProfileRepository
from ..schemas.image import ImageGenerationRequest
from ..schemas.instances import Instance
from .workflow import build_workflow, decode_image_bytes, validate_model_directory


class ImageGenerationService:
    def __init__(
        self,
        process_manager: ProcessManager,
        profile_repository: ProfileRepository,
        *,
        client_factory: Any | None = None,
        timeout: float = 1800.0,
    ) -> None:
        self.process_manager = process_manager
        self.profile_repository = profile_repository
        self.client_factory = client_factory
        self.timeout = timeout
        self._assets: dict[str, tuple[Path, str]] = {}

    async def close(self) -> None:
        self._assets.clear()

    async def _client(self) -> httpx.AsyncClient:
        if self.client_factory is not None:
            return self.client_factory()
        return httpx.AsyncClient(timeout=self.timeout)

    async def resolve_instance(self, model: str | None = None, profile_name: str | None = None) -> Instance:
        observed = await self.process_manager.refresh(probe_models=False)
        candidates = [
            item
            for item in observed.items
            if item.engine_id == "image"
            and item.state == "ready"
            and item.identity_verified
            and item.port is not None
            and item.profile_name is not None
        ]
        if profile_name:
            candidates = [item for item in candidates if item.profile_name == profile_name]
        elif model:
            candidates = [
                item for item in candidates if item.profile_name == model or item.model_name == model
            ]
        if not candidates:
            raise AppError(
                "image_instance_not_found",
                "Запущенный ComfyUI Image профиль не найден",
                status_code=404,
                details={"model": model, "profile_name": profile_name},
            )
        if len(candidates) > 1:
            raise AppError(
                "image_model_required",
                "Укажите model с именем профиля ComfyUI Image",
                status_code=409,
                details={"profiles": [item.profile_name for item in candidates]},
            )
        return candidates[0]

    async def generate(
        self,
        request: ImageGenerationRequest | Mapping[str, Any],
        *,
        request_id: str,
        url_prefix: str = "/v1",
    ) -> dict[str, Any]:
        payload = request if isinstance(request, ImageGenerationRequest) else ImageGenerationRequest.model_validate(request)
        instance = await self.resolve_instance(model=payload.model)
        if not instance.profile_name:
            raise AppError("image_profile_missing", "У image-экземпляра отсутствует профиль", status_code=409)
        profile = await asyncio.to_thread(self.profile_repository.get, instance.profile_name)
        validate_model_directory(profile.model.path)
        base_directory = self.process_manager.profile_runtime_dir(profile.name)
        workflow_directory = base_directory / "workflows"
        workflow_directory.mkdir(parents=True, exist_ok=True)
        output: list[dict[str, Any]] = []
        for _index in range(payload.n):
            seed = payload.seed if payload.seed is not None else int.from_bytes(uuid4().bytes[:8], "big")
            input_name = None
            if payload.image:
                input_name = await self._upload_input_image(instance, payload.image)
            workflow = build_workflow(
                profile.model.path,
                prompt=payload.prompt,
                size=payload.size,
                seed=seed,
                steps=payload.steps,
                transparent=payload.transparent,
                input_image=input_name,
            )
            workflow_path = workflow_directory / "qwen-image-2.1-t2i.json"
            workflow_path.write_text(json.dumps(workflow, ensure_ascii=False, indent=2), encoding="utf-8")
            prompt_id = await self._queue(instance, workflow, request_id)
            images = await self._wait_for_images(instance, prompt_id)
            if not images:
                raise AppError("image_output_missing", "ComfyUI не вернул изображение", status_code=502, retryable=True)
            for image in images:
                asset_id = uuid4().hex
                content, media_type = await self._download(instance, image)
                suffix = ".png" if media_type == "image/png" else ".bin"
                asset_path = base_directory / "output" / "neural-deck" / f"{asset_id}{suffix}"
                asset_path.parent.mkdir(parents=True, exist_ok=True)
                asset_path.write_bytes(content)
                self._assets[asset_id] = (asset_path, media_type)
                item: dict[str, Any] = {"revised_prompt": payload.prompt}
                if payload.response_format == "b64_json":
                    item["b64_json"] = base64.b64encode(content).decode("ascii")
                else:
                    item["url"] = f"{url_prefix}/images/{asset_id}"
                output.append(item)
                break
            if len(output) >= payload.n:
                break
        return {"created": int(time.time()), "data": output}

    async def _upload_input_image(self, instance: Instance, value: str) -> str:
        content = decode_image_bytes(value)
        filename = f"neural-deck-{uuid4().hex}.png"
        response = await self._request(
            instance, "POST", "/upload/image",
            files={"image": (filename, content, "image/png")},
            data={"type": "input"},
        )
        if response.status_code >= 400:
            raise AppError("image_upload_failed", "ComfyUI не принял входное изображение", status_code=502)
        try:
            uploaded = response.json()
        except ValueError as exc:
            raise AppError("image_upload_failed", "ComfyUI вернул неверный ответ загрузки", status_code=502) from exc
        if not isinstance(uploaded, dict) or not isinstance(uploaded.get("name"), str) or not uploaded["name"]:
            raise AppError("image_upload_failed", "ComfyUI не вернул имя загруженного изображения", status_code=502)
        subfolder = uploaded.get("subfolder", "")
        if not isinstance(subfolder, str):
            raise AppError("image_upload_failed", "ComfyUI вернул неверный каталог изображения", status_code=502)
        return f"{subfolder}/{uploaded['name']}" if subfolder else uploaded["name"]

    async def _queue(self, instance: Instance, workflow: dict[str, Any], request_id: str) -> str:
        response = await self._request(instance, "POST", "/prompt", json={"prompt": workflow, "client_id": request_id})
        try:
            payload = response.json()
        except (ValueError, TypeError) as exc:
            raise AppError("image_upstream_error", "ComfyUI вернул неверный ответ на /prompt", status_code=502) from exc
        prompt_id = payload.get("prompt_id") if isinstance(payload, dict) else None
        if response.status_code >= 400 or not isinstance(prompt_id, str) or not prompt_id:
            raise AppError(
                "image_upstream_error",
                "ComfyUI не принял workflow",
                status_code=502,
                details={"response": payload},
            )
        return prompt_id

    async def _wait_for_images(self, instance: Instance, prompt_id: str) -> list[dict[str, str]]:
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            response = await self._request(instance, "GET", f"/history/{prompt_id}")
            if response.status_code == 200:
                try:
                    payload = response.json()
                except (ValueError, TypeError):
                    payload = {}
                entry = payload.get(prompt_id) if isinstance(payload, dict) else None
                if isinstance(entry, dict):
                    status = entry.get("status")
                    if isinstance(status, dict) and status.get("status_str") == "error":
                        raise AppError("image_upstream_error", "ComfyUI завершил workflow с ошибкой", status_code=502, details={"status": status})
                    result: list[dict[str, str]] = []
                    outputs = entry.get("outputs")
                    if isinstance(outputs, dict):
                        for node_output in outputs.values():
                            if not isinstance(node_output, dict):
                                continue
                            images = node_output.get("images")
                            if not isinstance(images, list):
                                continue
                            for image in images:
                                if not isinstance(image, dict) or not isinstance(image.get("filename"), str):
                                    continue
                                result.append({
                                    "filename": image["filename"],
                                    "subfolder": str(image.get("subfolder", "")),
                                    "type": str(image.get("type", "output")),
                                })
                    if result:
                        return result
            await asyncio.sleep(0.25)
        raise AppError("image_timeout", "ComfyUI не завершил генерацию за отведённое время", status_code=504, retryable=True)

    async def _download(self, instance: Instance, image: Mapping[str, str]) -> tuple[bytes, str]:
        response = await self._request(
            instance,
            "GET",
            "/view",
            params={"filename": image["filename"], "subfolder": image.get("subfolder", ""), "type": image.get("type", "output")},
        )
        if response.status_code >= 400:
            raise AppError("image_output_unavailable", "ComfyUI не отдал файл изображения", status_code=502)
        media_type = response.headers.get("content-type", "image/png").split(";", 1)[0]
        return response.content, media_type

    async def native_request(
        self,
        profile_name: str,
        method: str,
        path: str,
        *,
        query: Mapping[str, str] | None = None,
        content: bytes | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        instance = await self.resolve_instance(profile_name=profile_name)
        clean_path = path.strip("/")
        if not clean_path or ".." in clean_path.split("/"):
            raise AppError("invalid_comfyui_path", "Недопустимый native ComfyUI путь", status_code=422)
        return await self._request(
            instance,
            method,
            f"/{clean_path}",
            params=query,
            content=content,
            headers=headers,
        )

    async def asset(self, asset_id: str) -> tuple[Path, str]:
        item = self._assets.get(asset_id)
        if item is None or not item[0].is_file():
            raise AppError("image_not_found", "Изображение не найдено или срок его хранения истёк", status_code=404)
        return item

    async def _request(self, instance: Instance, method: str, path: str, **kwargs: Any) -> httpx.Response:
        if instance.port is None:
            raise AppError("image_upstream_unavailable", "У ComfyUI отсутствует API-порт", status_code=503, retryable=True)
        client = await self._client()
        try:
            response = await client.request(method, f"http://127.0.0.1:{instance.port}{path}", **kwargs)
            await response.aread()
            return response
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
            raise AppError("image_upstream_unavailable", "ComfyUI недоступен", status_code=502, retryable=True) from exc
        finally:
            with contextlib.suppress(Exception):
                await client.aclose()


__all__ = ["ImageGenerationService"]
