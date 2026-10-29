import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from starlette.responses import StreamingResponse

from app.chat.service import ChatService, ChatTurnRequest
from app.chat.store import ChatStore
from app.errors import AppError


def reply(content="Готово", calls=None):
    return httpx.Response(200, headers={"content-type": "application/json"}, json={
        "choices": [{"message": {"role": "assistant", "content": content,
                                  **({"tool_calls": calls} if calls else {})},
                     "finish_reason": "tool_calls" if calls else "stop"}],
        "usage": {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25},
    })


def tool(name="generate_image", arguments=None, call_id="call-image"):
    return {"id": call_id, "type": "function", "function": {
        "name": name, "arguments": json.dumps(arguments or {"model": "image", "prompt": "cat"}),
    }}


def request(turn_id="first", **values):
    return ChatTurnRequest(id=turn_id, model="llm", prompt="Нарисуй кота",
                           content=values.pop("content", "Нарисуй кота"), **values)


@pytest.fixture
async def harness(tmp_path):
    store = ChatStore(tmp_path / "chats.sqlite3")
    proxy = SimpleNamespace(forward=AsyncMock(return_value=reply()), models=AsyncMock(return_value={"data": []}))
    comfy = SimpleNamespace(
        get_settings=lambda: {"models": {
            "image": {"type": "image", "max_megapixels": 8,
                      "workflows": {"generate": "g.json", "edit": "e.json"}},
            "video": {"type": "video", "max_resolution": 768, "max_seconds": 15,
                      "workflows": {"generate": "v.json"}},
        }},
        images=SimpleNamespace(generate=AsyncMock(return_value={
            "data": [{"url": "/api/proxy/images/provider-image"}],
        })),
        asset=AsyncMock(return_value=(b"image", "image/png", "image.png")),
        create_video=AsyncMock(return_value={"id": "video_existing", "status": "queued"}),
        video_status=AsyncMock(return_value={"id": "video_existing", "status": "completed"}),
    )
    async def extend_models(catalog):
        return {**catalog, "data": [*catalog.get("data", []), *[
            {"id": name, "object": "model", "types": [model["type"]], "capabilities": {"operations": list(model["workflows"])}}
            for name, model in comfy.get_settings()["models"].items()
        ]]}
    comfy.extend_models = extend_models
    service = ChatService(proxy, comfy, store)
    await service.initialize()
    yield service, proxy, comfy, store
    await service.close()


async def finish(service, conversation_id="chat-one"):
    await service._tasks[conversation_id]
    return await service.get(conversation_id)


async def test_real_tool_history_image_result_and_followup_edit(harness):
    service, proxy, comfy, store = harness
    proxy.forward.side_effect = [reply("Создаю", [tool()]), reply("Вот кот")]
    await service.start("chat-one", request(media_defaults={"image": {
        "size": "1536x2752", "background": "transparent",
    }}))
    result = await finish(service)
    assert result["turns"][0]["state"] == "complete"
    assert result["turns"][0]["answer"] == "Создаю\n\nВот кот"
    image = result["turns"][0]["results"][0]["assets"][0]
    assert image["url"].startswith("/api/v1/chat/chat-one/assets/")
    assert comfy.images.generate.call_args.args[0]["size"] == "1536x2752"
    assert comfy.images.generate.call_args.args[0]["background"] == "transparent"
    forwarded = proxy.forward.call_args.kwargs["payload"]
    assert forwarded["messages"][-1]["role"] == "tool"
    assert forwarded["messages"][-1]["tool_call_id"] == "call-image"
    assert json.loads(forwarded["messages"][-1]["content"])["images"][0]["id"] == image["id"]
    assert {item["function"]["name"] for item in forwarded["tools"]} == {
        "generate_image", "edit_image", "generate_video", "get_image",
    }
    proxy.forward.side_effect = [reply("", [tool("edit_image", {
        "model": "image", "prompt": "make blue", "source_id": image["id"],
    }, "call-edit")]), reply("Изменил")]
    await service.start("chat-one", request("second"))
    edited = await finish(service)
    assert edited["turns"][1]["state"] == "complete"
    assert comfy.images.generate.call_args.kwargs["operation"] == "edit"
    assert comfy.images.generate.call_args.args[0]["image"].startswith("data:image/png;base64,")
    assert (await store.get("chat-one"))["messages"][-1]["content"] == "Изменил"


async def test_duplicate_send_is_idempotent(harness):
    service, proxy, comfy, _ = harness
    proxy.forward.side_effect = [reply("", [tool()]), reply()]
    await service.start("chat-one", request())
    await finish(service)
    duplicate = await service.start("chat-one", request())
    assert len(duplicate["turns"]) == 1
    assert proxy.forward.await_count == 2
    comfy.images.generate.assert_awaited_once()


