"""Real Poppler rendering and PDF transport through both chat paths."""
import asyncio
import base64
import shutil

import httpx
import pytest
from sqlalchemy import text

from app.documents import PdfRenderer, document_capabilities, runtime_document_metadata
from app.errors import AppError
from tests.test_chat_harness import harness, finish, reply, request  # noqa: F401


def sample_pdf():
    objects = [
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 400] /Resources << /Font << /F1 5 0 R >> >> /Contents 6 0 R >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Resources << /Font << /F1 5 0 R >> >> /Contents 7 0 R >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
    ]
    for value in [b'BT /F1 24 Tf 30 200 Td (Page ONE) Tj ET', b'BT /F1 24 Tf 30 200 Td (Page TWO) Tj ET']:
        objects.append(b'<< /Length ' + str(len(value)).encode() + b' >>\nstream\n' + value + b'\nendstream')
    output = b'%PDF-1.4\n'
    offsets = [0]
    for index, value in enumerate(objects, 1):
        offsets.append(len(output))
        output += str(index).encode() + b' 0 obj\n' + value + b'\nendobj\n'
    start = len(output)
    output += b'xref\n0 8\n0000000000 65535 f \n'
    output += b''.join(f'{offset:010d} 00000 n \n'.encode() for offset in offsets[1:])
    return output + f'trailer\n<< /Size 8 /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n'.encode()


def content():
    return [{"type": "text", "text": "Прочитай документ"}, {"type": "file", "file": {
        "filename": "report.pdf", "file_data": "data:application/pdf;base64," + base64.b64encode(sample_pdf()).decode(),
    }}]


@pytest.mark.skipif(not shutil.which('pdftoppm'), reason='poppler-utils required')
async def test_render_real_pdf_in_page_order_and_remove_all_temporary_files(tmp_path):
    pages = await PdfRenderer(tmp_path).render(sample_pdf())
    assert len(pages) == 2
    assert all(page.startswith(b'\x89PNG\r\n\x1a\n') for page in pages)
    dimensions = [(int.from_bytes(page[16:20], 'big'), int.from_bytes(page[20:24], 'big')) for page in pages]
    assert dimensions[0][0] < dimensions[0][1]
    assert dimensions[1][0] > dimensions[1][1]
    assert max(*dimensions[0], *dimensions[1]) <= 2560
    assert list(tmp_path.iterdir()) == []
    with pytest.raises(AppError):
        await PdfRenderer(tmp_path).render(b'%PDF-1.4\nbroken')
    assert list(tmp_path.iterdir()) == []


async def test_cancelled_render_removes_temporary_files(tmp_path):
    started = asyncio.Event()
    renderer = PdfRenderer(tmp_path)
    async def wait(*args):
        started.set()
        await asyncio.Event().wait()
    renderer._run = wait
    task = asyncio.create_task(renderer.render(sample_pdf()))
    await started.wait()
    assert list(tmp_path.iterdir())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert list(tmp_path.iterdir()) == []


async def test_pdf_chat_sends_images_and_retains_only_original_in_history(harness, tmp_path):
    service, proxy, _, store = harness
    render_root = tmp_path / "rendered"
    render_root.mkdir()
    service.pdf = PdfRenderer(render_root)
    await service.start('chat-one', request(content=content()))
    result = await finish(service)
    assert result['turns'][0]['state'] == 'complete'
    outgoing = proxy.forward.call_args.kwargs['payload']['messages'][-1]['content']
    images = [part for part in outgoing if part['type'] == 'image_url']
    assert len(images) == 2
    assert all(part['image_url']['url'].startswith('data:image/png;base64,') for part in images)
    assert not any(part['type'] == 'file' for part in outgoing)
    saved = await store.get('chat-one')
    assert 'file_id' in saved['messages'][0]['content'][1]['file']
    assert 'base64' not in str(saved)
    async with store.sessions() as session:
        assert await session.scalar(text('SELECT count(*) FROM chat_attachments')) == 1
    assert list(render_root.iterdir()) == []


async def test_native_ocr_receives_each_page_as_image_and_preserves_order(harness, tmp_path):
    service, proxy, _, _ = harness
    render_root = tmp_path / "rendered"
    render_root.mkdir()
    service.pdf = PdfRenderer(render_root)
    proxy.forward.side_effect = [httpx.Response(200, json={'pages': [{'markdown': value}]}) for value in ['ONE', 'TWO']]
    await service.start('chat-one', request(content=content(), operation='ocr'))
    result = await finish(service)
    turn = result['turns'][0]
    assert turn['state'] == 'complete'
    assert turn['results'] == [{'kind': 'ocr', 'markdown': '## Страница 1\n\nONE\n\n## Страница 2\n\nTWO'}]
    for call in proxy.forward.call_args_list:
        assert call.kwargs['route'] == '/v1/ocr'
        assert call.kwargs['payload']['document']['image_url'].startswith('data:image/png;base64,')
    assert list(render_root.iterdir()) == []


