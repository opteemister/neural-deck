"""MCP and HTTP share the real chat service, history and media lifecycle."""
import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from app.api.chat import router
from app.api.dependencies import get_runtime
from app.chat.service import ChatTurnRequest
from app.errors import AppError, app_error_handler
from app.mcp.operations import build_operations
from app.mcp.server import MCPDependencies, create_mcp_server
from tests.test_chat_harness import harness, finish, reply, tool  # noqa: F401
from tests.test_documents import content as pdf_content


async def unused(*args, **kwargs):
    raise AssertionError('MCP must use ChatService')


def transports(service):
    runtime = SimpleNamespace(chat_service=service)
    app = FastAPI()
    app.state.runtime = runtime
    app.include_router(router)
    app.dependency_overrides[get_runtime] = lambda: runtime
    app.add_exception_handler(AppError, app_error_handler)
    @app.middleware('http')
    async def rid(request, call_next):
        request.state.request_id = 'test'
        return await call_next(request)
    mcp = create_mcp_server(MCPDependencies(unused, unused, unused, unused, unused, operations=build_operations(app)))
    return app, mcp


async def call(mcp, **args):
    action = args.pop('action', 'send')
    result = await mcp._tool_manager.call_tool('nd_chat_' + action, args)
    assert not result.isError, result.structuredContent
    return result.structuredContent['data']


@pytest.mark.parametrize('kind', ['llm-tool', 'direct-image', 'direct-video', 'pdf', 'native-ocr'])
async def test_mcp_send_is_the_http_chat_with_idempotency_and_results(harness, tmp_path, kind):
    service, proxy, comfy, _ = harness
    app, mcp = transports(service)
    model = {'direct-image': 'image', 'direct-video': 'video'}.get(kind, 'llm')
    if kind == 'llm-tool':
        proxy.forward.side_effect = [reply('', [tool()]), reply('Вот изображение')]
    if kind == 'native-ocr':
        proxy.models.return_value = {'data': [{'id': 'llm', 'types': ['ocr']}]}
        proxy.forward.side_effect = [httpx.Response(200, json={'pages': [{'markdown': value}]}) for value in ['one', 'two']]
    request = {'id': 'turn-one', 'model': model, 'prompt': 'Создай кота', 'content': pdf_content() if kind in {'pdf', 'native-ocr'} else 'Создай кота',
               'media_defaults': {'image': {'size': '1024x1024', 'background': 'transparent'}, 'video': {'size': '640x640', 'seconds': 5}}}
    initial = await call(mcp, request=request)
    assert initial['id'] == 'turn-one'
    await finish(service, 'turn-one')
    result = await call(mcp, action='get', conversation_id=initial['id'])
    assert not result['busy'] and result['turns'][0]['state'] == 'complete'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://deck.test') as client:
        assert (await client.get('/api/v1/chat/turn-one')).json()['data'] == result
        duplicate = (await client.post('/api/v1/chat/turn-one/turns', json=request)).json()['data']
        assert duplicate == result
    assert await call(mcp, request=request) == result
    if kind in {'llm-tool', 'direct-image'}:
        assert result['turns'][0]['results'][0]['kind'] == 'image'
        comfy.images.generate.assert_awaited_once()
        assert comfy.images.generate.call_args.args[0]['size'] == '1024x1024'
    if kind == 'direct-video':
        comfy.create_video.assert_awaited_once()
        assert comfy.create_video.call_args.args[0]['seconds'] == 5
        assert result['turns'][0]['results'][0]['kind'] == 'video'
    if kind == 'native-ocr':
        assert result['turns'][0]['results'][0]['kind'] == 'ocr'
        assert proxy.forward.await_count == 2
    if kind == 'pdf':
        outgoing = proxy.forward.call_args.kwargs['payload']['messages'][-1]['content']
        assert len([part for part in outgoing if part['type'] == 'image_url']) == 2