async def test_asset_cannot_be_read_or_edited_from_another_conversation(harness):
    service, proxy, comfy, store = harness
    asset_id = await store.attach("chat-other", "private.png", "image/png", content=b"private")
    with pytest.raises(AppError, match="не найдено"):
        await service.tools.image_input("chat-one", asset_id)
    proxy.forward.return_value = reply("", [tool("edit_image", {
        "model": "image", "prompt": "edit", "source_id": asset_id,
    })])
    await service.start("chat-one", request())
    result = await finish(service)
    assert result["turns"][0]["state"] == "error"
    comfy.images.generate.assert_not_awaited()


async def test_limits_are_checked_before_generation_and_model_receives_error(harness):
    service, proxy, comfy, _ = harness
    proxy.forward.side_effect = [reply("", [tool("generate_video", {
        "model": "video", "prompt": "city", "seconds": 30,
    })]), reply("Максимум 15 секунд")]
    await service.start("chat-one", request())
    result = await finish(service)
    assert result["turns"][0]["state"] == "complete"
    assert "error" in json.loads(proxy.forward.call_args.kwargs["payload"]["messages"][-1]["content"])
    comfy.create_video.assert_not_awaited()


async def test_video_reference_requires_an_alias_with_edit_support(harness):
    service, _, comfy, _ = harness
    with pytest.raises(AppError, match="workflow Edit"):
        service.tools.parameters("generate_video", {
            "model": "video", "prompt": "animate", "source_id": "existing-image",
        }, {})
    comfy.create_video.assert_not_awaited()


