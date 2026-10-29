"""A bounded chat/tool loop. Model transport and media execution stay in their services."""

from __future__ import annotations

import asyncio
import base64
import binascii
import codecs
import copy
import json
import logging
import time
from typing import Any, Literal
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.responses import StreamingResponse

from ..documents import MAX_PDF_BYTES, PdfRenderer, image_part, unlimited_ocr_request
from ..errors import AppError
from ..proxy.comfy_provider import ComfyProviderService
from ..proxy.service import ProxyService
from .store import ChatStore
from .tools import ChatTools, ImageLookup, MediaArguments

logger = logging.getLogger(__name__)
MAX_STEPS = 50
MAX_IMAGE_BYTES = 8 * 1024 * 1024
INSTRUCTIONS = """You are the user's assistant in Neural Deck. Reply in the user's language.
Use the supplied media tools when the user asks you to create or edit images or videos.
The user does not need to mention tools. If they only ask to write or discuss a prompt,
reply with text without generating media.
Use exact model names and image IDs from the tool schemas, user attachments, or tool results.
Never invent asset IDs, model names, results, or claims that you have visually inspected media.
Tool results identify real generated media which the application displays automatically.
Use get_image to inspect an existing image before answering questions about its visual
content or quality. Its image observation is for analysis, not a new generation request.
Omit optional size/background/seconds unless requested; saved user settings will be used.
Ask a brief clarification if the requested source image or model choice is ambiguous.
Do not generate extra media, retry a failed generation, or run alternative generators unless
the user requests it. You may correct arguments rejected before execution.
"""


class ChatTurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    model: str = Field(min_length=1, max_length=256)
    prompt: str = Field(max_length=100_000)
    content: str | list[dict[str, Any]]
    image_names: list[str] = Field(default_factory=list, max_length=8)
    reasoning: dict[str, Any] = Field(default_factory=dict)
    media_defaults: dict[str, dict[str, Any]] = Field(default_factory=dict)
    operation: Literal["chat", "ocr"] = "chat"


