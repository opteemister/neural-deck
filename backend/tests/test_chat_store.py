"""Disposable chat storage uses native SQLite creation, independently of the main DB."""
from pathlib import Path
import sqlite3

import pytest

from app.chat.store import ChatStore
from app.errors import AppError
from tests.test_chat_harness import harness, request, finish  # noqa: F401


async def test_initialize_creates_separate_chat_file_without_touching_main(tmp_path: Path):
    main = tmp_path / 'neural-deck.sqlite3'
    with sqlite3.connect(main) as connection:
        connection.execute('CREATE TABLE sentinel (value TEXT)')
        connection.execute("INSERT INTO sentinel VALUES ('keep')")
    store = ChatStore(tmp_path / 'chats.sqlite3')
    try:
        await store.initialize()
        await store.save({'id': 'one', 'messages': ['hello']})
        assert store.path.is_file()
        with sqlite3.connect(main) as connection:
            assert connection.execute('SELECT * FROM sentinel').fetchall() == [('keep',)]
            assert not connection.execute("SELECT name FROM sqlite_master WHERE name LIKE 'chat_%'").fetchall()
    finally:
        await store.close()


async def test_deleted_file_is_recreated_on_read_and_next_write(tmp_path: Path):
    store = ChatStore(tmp_path / 'chats.sqlite3')
    try:
        await store.initialize()
        await store.save({'id': 'old'})
        await store.get('old')
        old_asset = await store.attach('old', 'private.pdf', 'application/pdf', content=b'%PDF-private')
        store.path.unlink()
        assert await store.get('old') is None
        assert store.path.is_file()
        with pytest.raises(AppError) as error:
            await store.attachment('old', old_asset)
        assert error.value.status_code == 404
        await store.save({'id': 'new'})
        assert await store.get('new') == {'id': 'new'}
        store.path.unlink()
        asset = await store.attach('new', 'new.png', 'image/png', content=b'new image')
        assert (await store.attachment('new', asset))['content'] == b'new image'
        store.path.unlink()
        await store.save({'id': 'again'})
        assert await store.get('again') == {'id': 'again'}
    finally:
        await store.close()


async def test_cleared_database_does_not_restore_cached_conversation(harness):
    service, _, _, store = harness
    await service.start('chat-one', request('old-turn'))
    await finish(service)
    assert service._states['chat-one']['turns']
    store.path.unlink()
    with pytest.raises(AppError) as error:
        await service.get('chat-one')
    assert error.value.code == 'chat_not_found'
    assert store.path.exists()
    await service.start('chat-one', request('new-turn'))
    result = await finish(service)
    assert [turn['id'] for turn in result['turns']] == ['new-turn']
