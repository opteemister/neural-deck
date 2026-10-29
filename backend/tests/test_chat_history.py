import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

from app.errors import AppError
from tests.test_chat_harness import harness, request, finish  # noqa: F401
from tests.test_mcp_chat import transports


async def test_history_is_sorted_metadata_and_delete_keeps_other_chat(harness):
    service, _, _, store = harness
    for cid, stamp in [('older', 100), ('newer', 200)]:
        await service.start(cid, request(cid))
        await finish(service, cid)
        state = await store.get(cid)
        state['turns'][0]['created_at'] = stamp
        state['turns'][0]['prompt'] = cid + '\n title'
        await store.save(state)
    asset = await store.attach('older', 'private.pdf', 'application/pdf', content=b'private document')
    keep = await store.attach('newer', 'keep.pdf', 'application/pdf', content=b'keep document')
    app, mcp = transports(service)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://deck.test') as client:
        result = (await client.get('/api/v1/chat')).json()['data']
        assert [item['id'] for item in result['items']] == ['newer', 'older']
        assert result['items'][0]['title'] == 'newer title'
        assert 'private document' not in str(result)
        assert 'messages' not in str(result)
        response = await client.delete('/api/v1/chat/older')
        assert response.status_code == 200 and response.json()['data']['deleted']
        assert (await client.get('/api/v1/chat/older')).status_code == 404
        assert (await client.get('/api/v1/chat/newer')).status_code == 200
    with pytest.raises(AppError):
        await store.attachment('older', asset)
    assert (await store.attachment('newer', keep))['content'] == b'keep document'
    assert 'older' not in service._states
    result = await mcp._tool_manager.call_tool('nd_chat_list', {})
    assert [item['id'] for item in result.structuredContent['data']['items']] == ['newer']
    result = await mcp._tool_manager.call_tool('nd_chat_delete', {'conversation_id': 'newer'})
    assert not result.isError
    assert (await service.list_conversations()) == {'items': []}


async def test_delete_waits_for_running_writer_and_cannot_resurrect_chat(harness):
    service, proxy, _, _ = harness
    started = asyncio.Event()
    async def waiting(**kwargs):
        started.set()
        await asyncio.Event().wait()
    proxy.forward = AsyncMock(side_effect=waiting)
    await service.start('running', request())
    await started.wait()
    task = service._tasks['running']
    assert (await service.list_conversations())['items'][0]['busy']
    await service.delete('running')
    assert task.done()
    assert (await service.list_conversations())['items'] == []
    with pytest.raises(AppError) as error:
        await service.get('running')
    assert error.value.code == 'chat_not_found'