class ChatService:
    def __init__(self, proxy: ProxyService, comfy: ComfyProviderService, store: ChatStore) -> None:
        self.proxy = proxy
        self.store = store
        self.tools = ChatTools(comfy, store)
        self.pdf = PdfRenderer()
        self._states: dict[str, dict[str, Any]] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()
        self._closing = False

    async def initialize(self) -> None:
        await self.store.initialize()

    async def close(self) -> None:
        self._closing = True
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.store.close()

    def _launch(self, state: dict[str, Any]) -> None:
        conversation_id = state["id"]
        task = self._tasks.get(conversation_id)
        if state["pending"] and (task is None or task.done()):
            self._tasks[conversation_id] = asyncio.create_task(
                self._run(state),
                name=f"chat-{conversation_id}",
            )

    async def _load(self, conversation_id: str) -> dict[str, Any] | None:
        # Check persisted ownership even for cached chats: clearing chats.sqlite3
        # must not revive old history from this process's memory on the next send.
        saved = await self.store.get(conversation_id)
        if saved is None:
            self._states.pop(conversation_id, None)
            return None
        if conversation_id not in self._states:
            self._states[conversation_id] = saved
        return self._states[conversation_id]

    @staticmethod
    def public(state: dict[str, Any]) -> dict[str, Any]:
        return copy.deepcopy(
            {"id": state["id"], "turns": state["turns"], "busy": state["pending"] is not None}
        )

    async def get(self, conversation_id: str) -> dict[str, Any]:
        async with self._lock:
            state = await self._load(conversation_id)
            if state is None:
                raise AppError("chat_not_found", "Диалог не найден", status_code=404)
            self._launch(state)
            return self.public(state)

    async def list_conversations(self) -> dict[str, Any]:
        return {"items": await self.store.list_conversations()}

    async def delete(self, conversation_id: str) -> dict[str, Any]:
        async with self._lock:
            if await self._load(conversation_id) is None:
                raise AppError("chat_not_found", "Диалог не найден", status_code=404)
            task = self._tasks.get(conversation_id)
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await self.store.delete_conversation(conversation_id)
            self._states.pop(conversation_id, None)
            self._tasks.pop(conversation_id, None)
            return {"id": conversation_id, "deleted": True}

    async def models(self) -> dict[str, Any]:
        return await self.tools.comfy.extend_models(await self.proxy.models())

    async def _operation(self, request: ChatTurnRequest, attachments: list[dict[str, str]]) -> str:
        model = self.tools.comfy.get_settings()["models"].get(request.model)
        metadata: dict[str, Any] = {}
        if model is None:
            catalog = await self.proxy.models()
            metadata = next(
                (item for item in catalog.get("data", []) if item["id"] == request.model), {}
            )
            types = metadata.get("types", [])
            if "image" in types and "text" not in types:
                model = {
                    "type": "image",
                    "workflows": {
                        name: True
                        for name in (metadata.get("capabilities") or {}).get("operations", [])
                    },
                }
        if model is not None:
            images = [item for item in attachments if item["type"].startswith("image/")]
            if len(images) != len(attachments):
                raise AppError(
                    "invalid_chat_attachment", "Для генерации прикрепите только изображения"
                )
            if model["type"] == "video":
                if not request.prompt.strip() or len(images) > 1:
                    raise AppError(
                        "invalid_chat_message",
                        "Опишите видео и прикрепите не больше одного изображения",
                    )
                return "video"
            if len(images) > 2:
                raise AppError(
                    "invalid_chat_attachment", "Прикрепите изображение и, при необходимости, маску"
                )
            if not images:
                if not request.prompt.strip():
                    raise AppError("invalid_chat_message", "Опишите изображение")
                return "image-generation"
            has_prompt = (
                bool(request.content.strip())
                if isinstance(request.content, str)
                else any(
                    part.get("type") == "text" and str(part.get("text", "")).strip()
                    for part in request.content
                )
            )
            if not has_prompt:
                if len(images) != 1 or "variations" not in model.get("workflows", {}):
                    raise AppError(
                        "invalid_chat_message", "Добавьте описание изменений изображения"
                    )
                return "image-variations"
            return "image-edit"
        if request.operation == "ocr":
            return "ocr"
        if (
            "ocr" in metadata.get("types", [])
            or (metadata.get("capabilities") or {}).get("document_adapter") == "unlimited_ocr_2"
        ):
            return "ocr"
        return "chat"

    async def start(self, conversation_id: str, request: ChatTurnRequest) -> dict[str, Any]:
        async with self._lock:
            state = await self._load(conversation_id)
            if state is None:
                state = {"id": conversation_id, "messages": [], "turns": [], "pending": None}
                self._states[conversation_id] = state
            # A retry after a lost POST response must not create a second media generation.
            if any(turn["id"] == request.id for turn in state["turns"]):
                self._launch(state)
                return self.public(state)
            if state["pending"]:
                raise AppError(
                    "chat_busy", "В этом диалоге уже выполняется запрос", status_code=409
                )
            content, attachments = await self._content(conversation_id, request)
            if not content:
                raise AppError("invalid_chat_message", "Введите сообщение или прикрепите файл")
            operation = await self._operation(request, attachments)
            turn = {
                "id": request.id,
                "prompt": request.prompt,
                "model": request.model,
                "answer": "",
                "reasoning": "",
                "results": [],
                "attachments": attachments,
                "state": "streaming",
                "phase": "thinking",
                "created_at": time.time(),
            }
            if operation != "chat":
                turn["operation"] = operation
            state["turns"].append(turn)
            state["messages"].append({"role": "user", "content": content})
            state["pending"] = {
                "turn_id": request.id,
                "model": request.model,
                "reasoning": {
                    key: value
                    for key, value in request.reasoning.items()
                    if key in {"reasoning_effort", "chat_template_kwargs"}
                },
                "defaults": request.media_defaults,
                "step": 0,
                "calls": [],
                "operation": operation,
            }
            await self.store.save(state)
            self._launch(state)
            return self.public(state)

    async def stop(self, conversation_id: str) -> dict[str, Any]:
        async with self._lock:
            state = await self._load(conversation_id)
            if state is None:
                raise AppError("chat_not_found", "Диалог не найден", status_code=404)
            task = self._tasks.get(conversation_id)
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            elif state["pending"]:
                await self._finish(state, "cancelled")
            return self.public(state)

    async def _content(
        self,
        conversation_id: str,
        request: ChatTurnRequest,
    ) -> tuple[str | list[dict[str, Any]], list[dict[str, str]]]:
        if isinstance(request.content, str):
            if len(request.content) > 100_000:
                raise AppError("chat_message_too_large", "Текст сообщения слишком большой")
            return request.content, []
        if len(request.content) > 32:
            raise AppError("chat_message_too_large", "Слишком много вложений")
        parts: list[dict[str, Any]] = []
        attachments: list[dict[str, str]] = []
        for part in request.content:
            if part.get("type") == "text" and isinstance(part.get("text"), str):
                if len(part["text"]) > 100_000:
                    raise AppError("chat_message_too_large", "Текст вложения слишком большой")
                parts.append({"type": "text", "text": part["text"]})
                continue
            if part.get("type") == "file" and isinstance(part.get("file"), dict):
                file = part["file"]
                source = file.get("file_data")
                if not isinstance(source, str) or len(source) > MAX_PDF_BYTES * 4 // 3 + 128:
                    raise AppError("pdf_too_large", "Максимальный размер PDF — 25 МБ")
                try:
                    header, encoded = source.split(",", 1)
                    if header != "data:application/pdf;base64":
                        raise ValueError
                    data = base64.b64decode(encoded, validate=True)
                    if (
                        b"%PDF-" not in data[:1024]
                        or len(data) > MAX_PDF_BYTES
                        or len(attachments) >= 8
                    ):
                        raise ValueError
                except (ValueError, binascii.Error) as exc:
                    raise AppError(
                        "invalid_pdf", "Некорректный PDF или превышен лимит файла"
                    ) from exc
                name = file.get("filename", "document.pdf")
                if not isinstance(name, str) or not name.strip():
                    raise AppError("invalid_pdf", "Не задано имя PDF")
                name = name[:256]
                asset_id = await self.store.attach(
                    conversation_id, name, "application/pdf", content=data
                )
                attachments.append(
                    {
                        "name": name,
                        "type": "application/pdf",
                        "previewUrl": self.store.asset_url(conversation_id, asset_id),
                    }
                )
                parts.append({"type": "file", "file": {"file_id": asset_id}})
                continue
            if part.get("type") != "image_url" or not isinstance(part.get("image_url"), dict):
                raise AppError("invalid_chat_attachment", "Неподдерживаемое вложение")
            url = part["image_url"].get("url")
            if not isinstance(url, str) or len(url) > MAX_IMAGE_BYTES * 4 // 3 + 128:
                raise AppError("invalid_chat_attachment", "Изображение превышает 8 МБ")
            try:
                header, encoded = url.split(",", 1)
                if header not in {
                    f"data:image/{kind};base64"
                    for kind in ("png", "jpeg", "webp", "gif", "avif", "bmp")
                }:
                    raise ValueError
                content = base64.b64decode(encoded, validate=True)
                if not content or len(content) > MAX_IMAGE_BYTES or len(attachments) >= 8:
                    raise ValueError
            except (ValueError, binascii.Error) as exc:
                raise AppError(
                    "invalid_chat_attachment", "Некорректное изображение или превышен лимит 8 МБ"
                ) from exc
            media_type = header[5:-7]
            image_index = sum(item["type"].startswith("image/") for item in attachments)
            name = (
                request.image_names[image_index][:256]
                if len(request.image_names) > image_index
                else "image"
            )
            asset_id = await self.store.attach(conversation_id, name, media_type, content=content)
            attachments.append(
                {
                    "name": name,
                    "type": media_type,
                    "previewUrl": self.store.asset_url(conversation_id, asset_id),
                }
            )
            parts.append({"type": "text", "text": f"Attached image: {name}; source_id={asset_id}"})
            parts.append({"type": "image_url", "image_url": {"url": f"asset:{asset_id}"}})
        return parts, attachments

    async def _expand_content(self, state: dict[str, Any], content: Any) -> Any:
        if not isinstance(content, list):
            return content
        parts: list[dict[str, Any]] = []
        for part in content:
            if part.get("type") == "file":
                asset = await self.store.attachment(state["id"], part["file"]["file_id"])
                self._turn(state)["phase"] = "preparing_pdf"
                images = await self.pdf.render(asset["content"])
                parts.append(
                    {"type": "text", "text": f"PDF: {asset['name']}; pages: {len(images)}"}
                )
                for number, page in enumerate(images, 1):
                    parts.extend(
                        [{"type": "text", "text": f"Page {number}/{len(images)}"}, image_part(page)]
                    )
            elif part.get("type") == "image_url":
                asset_id = part["image_url"]["url"].removeprefix("asset:")
                parts.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": await self.tools.image_input(state["id"], asset_id)},
                    }
                )
            else:
                parts.append(copy.deepcopy(part))
        return parts

    async def _messages(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        messages = copy.deepcopy(state["messages"])
        for message in messages:
            message["content"] = await self._expand_content(state, message.get("content"))
        self._turn(state)["phase"] = "thinking"
        return [{"role": "system", "content": INSTRUCTIONS}, *messages]

    @staticmethod
    def _turn(state: dict[str, Any]) -> dict[str, Any]:
        return next(turn for turn in state["turns"] if turn["id"] == state["pending"]["turn_id"])

    async def _finish(self, state: dict[str, Any], status: str, error: str | None = None) -> None:
        pending = state["pending"]
        turn = self._turn(state)
        # Keep assistant tool_calls paired with tool replies even after Stop or an error.
        for call in pending["calls"]:
            if call["state"] != "done":
                state["messages"].append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": json.dumps({"error": error or "Выполнение остановлено"}),
                    }
                )
                call["state"] = "done"
        turn.update(state=status, phase=None, elapsed=(time.time() - turn["created_at"]) * 1000)
        if error:
            turn["error"] = error
        state["pending"] = None
        await self.store.save(state)

    async def _run(self, state: dict[str, Any]) -> None:
        try:
            operation = state["pending"].get("operation", "chat")
            if operation in {"image-generation", "image-edit", "image-variations", "video"}:
                await self._direct_media(state, operation)
                await self._finish(state, "complete")
                return
            if operation == "ocr":
                await self._ocr(state)
                await self._finish(state, "complete")
                return
            while state["pending"]:
                pending = state["pending"]
                turn = self._turn(state)
                if pending["calls"]:
                    for call in pending["calls"]:
                        if call["state"] == "done":
                            continue
                        await self._tool(state, call)
                    image_ids = pending.pop("view_images", [])
                    if image_ids:
                        # Chat Completions tool replies carry text. Supply the pixels as
                        # a standard image message after ALL replies in this tool batch.
                        state["messages"].append(
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "text",
                                        "text": (
                                            "Images requested by get_image for the current "
                                            "question. These are observations, not a new "
                                            "request to generate media."
                                        ),
                                    },
                                    *[
                                        {
                                            "type": "image_url",
                                            "image_url": {"url": f"asset:{asset_id}"},
                                        }
                                        for asset_id in image_ids
                                    ],
                                ],
                            }
                        )
                    pending["calls"] = []
                    await self.store.save(state)
                if pending["step"] >= MAX_STEPS:
                    raise AppError("chat_step_limit", "Достигнут лимит действий за один запрос")
                turn["phase"] = "thinking"
                # A restarted LLM request has no side effects. Restore its partial display first.
                checkpoint = pending.setdefault(
                    "display", {"answer": turn["answer"], "reasoning": turn["reasoning"]}
                )
                turn.update(checkpoint)
                await self.store.save(state)
                message = await self._completion(state)
                pending.pop("display", None)
                state["messages"].append(message)
                pending["step"] += 1
                calls = message.get("tool_calls", [])
                if not calls:
                    await self._finish(state, "complete")
                    return
                if len(calls) > 4 or len({call["id"] for call in calls}) != len(calls):
                    raise AppError(
                        "invalid_tool_calls", "Модель вернула некорректный список инструментов"
                    )
                pending["calls"] = [{**call, "state": "queued"} for call in calls]
                if turn["answer"]:
                    turn["answer"] += "\n\n"
                await self.store.save(state)
        except asyncio.CancelledError:
            if not self._closing and state["pending"]:
                await self._finish(state, "cancelled")
            raise
        except Exception as exc:
            logger.error(
                "Chat run failed: conversation=%s error_type=%s", state["id"], type(exc).__name__
            )
            error = (
                exc.message if isinstance(exc, AppError) else "Не удалось выполнить запрос к модели"
            )
            if state["pending"]:
                await self._finish(state, "error", error)

    async def _direct_media(self, state: dict[str, Any], operation: str) -> None:
        pending = state["pending"]
        if not pending["calls"]:
            images = [
                item["previewUrl"].rsplit("/", 1)[-1]
                for item in self._turn(state)["attachments"]
                if item["type"].startswith("image/")
            ]
            name = (
                "generate_video"
                if operation == "video"
                else "edit_image"
                if operation == "image-edit"
                else "generate_image"
            )
            raw = {
                "model": pending["model"],
                "prompt": self._turn(state)["prompt"] or "Вариация изображения",
            }
            if images:
                raw["source_id"] = images[0]
            if len(images) > 1:
                raw["mask_id"] = images[1]
            call = {
                "id": "direct_" + uuid4().hex,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(raw, ensure_ascii=False)},
            }
            state["messages"].append({"role": "assistant", "content": "", "tool_calls": [call]})
            pending["calls"] = [{**call, "state": "queued", "direct_operation": operation}]
            await self.store.save(state)
        for call in pending["calls"]:
            if call["state"] != "done":
                await self._tool(state, call)

    async def _ocr(self, state: dict[str, Any]) -> None:
        if await self._document_adapter(state) == "unlimited_ocr_2":
            state["messages"].append(await self._completion(state))
            return
        content = await self._expand_content(state, state["messages"][-1]["content"])
        images = (
            [part["image_url"]["url"] for part in content if part.get("type") == "image_url"]
            if isinstance(content, list)
            else []
        )
        if not images:
            raise AppError("document_required", "Прикрепите PDF или изображение для распознавания")
        pending = state["pending"]
        pages = pending.setdefault("ocr_pages", [])
        for index in range(len(pages), len(images)):
            self._turn(state)["phase"] = "reading_document"
            result = await self.proxy.forward(
                route="/v1/ocr",
                payload={
                    "model": pending["model"],
                    "document": {"type": "image_url", "image_url": images[index]},
                },
                headers={"authorization": "Bearer l-chat"},
                request_id=f"ocr_{uuid4().hex}",
                force_usage=True,
            )
            body = result.content if isinstance(result, httpx.Response) else result.body
            if result.status_code >= 400:
                raise AppError("ocr_upstream_error", f"OCR-сервер вернул HTTP {result.status_code}")
            value = json.loads(body)
            if not isinstance(value, dict):
                raise AppError("invalid_ocr_response", "OCR-сервер вернул неверный ответ")
            if isinstance(value.get("data"), dict):
                value = value["data"]
            output = value.get("pages")
            if (
                not isinstance(output, list)
                or not output
                or any(
                    not isinstance(page, dict) or not isinstance(page.get("markdown"), str)
                    for page in output
                )
            ):
                raise AppError("invalid_ocr_response", "OCR-сервер не вернул страницы")
            pages.append("\n\n".join(page["markdown"] for page in output))
            await self.store.save(state)
        markdown = (
            "\n\n".join(f"## Страница {index}\n\n{text}" for index, text in enumerate(pages, 1))
            if len(pages) > 1
            else pages[0]
        )
        self._turn(state)["results"].append({"kind": "ocr", "markdown": markdown})
        state["messages"].append({"role": "assistant", "content": markdown})

    async def _tool(self, state: dict[str, Any], call: dict[str, Any]) -> None:
        pending = state["pending"]
        turn = self._turn(state)
        name = call["function"]["name"]
        if call["state"] == "running":
            # An interrupted POST may already have reached ComfyUI. Never replay it.
            raise AppError(
                "chat_execution_interrupted",
                "Запуск генерации был прерван. Его результат неизвестен; "
                "повторный запуск автоматически не выполнялся.",
            )
        try:
            raw = json.loads(call["function"]["arguments"])
            # A known video job belongs to its original source; config edits must not
            # prevent resuming it or cause another submission.
            direct = call.get("direct_operation")
            params = (
                {}
                if call.get("video_id") or name == "get_image"
                else self.tools.parameters(name, raw, pending["defaults"])
                if not direct
                else {
                    "model": raw["model"],
                    "prompt": raw["prompt"],
                    **pending["defaults"].get(raw["model"], {}),
                }
            )
            if direct:
                MediaArguments.model_validate({**raw, **params})
            turn["phase"] = name
            result = None
            if name == "get_image":
                image_id = ImageLookup.model_validate(raw).image_id
                asset = await self.store.attachment(state["id"], image_id)
                if not asset["media_type"].startswith("image/"):
                    raise AppError("invalid_tool_arguments", "Для просмотра требуется изображение")
                images = pending.setdefault("view_images", [])
                if image_id not in images:
                    images.append(image_id)
                output = {"image_id": image_id, "image_input_follows": True}
            elif name == "generate_video":
                if not call.get("video_id"):
                    if raw.get("source_id"):
                        params["input_reference"] = await self.tools.image_input(
                            state["id"], raw["source_id"]
                        )
                    call["state"] = "running"
                    await self.store.save(state)
                    created = await self.tools.comfy.create_video(params, request_id=call["id"])
                    call.update(video_id=created["id"], state="waiting")
                    await self.store.save(state)
                await self._wait_video(state, call)
                video_id = call["video_id"]
                output = {"video_id": video_id, "url": f"/api/proxy/videos/{video_id}/content"}
                result = {"kind": "video", **output, "id": video_id, "downloadUrl": output["url"]}
            else:
                call["state"] = "running"
                await self.store.save(state)
                if direct and raw["model"] not in self.tools.comfy.get_settings()["models"]:
                    output, result = await self._proxy_image(state, direct, raw, params, call["id"])
                else:
                    output, result = await self.tools.image(
                        state["id"],
                        name,
                        raw,
                        params,
                        call["id"],
                        operation="variations" if direct == "image-variations" else None,
                    )
            if result is not None:
                turn["results"].append(result)
        except (AppError, ValidationError, ValueError, TypeError) as exc:
            # Runtime failures stop the turn: asking the LLM again could repeat a costly POST.
            if call["state"] in {"running", "waiting"} or call.get("direct_operation"):
                raise
            output = {
                "error": exc.message
                if isinstance(exc, AppError)
                else "Некорректные аргументы инструмента"
            }
        state["messages"].append(
            {
                "role": "tool",
                "tool_call_id": call["id"],
                "content": json.dumps(output, ensure_ascii=False),
            }
        )
        call["state"] = "done"
        await self.store.save(state)

    async def _proxy_image(
        self,
        state: dict[str, Any],
        operation: str,
        raw: dict[str, Any],
        params: dict[str, Any],
        request_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        suffix = {
            "image-generation": "generations",
            "image-edit": "edits",
            "image-variations": "variations",
        }[operation]
        files = {}
        if raw.get("source_id"):
            content, media_type, name = await self.tools.asset(state["id"], raw["source_id"])
            files["image"] = (name, content, media_type)
        if raw.get("mask_id"):
            content, media_type, name = await self.tools.asset(state["id"], raw["mask_id"])
            files["mask"] = (name, content, media_type)
        if operation == "image-variations":
            params = {key: value for key, value in params.items() if key != "prompt"}
        result = await self.proxy.forward(
            route=f"/v1/images/{suffix}",
            payload=params,
            headers={"authorization": "Bearer l-chat"},
            request_id=request_id,
            multipart_data=params if files else None,
            multipart_files=files or None,
        )
        body = result.content if isinstance(result, httpx.Response) else result.body
        if result.status_code >= 400:
            raise AppError(
                "image_generation_failed", f"Сервер изображений вернул HTTP {result.status_code}"
            )
        value = json.loads(body)
        data = value.get("data") if isinstance(value, dict) else None
        if not isinstance(data, list) or not data:
            raise AppError("invalid_tool_result", "Генератор не вернул изображения")
        assets = []
        for item in data:
            if not isinstance(item, dict):
                raise AppError("invalid_tool_result", "Генератор вернул неверное изображение")
            if isinstance(item.get("b64_json"), str):
                try:
                    image = base64.b64decode(item["b64_json"], validate=True)
                except (ValueError, binascii.Error) as exc:
                    raise AppError(
                        "invalid_tool_result", "Генератор вернул неверный base64"
                    ) from exc
                asset_id = await self.store.attach(
                    state["id"], "image.png", "image/png", content=image
                )
                assets.append({"id": asset_id, "url": self.store.asset_url(state["id"], asset_id)})
            elif isinstance(item.get("url"), str) and item["url"]:
                # Standard URL-only results stay URLs; no invented owned image ID.
                assets.append({"url": item["url"]})
            else:
                raise AppError("invalid_tool_result", "Изображение не содержит url или b64_json")
        return {"images": assets}, {"kind": "image", "assets": assets}

    async def _wait_video(self, state: dict[str, Any], call: dict[str, Any]) -> None:
        while True:
            try:
                async with asyncio.timeout(20):
                    status = await self.tools.comfy.video_status(call["video_id"])
                self._turn(state)["phase"] = "generate_video"
            except (TimeoutError, httpx.TransportError):
                self._turn(state)["phase"] = "reconnecting"
                await asyncio.sleep(3)
                continue
            except AppError as exc:
                if exc.status_code not in {408, 429, 500, 502, 503, 504}:
                    raise
                self._turn(state)["phase"] = "reconnecting"
                await asyncio.sleep(3)
                continue
            if status["status"] == "completed":
                return
            if status["status"] == "failed":
                raise AppError(
                    "video_generation_failed",
                    (status.get("error") or {}).get("message", "Не удалось создать видео"),
                )
            await asyncio.sleep(2)

    async def _document_adapter(self, state: dict[str, Any]) -> str | None:
        pending = state["pending"]
        if "document_adapter" not in pending:
            catalog = await self.proxy.models()
            model: dict[str, Any] = next(
                (item for item in catalog.get("data", []) if item["id"] == pending["model"]), {}
            )
            pending["document_adapter"] = (model.get("capabilities") or {}).get("document_adapter")
        adapter = pending["document_adapter"]
        return adapter if isinstance(adapter, str) else None

    async def _completion(self, state: dict[str, Any]) -> dict[str, Any]:
        pending = state["pending"]
        turn = self._turn(state)
        definitions = self.tools.definitions()
        adapter = await self._document_adapter(state)
        messages = (
            [
                {
                    "role": "user",
                    "content": await self._expand_content(state, state["messages"][-1]["content"]),
                }
            ]
            if adapter == "unlimited_ocr_2"
            else await self._messages(state)
        )
        payload = {
            "model": pending["model"],
            "messages": messages,
            "stream": True,
            **pending["reasoning"],
        }
        if definitions:
            payload.update(tools=definitions, tool_choice="auto")
        if adapter == "unlimited_ocr_2":
            payload = unlimited_ocr_request(pending["model"], messages[-1]["content"])
        response = await self.proxy.forward(
            route="/v1/chat/completions",
            payload=payload,
            headers={"authorization": "Bearer l-chat"},
            request_id=f"chat_{uuid4().hex}",
            force_usage=True,
        )
        if response.status_code >= 400:
            body = response.content if isinstance(response, httpx.Response) else response.body
            try:
                error = json.loads(body)["error"]["message"]
            except (ValueError, TypeError, KeyError):
                error = f"Сервер модели вернул HTTP {response.status_code}"
            raise AppError("chat_upstream_error", str(error), status_code=502)
        message: dict[str, Any] = {"role": "assistant", "content": ""}
        calls: dict[int, dict[str, Any]] = {}
        finish_reason: str | None = None
        last_save = time.monotonic()

        async def consume(payload: dict[str, Any], streaming: bool) -> None:
            nonlocal finish_reason, last_save
            if payload.get("error"):
                raise AppError("chat_upstream_error", "Сервер модели вернул ошибку в потоке")
            if payload.get("usage"):
                turn["usage"] = payload["usage"]
            if payload.get("timings"):
                turn["timings"] = payload["timings"]
            choice: dict[str, Any] = next(iter(payload.get("choices", [])), {})
            delta = choice.get("delta" if streaming else "message", {}) or {}
            for field, display in (
                ("content", "answer"),
                ("reasoning_content", "reasoning"),
                ("reasoning", "reasoning"),
            ):
                value = delta.get(field)
                if isinstance(value, str) and value:
                    message[field] = message.get(field, "") + value
                    turn[display] += value
                    turn.setdefault("firstToken", (time.time() - turn["created_at"]) * 1000)
            for index, fragment in enumerate(delta.get("tool_calls", [])):
                number = fragment.get("index", index)
                if not isinstance(number, int) or number < 0 or number >= 4:
                    raise AppError("invalid_tool_calls", "Модель вернула слишком много вызовов")
                call = calls.setdefault(
                    number,
                    {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
                )
                if fragment.get("id"):
                    call["id"] += fragment["id"]
                function = fragment.get("function", {})
                for field in ("name", "arguments"):
                    if isinstance(function.get(field), str):
                        call["function"][field] += function[field]
            if choice.get("finish_reason"):
                finish_reason = choice["finish_reason"]
            if time.monotonic() - last_save >= 1:
                await self.store.save(state)
                last_save = time.monotonic()

        if "text/event-stream" not in response.headers.get("content-type", ""):
            body = response.content if isinstance(response, httpx.Response) else response.body
            await consume(json.loads(body), False)
            finish_reason = finish_reason or "stop"
        else:
            decoder = codecs.getincrementaldecoder("utf-8")()
            buffer = ""
            event: list[str] = []

            async def line(value: str) -> None:
                if not value:
                    if event:
                        raw = "\n".join(event)
                        event.clear()
                        if raw.strip() != "[DONE]":
                            await consume(json.loads(raw), True)
                elif value.startswith("data:"):
                    event.append(value[5:].lstrip())

            if not isinstance(response, StreamingResponse):
                raise AppError("invalid_chat_response", "Сервер модели не вернул поток ответа")
            iterator = response.body_iterator
            try:
                async for chunk in iterator:
                    buffer += chunk if isinstance(chunk, str) else decoder.decode(chunk)
                    while "\n" in buffer:
                        value, buffer = buffer.split("\n", 1)
                        await line(value.rstrip("\r"))
                buffer += decoder.decode(b"", final=True)
                if buffer:
                    await line(buffer.rstrip("\r"))
                await line("")
            finally:
                close = getattr(iterator, "aclose", None)
                if close is not None:
                    await close()
        if finish_reason is None or (calls and finish_reason not in {"tool_calls", "stop"}):
            raise AppError(
                "chat_incomplete_response",
                "Ответ модели прервался. Генерация по незавершённому "
                "вызову инструмента не запускалась.",
            )
        if calls:
            ordered = [calls[index] for index in sorted(calls)]
            if any(not call["id"] or not call["function"]["name"] for call in ordered):
                raise AppError(
                    "invalid_tool_calls", "Модель вернула незавершённый вызов инструмента"
                )
            message["tool_calls"] = ordered
        turn["finishReason"] = finish_reason
        return message