async def test_mcp_long_video_returns_immediately_can_be_read_and_stopped(harness):
    service, _, comfy, _ = harness
    _, mcp = transports(service)
    started = asyncio.Event()
    async def waiting(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()
    comfy.video_status.side_effect = waiting
    first = await call(mcp, conversation_id='one', request={'id': 'turn-one', 'model': 'video', 'prompt': 'long video', 'content': 'long video'})
    assert first['busy']
    await started.wait()
    assert (await call(mcp, action='get', conversation_id='one'))['busy']
    assert not (await call(mcp, action='stop', conversation_id='one'))['busy']
    assert (await service.get('one'))['turns'][0]['state'] == 'cancelled'
    comfy.create_video.assert_awaited_once()


async def test_mcp_catalog_and_validation_share_chat_contract(harness):
    service, _, _, _ = harness
    _, mcp = transports(service)
    result = await mcp._tool_manager.call_tool('nd_list_chat_models', {})
    assert {'image', 'video'} <= {item['id'] for item in result.structuredContent['data']['items']}
    names = {item.name for item in mcp._tool_manager.list_tools()}
    assert not {'get_image', 'edit_image', 'generate_video'} & names
    assert {'nd_chat_send', 'nd_chat_get', 'nd_chat_stop'} <= names
    assert 'nd_chat' not in names
    result = await mcp._tool_manager.call_tool('nd_chat_get', {'conversation_id': ''})
    assert result.isError and result.structuredContent['error']['code'] == 'validation_error'
    with pytest.raises(AppError):
        await build_operations(SimpleNamespace(state=SimpleNamespace(runtime=SimpleNamespace(chat_service=service))))['chat']('send', None, None)


@pytest.mark.parametrize('mode', ['edit', 'edit_masked', 'variations'])
async def test_direct_image_modes_use_shared_attachments_and_workflow(harness, mode):
    service, proxy, comfy, _ = harness
    settings = comfy.get_settings()
    settings['models']['image']['workflows']['variations'] = 'variations.json'
    comfy.get_settings = lambda: settings
    _, mcp = transports(service)
    images = [{'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,aW1hZ2U='}}]
    if mode == 'edit_masked':
        images *= 2
    if mode != 'variations':
        images.insert(0, {'type': 'text', 'text': 'change background'})
    await call(mcp, request={'id': 'edit-one', 'model': 'image', 'prompt': 'change background' if mode != 'variations' else '', 'content': images})
    result = await finish(service, 'edit-one')
    assert result['turns'][0]['state'] == 'complete'
    assert comfy.images.generate.call_args.kwargs['operation'] == mode
    proxy.forward.assert_not_awaited()


@pytest.mark.parametrize('mode', ['generations', 'edits'])
async def test_native_image_provider_keeps_standard_api_via_common_chat(harness, mode):
    service, proxy, _, _ = harness
    proxy.models.return_value = {'data': [{'id': 'native-image', 'types': ['image']}]}
    proxy.forward.return_value = httpx.Response(200, json={'data': [{'b64_json': 'aW1hZ2U='}]})
    _, mcp = transports(service)
    content = 'draw a cat' if mode == 'generations' else [
        {'type': 'text', 'text': 'draw a cat'},
        {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,aW1hZ2U='}},
    ]
    await call(mcp, request={'id': 'native', 'model': 'native-image', 'prompt': 'draw a cat', 'content': content})
    result = await finish(service, 'native')
    assert result['turns'][0]['state'] == 'complete'
    assert result['turns'][0]['results'][0]['assets'][0]['id']
    assert proxy.forward.call_args.kwargs['route'] == '/v1/images/' + mode
    if mode == 'edits':
        assert proxy.forward.call_args.kwargs['multipart_files']['image'][1] == b'image'


async def test_real_shared_catalog_keeps_comfy_limits_and_http_parity(tmp_path):
    from app.proxy.comfy_provider import ComfyProviderService
    from app.proxy.media_router import _all_models
    from unittest.mock import AsyncMock
    comfy = ComfyProviderService({"models": {"image": {"type": "image", "source": "local", "workflows": {"generate": "generate.json"}}}})
    comfy.workflows = AsyncMock(return_value={'available': True})
    try:
        catalog = await comfy.extend_models({'data': []})
        assert any('max_megapixels' in item['capabilities'] for item in catalog['data'])
        assert await _all_models(SimpleNamespace(comfy_service=comfy), {'data': []}) == catalog
    finally:
        await comfy.close()
