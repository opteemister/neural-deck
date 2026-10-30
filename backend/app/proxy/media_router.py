"""Provider media routes and same-origin configuration for ComfyUI aliases."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated, Any, Literal, cast
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from starlette.datastructures import UploadFile

from ..api.dependencies import get_proxy_service, get_runtime
from ..api.management import success
from ..container import AppContainer
from ..errors import AppError
from ..schemas.media_responses import IMAGE_RESPONSES, VIDEO_RESPONSES
from ..schemas.proxy import OpenAIModel, OpenAIModelList
from .comfy_provider import ComfyProviderService
from .router import _forward
from .configuration import ProxyConfiguration
from ..schemas.proxy_management import ComfySourceRequest, MediaAliasRequest
from .service import ProxyService

router = APIRouter(prefix="/v1", tags=["media"])
internal_router = APIRouter(prefix="/api/proxy", tags=["internal-media"])
management_router = APIRouter(prefix="/api/v1/proxy", tags=["proxy-media"])
RuntimeDep = Annotated[AppContainer, Depends(get_runtime)]
ProxyDep = Annotated[ProxyService, Depends(get_proxy_service)]


class ImageRequest(BaseModel):
    """Common standard image fields; optional native extensions pass through."""

    model_config = ConfigDict(extra="allow")
    model: str = Field(
        min_length=1, examples=["qwen-image-turbo"], description="Публичный алиас модели."
    )
    prompt: str = Field(
        min_length=1,
        examples=["A green ceramic teapot on a light background"],
        description="Описание изображения.",
    )
    n: StrictInt | None = Field(
        default=None, ge=1, description="Число изображений; передаётся в batch/count вход workflow."
    )
    size: str | None = Field(
        default=None, description="WIDTHxHEIGHT. Если не задан, используются размеры workflow."
    )
    quality: str | None = Field(default="auto", description="auto, low, medium, high используют сохранённый native-режим workflow без изменения сэмплинга.")
    background: Literal["auto", "opaque", "transparent"] | None = Field(
        default=None, description="Режим фона, включая прозрачность. Это не асинхронное исполнение."
    )
    output_format: Literal["png", "jpeg", "webp"] | None = Field(
        default="png", description="Реальный формат результата; PNG по умолчанию. При необходимости преобразуется после ComfyUI."
    )
    output_compression: StrictInt | None = Field(
        default=None, ge=0, le=100, description="Качество кодирования JPEG/WebP от 0 до 100, по умолчанию 100. Для PNG не применяется и отклоняется."
    )
    seed: StrictInt | None = Field(default=None, ge=0, le=2**64 - 1, description="Расширение: seed генератора шума. Без значения — новый случайный seed на каждый запрос.")
    response_format: Literal["url", "b64_json"] | None = Field(
        default=None, description="Доставка результата: URL файла или base64 его байтов."
    )


def _service(runtime: AppContainer) -> ComfyProviderService:
    service: ComfyProviderService | None = getattr(runtime, "comfy_service", None)
    if service is None:
        raise AppError("comfyui_unavailable", "ComfyUI adapter не запущен", status_code=503)
    return service


async def _wire(action: Callable[[], Awaitable[Any]]) -> Any:
    try:
        return await action()
    except AppError as exc:
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


async def _all_models(runtime: AppContainer, result: dict[str, Any]) -> dict[str, Any]:
    service: ComfyProviderService | None = getattr(runtime, "comfy_service", None)
    if service is None:
        return result
    return await service.extend_models(result)


@router.get("/models", response_model=OpenAIModelList, response_model_exclude_none=True)
async def models(request: Request, service: ProxyDep, all_types: bool = False) -> dict[str, Any]:
    result = await service.models()
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is not None:
        result = await _all_models(runtime, result)
    if request.url.path.startswith("/api/proxy/") and not all_types:
        result["data"] = [item for item in result["data"] if "text" in item.get("types", [])]
    return result


@router.get("/models/{model_id:path}", response_model=OpenAIModel, response_model_exclude_none=True)
async def model_detail(
    request: Request, model_id: str, service: ProxyDep, all_types: bool = False
) -> Any:
    async def find() -> dict[str, Any]:
        result = await service.models()
        runtime = getattr(request.app.state, "runtime", None)
        if runtime is not None:
            result = await _all_models(runtime, result)
        for item in result["data"]:
            if item["id"] == model_id:
                if (
                    request.url.path.startswith("/api/proxy/")
                    and not all_types
                    and "text" not in item.get("types", [])
                ):
                    break
                return cast(dict[str, Any], item)
        raise AppError("model_not_found", "Модель не найдена", status_code=404)

    return await _wire(find)


@management_router.get("/comfyui")
async def settings(request: Request, runtime: RuntimeDep) -> JSONResponse:
    return success(request, _service(runtime).get_settings())


@management_router.put("/comfyui")
async def save_settings(
    request: Request, payload: dict[str, Any], runtime: RuntimeDep
) -> JSONResponse:
    return success(request, await ProxyConfiguration(runtime.proxy_service, _service(runtime)).save(payload))


@management_router.post("/comfyui/sources")
async def create_source(request: Request, payload: ComfySourceRequest, runtime: RuntimeDep):
    return success(request, ProxyConfiguration(runtime.proxy_service, _service(runtime)).save_source(payload))


@management_router.patch("/comfyui/sources/{source}")
async def update_source(request: Request, source: str, payload: ComfySourceRequest, runtime: RuntimeDep):
    return success(request, ProxyConfiguration(runtime.proxy_service, _service(runtime)).save_source(payload, source))


@management_router.delete("/comfyui/sources/{source}")
async def delete_source(request: Request, source: str, runtime: RuntimeDep):
    return success(request, ProxyConfiguration(runtime.proxy_service, _service(runtime)).delete_source(source))


@management_router.post("/comfyui/aliases")
async def create_media_alias(request: Request, payload: MediaAliasRequest, runtime: RuntimeDep):
    return success(request, await ProxyConfiguration(runtime.proxy_service, _service(runtime)).save_alias(payload))


@management_router.patch("/comfyui/aliases/{name}")
async def update_media_alias(request: Request, name: str, payload: MediaAliasRequest, runtime: RuntimeDep):
    return success(request, await ProxyConfiguration(runtime.proxy_service, _service(runtime)).save_alias(payload, name))


@management_router.delete("/comfyui/aliases/{name}")
async def delete_media_alias(request: Request, name: str, runtime: RuntimeDep):
    return success(request, ProxyConfiguration(runtime.proxy_service, _service(runtime)).delete_alias(name))


@management_router.get("/comfyui/sources/{source}/workflows")
async def workflows(request: Request, source: str, runtime: RuntimeDep) -> JSONResponse:
    return success(request, await _service(runtime).workflows(source))


@management_router.get("/comfyui/sources/{source}/workflows/validate")
async def validate_workflow(request: Request, source: str, path: str, runtime: RuntimeDep) -> Any:
    async def check() -> JSONResponse:
        return success(request, await _service(runtime).validate_workflow(source, path))

    return await _wire(check)


@management_router.get("/comfyui/sources/{source}/status")
async def source_status(request: Request, source: str, runtime: RuntimeDep) -> Any:
    async def check() -> JSONResponse:
        return success(request, await _service(runtime).source_status(source))

    return await _wire(check)


@router.post("/images/generations", response_model=None, responses=IMAGE_RESPONSES)
async def image_generation(request: Request, payload: ImageRequest, runtime: RuntimeDep) -> Any:
    async def run() -> Any:
        body = payload.model_dump(exclude_unset=True)
        service = _service(runtime)
        model = service.get_settings()["models"].get(payload.model)
        if model is None:
            return await _forward(request, "/v1/images/generations", body, runtime.proxy_service)
        if model["type"] != "image":
            raise AppError("unsupported_operation", "Требуется image-модель", status_code=400)
        prefix = (
            "/api/proxy"
            if request.url.path.startswith("/api/proxy/")
            else str(request.base_url).rstrip("/") + "/v1"
        )
        return await service.generate_image(
            body, request_id=request.state.request_id, url_prefix=prefix
        )

    return await _wire(run)


def _multipart_schema(
    fields: dict[str, Any], required: list[str], *, one_of: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    return {
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object", "properties": fields, "required": required,
                        **({"oneOf": one_of} if one_of else {}),
                    },
                }
            },
        }
    }


_IMAGE_FIELDS: dict[str, Any] = {
    "model": {"type": "string"},
    "prompt": {"type": "string"},
    "image": {"type": "string", "format": "binary"},
    "mask": {"type": "string", "format": "binary"},
    **{
        name: field
        for name, field in ImageRequest.model_json_schema()["properties"].items()
        if name not in {"model", "prompt"}
    },
}
_VIDEO_FIELDS: dict[str, Any] = {
    "model": {"type": "string"},
    "prompt": {"type": "string"},
    "size": {"type": "string"},
    "seconds": {
        "type": "string",
        "description": "Duration in seconds supported by the workflow.",
        "example": "4",
    },
    "input_reference": {"type": "string", "format": "binary"},
    "seed": {"type": "integer", "minimum": 0, "description": "Необязательный seed генерации; без значения выбирается новый случайный seed."},
}


async def _form(
    request: Request, *, image_array: bool = False
) -> tuple[dict[str, Any], dict[str, Any], list[tuple[str, Any]]]:
    async with request.form() as form:
        body: dict[str, Any] = {}
        data: dict[str, Any] = {}
        files: list[tuple[str, Any]] = []
        for name, value in form.multi_items():
            is_image_array = image_array and name == "image[]"
            if is_image_array and not isinstance(value, UploadFile):
                raise AppError(
                    "invalid_parameter", "image[] должен содержать файлы изображений",
                    status_code=400, details={"field": name},
                )
            if name in body and not is_image_array:
                raise AppError(
                    "unsupported_parameter",
                    "Повторяющееся поле не поддерживается",
                    status_code=400,
                    details={"field": name},
                )
            if isinstance(value, UploadFile):
                content = await value.read(25 * 1024 * 1024 + 1)
                if len(content) > 25 * 1024 * 1024:
                    raise AppError(
                        "input_too_large",
                        "Файл превышает 25 МБ",
                        status_code=400,
                        details={"field": name},
                    )
                if is_image_array:
                    body.setdefault(name, []).append(content)
                else:
                    body[name] = content
                files.append((name, (value.filename or name, content, value.content_type)))
            else:
                data[name] = value
                body[name] = value
        return body, data, files


async def _image_form(request: Request, runtime: AppContainer, operation: str) -> Any:
    body, data, files = await _form(request, image_array=operation == "edits")
    if "image" in body and "image[]" in body:
        raise AppError(
            "invalid_parameter", "Укажите image или image[], но не оба поля",
            status_code=400, details={"field": "image[]"},
        )
    if not body.get("model") or not (body.get("image") or body.get("image[]")):
        raise AppError("invalid_request", "Требуются model и image (или image[])", status_code=400)
    service = _service(runtime)
    model = service.get_settings()["models"].get(body["model"])
    if model is None:
        return await _forward(
            request,
            f"/v1/images/{operation}",
            body,
            runtime.proxy_service,
            multipart_data=data,
            multipart_files=files,
        )
    if model["type"] != "image":
        raise AppError("unsupported_operation", "Требуется image-модель", status_code=400)
    if "image[]" in body:
        images = body.pop("image[]")
        if len(images) != 1:
            raise AppError(
                "unsupported_parameter",
                f"Модель {body['model']} поддерживает только одно исходное изображение; "
                f"получено {len(images)}",
                status_code=400, details={"field": "image[]"},
            )
        body["image"] = images[0]
    if operation == "edits" and not body.get("prompt"):
        raise AppError(
            "invalid_request", "Требуется prompt", status_code=400, details={"field": "prompt"}
        )
    for field in ("n", "output_compression", "seed"):
        if field in body:
            try:
                body[field] = int(body[field])
            except ValueError as exc:
                raise AppError(
                    "invalid_parameter",
                    "Требуется целое число",
                    status_code=400,
                    details={"field": field},
                ) from exc
    prefix = (
        "/api/proxy"
        if request.url.path.startswith("/api/proxy/")
        else str(request.base_url).rstrip("/") + "/v1"
    )
    selected = (
        "variations" if operation == "variations" else "edit_masked" if body.get("mask") else "edit"
    )
    return await service.images.generate(
        body, operation=selected, request_id=request.state.request_id, url_prefix=prefix
    )


@router.post(
    "/images/edits",
    openapi_extra=_multipart_schema(
        {
            **_IMAGE_FIELDS,
            "image[]": {
                "type": "array", "minItems": 1,
                "items": {"type": "string", "format": "binary"},
                "description": (
                    "Совместные исходники/референсы одной генерации; "
                    "число зависит от модели."
                ),
            },
        },
        ["model", "prompt"],
        one_of=[{"required": ["image"]}, {"required": ["image[]"]}],
    ),
    responses=IMAGE_RESPONSES,
)
async def image_edit(request: Request, runtime: RuntimeDep) -> Any:
    return await _wire(lambda: _image_form(request, runtime, "edits"))


@router.post(
    "/images/variations",
    responses=IMAGE_RESPONSES,
    openapi_extra=_multipart_schema(
        {name: field for name, field in _IMAGE_FIELDS.items() if name not in {"prompt", "mask"}},
        ["model", "image"],
    ),
)
async def image_variations(request: Request, runtime: RuntimeDep) -> Any:
    return await _wire(lambda: _image_form(request, runtime, "variations"))


@router.get(
    "/images/{asset_id}",
    responses={200: {"content": {"image/png": {}, "image/jpeg": {}, "image/webp": {}}}},
)
async def image_asset(asset_id: str, runtime: RuntimeDep) -> Any:
    async def download() -> Response:
        try:
            content, media_type, filename = await _service(runtime).asset(asset_id)
            if media_type.split(";", 1)[0] not in {
                "image/png",
                "image/jpeg",
                "image/webp",
                "image/gif",
            }:
                raise AppError(
                    "invalid_media_type",
                    "ComfyUI вернул неподдержанный тип изображения",
                    status_code=502,
                )
            return Response(
                content=content,
                media_type=media_type,
                headers={
                    "Content-Disposition": f"inline; filename*=UTF-8''{quote(filename, safe='')}",
                    "X-Content-Type-Options": "nosniff",
                },
            )
        except AppError as exc:
            if exc.status_code != 404:
                raise
            path, media_type = await runtime.image_service.asset(asset_id)
            return FileResponse(path, media_type=media_type)

    return await _wire(download)


@router.post(
    "/videos",
    openapi_extra=_multipart_schema(_VIDEO_FIELDS, ["model", "prompt"]),
    responses=VIDEO_RESPONSES,
)
async def video_create(request: Request, runtime: RuntimeDep) -> Any:
    async def create() -> Any:
        body, _, _ = await _form(request)
        for field in ("seconds", "seed"):
            if field in body:
                try:
                    body[field] = int(body[field])
                except (TypeError, ValueError) as exc:
                    raise AppError("invalid_parameter", "Требуется целое число", status_code=400, details={"field": field}) from exc
        service = _service(runtime)
        model = service.get_settings()["models"].get(body.get("model"))
        if not model or model["type"] != "video":
            raise AppError("model_not_found", "Video-модель не найдена", status_code=404)
        return await service.create_video(body, request_id=request.state.request_id)

    return await _wire(create)


@router.get("/videos/{video_id}", responses=VIDEO_RESPONSES)
async def video_status(video_id: str, runtime: RuntimeDep) -> Any:
    return await _wire(lambda: _service(runtime).video_status(video_id))


@router.get("/videos/{video_id}/content", responses={200: {"content": {"video/mp4": {}}}})
async def video_content(video_id: str, runtime: RuntimeDep) -> Any:
    async def download() -> Response:
        content, media_type = await _service(runtime).video_content(video_id)
        return Response(content=content, media_type=media_type)

    return await _wire(download)


@router.get("/videos")
async def video_list(runtime: RuntimeDep) -> Any:
    async def listing() -> dict[str, Any]:
        return {"object": "list", "data": await _service(runtime).video_list()}

    return await _wire(listing)


@router.delete(
    "/videos/{video_id}",
    description=(
        "Delete the API resource reference. The ComfyUI output file is retained; "
        "running prompts are not interrupted."
    ),
)
async def video_delete(video_id: str, runtime: RuntimeDep) -> Any:
    async def delete() -> dict[str, Any]:
        return {
            "id": video_id,
            "object": "video.deleted",
            "deleted": await _service(runtime).video_delete(video_id),
        }

    return await _wire(delete)


for path, endpoint, methods in (
    ("/models", models, ["GET"]),
    ("/models/{model_id:path}", model_detail, ["GET"]),
    ("/images/generations", image_generation, ["POST"]),
    ("/images/edits", image_edit, ["POST"]),
    ("/images/variations", image_variations, ["POST"]),
    ("/images/{asset_id}", image_asset, ["GET"]),
    ("/videos", video_create, ["POST"]),
    ("/videos", video_list, ["GET"]),
    ("/videos/{video_id}", video_status, ["GET"]),
    ("/videos/{video_id}", video_delete, ["DELETE"]),
    ("/videos/{video_id}/content", video_content, ["GET"]),
):
    internal_router.add_api_route(path, endpoint, methods=methods)