async def test_fragmented_stream_collects_tool_arguments_before_executing(harness):
    service, proxy, comfy, _ = harness
    async def chunks():
        events = [
            {"choices": [{"delta": {"reasoning_content": "Plan"}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call-stream", "function": {"name": "generate_image", "arguments": '{"model":"image",'}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '"prompt":"cat"}'}}]}, "finish_reason": "tool_calls"}]},
        ]
        wire = "".join(f"data: {json.dumps(event)}\n\n" for event in events) + "data: [DONE]\n\n"
        for byte in wire.encode():
            assert comfy.images.generate.await_count == 0
            yield bytes([byte])
    proxy.forward.side_effect = [StreamingResponse(chunks(), media_type="text/event-stream"), reply()]
    await service.start("chat-one", request())
    result = await finish(service)
    assert result["turns"][0]["reasoning"] == "Plan"
    assert result["turns"][0]["state"] == "complete"
    comfy.images.generate.assert_awaited_once()


async def test_incomplete_stream_does_not_execute_even_valid_partial_tool_json(harness):
    service, proxy, comfy, _ = harness
    async def chunks():
        event = {"choices": [{"delta": {"tool_calls": [{"index": 0, **tool()}]}}]}
        yield f"data: {json.dumps(event)}\n\n".encode()
    proxy.forward.return_value = StreamingResponse(chunks(), media_type="text/event-stream")
    await service.start("chat-one", request())
    result = await finish(service)
    assert result["turns"][0]["state"] == "error"
    assert "прервался" in result["turns"][0]["error"]
    comfy.images.generate.assert_not_awaited()


async def test_video_resumes_after_service_restart_without_repeating_post(harness):
    service, proxy, comfy, store = harness
    polling = asyncio.Event()
    async def wait(_):
        polling.set()
        await asyncio.Event().wait()
    comfy.video_status.side_effect = wait
    proxy.forward.return_value = reply("", [tool("generate_video", {
        "model": "video", "prompt": "city", "seconds": 15, "size": "1344x768",
    }, "call-video")])
    await service.start("chat-one", request())
    await asyncio.wait_for(polling.wait(), 2)
    await service.close()
    assert (await store.get("chat-one"))["pending"]["calls"][0]["video_id"] == "video_existing"
    resumed = ChatService(proxy, comfy, store)
    try:
        comfy.video_status.side_effect = None
        proxy.forward.return_value = reply("Видео готово")
        await resumed.get("chat-one")
        result = await finish(resumed)
        assert result["turns"][0]["state"] == "complete"
        assert result["turns"][0]["results"][0]["url"] == "/api/proxy/videos/video_existing/content"
        comfy.create_video.assert_awaited_once()
        with pytest.raises(AppError):
            await resumed.get("chat-other")
    finally:
        await resumed.close()


async def test_ambiguous_image_submission_is_not_replayed_after_restart(harness):
    service, proxy, comfy, store = harness
    started = asyncio.Event()
    async def generating(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()
    comfy.images.generate.side_effect = generating
    proxy.forward.return_value = reply("", [tool()])
    await service.start("chat-one", request())
    await asyncio.wait_for(started.wait(), 2)
    await service.close()
    resumed = ChatService(proxy, comfy, store)
    try:
        await resumed.get("chat-one")
        result = await finish(resumed)
        assert result["turns"][0]["state"] == "error"
        assert "автоматически" in result["turns"][0]["error"]
        comfy.images.generate.assert_awaited_once()
    finally:
        await resumed.close()


async def test_stop_preserves_valid_tool_history_without_interrupting_comfy_queue(harness):
    service, proxy, comfy, store = harness
    started = asyncio.Event()
    async def waiting(_):
        started.set()
        await asyncio.Event().wait()
    comfy.video_status.side_effect = waiting
    proxy.forward.return_value = reply("", [tool("generate_video", {
        "model": "video", "prompt": "city",
    }, "call-video")])
    await service.start("chat-one", request())
    await asyncio.wait_for(started.wait(), 2)
    stopped = await service.stop("chat-one")
    assert not stopped["busy"]
    assert stopped["turns"][0]["state"] == "cancelled"
    history = (await store.get("chat-one"))["messages"]
    assert history[-1]["role"] == "tool"
    assert history[-1]["tool_call_id"] == "call-video"


async def test_plain_text_and_reasoning_settings_use_existing_proxy(harness):
    service, proxy, _, _ = harness
    await service.start("chat-one", request(reasoning={"reasoning_effort": "high", "model": "evil"}))
    result = await finish(service)
    payload = proxy.forward.call_args.kwargs["payload"]
    assert payload["model"] == "llm"
    assert payload["reasoning_effort"] == "high"
    assert result["turns"][0]["usage"]["total_tokens"] == 25
    assert result["turns"][0]["answer"] == "Готово"


async def test_get_image_supplies_real_pixels_after_all_tool_replies_and_keeps_history(harness):
    service, proxy, comfy, store = harness
    image_id = await store.attach("chat-one", "generated.png", "image/png", provider_id="existing")
    proxy.forward.side_effect = [reply("", [
        tool("get_image", {"image_id": image_id}, "view-one"),
        tool("get_image", {"image_id": image_id}, "view-two"),
    ]), reply("На картинке кот")]
    await service.start("chat-one", request())
    result = await finish(service)
    messages = proxy.forward.call_args.kwargs["payload"]["messages"]
    assert [message["role"] for message in messages[-4:]] == ["assistant", "tool", "tool", "user"]
    parts = messages[-1]["content"]
    image_parts = [part for part in parts if part["type"] == "image_url"]
    assert image_parts == [{"type": "image_url", "image_url": {"url": "data:image/png;base64,aW1hZ2U="}}]
    assert result["turns"][0]["state"] == "complete"
    assert result["turns"][0]["results"] == []
    comfy.images.generate.assert_not_awaited()
    comfy.create_video.assert_not_awaited()
    saved = await store.get("chat-one")
    assert saved["messages"][-2]["content"][-1]["image_url"]["url"] == f"asset:{image_id}"
    assert "data:image/" not in json.dumps(saved)
    proxy.forward.side_effect = None
    proxy.forward.return_value = reply("Ответ про ту же картинку")
    await service.start("chat-one", request("followup"))
    await finish(service)
    assert any(isinstance(message.get("content"), list) and any(
        part.get("type") == "image_url" for part in message["content"]
    ) for message in proxy.forward.call_args.kwargs["payload"]["messages"])


async def test_get_image_cannot_read_another_conversations_image(harness):
    service, proxy, comfy, store = harness
    image_id = await store.attach("private-chat", "secret.png", "image/png", content=b"private")
    proxy.forward.side_effect = [reply("", [tool("get_image", {"image_id": image_id})]), reply("Изображение недоступно")]
    await service.start("chat-one", request())
    await finish(service)
    messages = proxy.forward.call_args.kwargs["payload"]["messages"]
    assert messages[-1]["role"] == "tool"
    assert "error" in json.loads(messages[-1]["content"])
    assert not any(isinstance(message.get("content"), list) for message in messages)
    comfy.asset.assert_not_awaited()


async def test_get_image_is_available_without_any_generators_and_rejects_urls(harness):
    service, proxy, comfy, _ = harness
    comfy.get_settings = lambda: {"models": {}}
    assert [item["function"]["name"] for item in service.tools.definitions()] == ["get_image"]
    proxy.forward.side_effect = [reply("", [tool("get_image", {"image_id": "http://external/image.png"})]), reply("Нужен ID")]
    await service.start("chat-one", request())
    result = await finish(service)
    assert result["turns"][0]["state"] == "complete"
    assert "error" in json.loads(proxy.forward.call_args.kwargs["payload"]["messages"][-1]["content"])
    comfy.asset.assert_not_awaited()