@pytest.mark.parametrize('operation', ['chat', 'ocr'])
async def test_unlimited_ocr_2_uses_documented_sglang_chat_request(harness, tmp_path, operation):
    service, proxy, _, _ = harness
    render_root = tmp_path / "rendered"
    render_root.mkdir()
    service.pdf = PdfRenderer(render_root)
    proxy.models.return_value = {'data': [{'id': 'llm', 'capabilities': {'document_adapter': 'unlimited_ocr_2'}}]}
    proxy.forward.return_value = reply('Распознанный документ')
    await service.start('chat-one', request(content=content(), operation=operation))
    result = await finish(service)
    assert result['turns'][0]['state'] == 'complete'
    payload = proxy.forward.call_args.kwargs['payload']
    assert proxy.forward.call_args.kwargs['route'] == '/v1/chat/completions'
    assert len(payload['messages']) == 1
    assert payload['messages'][0]['content'][-1]['text'] == '<PAGE><image><PAGE><image>Multi page parsing.'
    assert payload['images_config'] == {'crop_mode': False}
    assert payload['temperature'] == 0 and payload['skip_special_tokens'] is False
    assert 'tools' not in payload
    assert list(render_root.iterdir()) == []


def test_only_explicit_document_adapter_is_advertised():
    assert runtime_document_metadata('python -m sglang.launch_server --model-path /models/baidu/Unlimited-OCR-2 --chat-template unlimited-ocr') == {'capabilities': {'document_adapter': 'unlimited_ocr_2'}}
    assert runtime_document_metadata('python -m sglang.launch_server --model-path qwen') == {}
    assert runtime_document_metadata('sglang --model-path baidu/Unlimited-OCR --chat-template unlimited-ocr') == {}
    assert runtime_document_metadata('sglang --model-path=baidu/Unlimited-OCR-2 --chat-template=unlimited-ocr') == {'capabilities': {'document_adapter': 'unlimited_ocr_2'}}
    assert document_capabilities({'id': 'main-ocr-model'}) == {}
    assert document_capabilities({'capabilities': {'document_adapter': 'unknown', 'api_key': 'secret'}}) == {}


async def test_upstream_failure_keeps_pdf_cleanup(harness, tmp_path):
    service, proxy, _, _ = harness
    render_root = tmp_path / "rendered"
    render_root.mkdir()
    service.pdf = PdfRenderer(render_root)
    proxy.forward.return_value = httpx.Response(500, json={"error": {"message": "upstream failed"}})
    await service.start('chat-one', request(content=content()))
    result = await finish(service)
    assert result['turns'][0]['state'] == 'error'
    assert list(render_root.iterdir()) == []


async def test_runtime_document_capability_is_published_for_local_alias(tmp_path):
    from app.proxy.routes import RouteManager
    from app.proxy.service import ProxyService
    from tests.test_proxy_routes import ProcessManagerStub, ProfileRepositoryStub, profile, instance
    proxy = ProxyService(
        ProcessManagerStub([instance('local', 8000)]),
        route_manager=RouteManager(tmp_path, ProfileRepositoryStub([profile('local', 'ocr-alias')])),
        reasoning_metadata=lambda _: runtime_document_metadata('sglang --model-path baidu/Unlimited-OCR-2 --chat-template unlimited-ocr'),
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(
            lambda req: httpx.Response(200, json={'data': [{'id': 'actual-model'}]})
        )),
    )
    try:
        model = next(item for item in (await proxy.models())['data'] if item['id'] == 'ocr-alias')
        assert model['capabilities']['document_adapter'] == 'unlimited_ocr_2'
    finally:
        await proxy.close()


async def test_document_capability_survives_remote_connection_and_alias(tmp_path):
    from tests.test_openai_connections import service
    proxy = service(tmp_path, lambda req: httpx.Response(200, json={'data': [{
        'id': 'actual-model', 'capabilities': {'document_adapter': 'unlimited_ocr_2', 'api_key': 'private'},
    }]}))
    connection = proxy.connections().save(None, 'Remote', 'http://remote.test/v1', '')
    try:
        await proxy.create_alias('ocr-alias', [], {'connection_id': connection['id'], 'model': 'actual-model'})
        catalogue = await proxy.models()
        model = next(item for item in catalogue['data'] if item['id'] == 'ocr-alias')
        assert model['capabilities'] == {'document_adapter': 'unlimited_ocr_2'}
        assert 'private' not in str(catalogue)
    finally:
        await proxy.close()
