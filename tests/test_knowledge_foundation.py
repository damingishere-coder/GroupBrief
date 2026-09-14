import json
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.ai.speaker_attribution import build_attribution_contract
from app.knowledge.db import Conflict, KnowledgeUnavailable, connect, install_schema, transaction
from app.knowledge.ingest import import_snapshot, load_snapshot
from app.knowledge import jobs, service
from app.knowledge.worker import process_one
from scripts.migrate_knowledge import migrate


@pytest.fixture
def foundation(tmp_path):
    path = tmp_path / 'core.db'
    with sqlite3.connect(path) as con:
        con.executescript('''PRAGMA user_version=1;
          CREATE TABLE groups(id INTEGER PRIMARY KEY,wechat_group_id TEXT,deleted_at TEXT);
          INSERT INTO groups VALUES(1,'room1',NULL),(2,'room2',NULL),(3,'deleted','2026-01-01');
          CREATE TABLE schema_migrations(migration_id TEXT PRIMARY KEY,applied_at TEXT,checksum TEXT);
          CREATE TABLE runs(id INTEGER PRIMARY KEY,status TEXT); INSERT INTO runs VALUES(1,'SENT');
          CREATE TABLE group_runs(id INTEGER PRIMARY KEY); CREATE TABLE reports(id INTEGER PRIMARY KEY);
          CREATE TABLE settings(id INTEGER PRIMARY KEY);''')
    assert install_schema(path)
    output = tmp_path / 'output'
    output.mkdir()
    return path, output


def message(mid='1', **extra):
    return {'message_id': mid, 'group_id': 'room1', 'sender_id': 'u1', 'sender_name': '甲',
            'timestamp': '2026-09-01T12:00:00', 'message_type': 'text', 'content': '推荐 Claude Code', **extra}


def snapshot(output, rows=None, name='群/2026-09-02', complete=False, **meta):
    rows = [message()] if rows is None else rows
    folder = output / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'messages.json').write_text(json.dumps(rows, ensure_ascii=False), encoding='utf-8')
    run = {'group_id': 1, 'wechat_group_id': 'room1', 'period_start': '2026-09-01T00:00:00',
           'period_end': '2026-09-01T23:59:59', 'fetch_metrics': {'coverage_complete': complete},
           'message_snapshot_sha256': build_attribution_contract(rows).message_snapshot_sha256, **meta}
    (folder / 'run.json').write_text(json.dumps(run), encoding='utf-8')
    return load_snapshot(output, f'{name}/messages.json')


def test_migration_explicit_idempotent_and_preserves_core(foundation, tmp_path):
    path, _ = foundation
    assert not install_schema(path)
    before = path.read_bytes()
    result = migrate(path, tmp_path / 'copy.db')
    assert result['integrity'] == 'ok'
    assert path.read_bytes() == before
    with connect(tmp_path / 'copy.db') as con:
        assert con.execute('PRAGMA user_version').fetchone()[0] == 1
        assert con.execute('SELECT status FROM runs').fetchone()[0] == 'SENT'
    with pytest.raises(ValueError):
        migrate(path, path)


def test_wal_migration_manifest_matches_closed_output(foundation,tmp_path):
    from app.knowledge.db import digest
    path,_=foundation
    with sqlite3.connect(path) as con:
        con.execute('PRAGMA journal_mode=WAL')
    output=tmp_path/'wal-copy.db'
    result=migrate(path,output)
    assert result['output_sha256']==digest(output.read_bytes())


def test_missing_schema_does_not_create_database(tmp_path):
    path = tmp_path / 'missing.db'
    with pytest.raises(KnowledgeUnavailable), connect(path):
        pass
    assert not path.exists()


def test_real_core_initializer_accepts_extension_without_auto_creating_it(tmp_path, monkeypatch):
    from app.config.settings import Settings
    from app.db import repository
    from sqlmodel import SQLModel
    path = tmp_path / 'actual-core.db'
    monkeypatch.setattr(repository, 'engine', repository.engine)
    settings = Settings(_env_file=None, database_url=f'sqlite:///{path.as_posix()}')
    engine = repository.init_db(settings)
    try:
        with connect(path, require_schema=False) as con:
            assert not con.execute("SELECT 1 FROM sqlite_master WHERE name='messages'").fetchone()
        assert 'messages' not in SQLModel.metadata.tables
        install_schema(path)
        repository._ensure_relationship_schema_current()
    finally:
        engine.dispose()


def test_worker_disabled_never_spawns_child(foundation, monkeypatch):
    from app.knowledge.runtime import start_worker
    import subprocess
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **kw: pytest.fail('不应创建进程'))
    assert start_worker(SimpleNamespace(knowledge_enabled=False)) is None


def test_overlap_deduplicates_and_keeps_every_original(foundation):
    path, output = foundation
    first = snapshot(output, [message('1'), message('2')], complete=True)
    second = snapshot(output, [message('1')], name='群/2026-09-03')
    import_snapshot(path, first)
    assert import_snapshot(path, first)['status'] == 'reused'
    import_snapshot(path, second)
    rows = service.list_messages(path)['items']
    assert len(rows) == 2
    sources = service.message_sources(path, rows[0]['id'])['items']
    assert len(sources) == 2
    assert sources[0]['raw_record']['content'] == '推荐 Claude Code'
    assert rows[0]['sent_at'] == '2026-09-01T04:00:00.000000+00:00'


def test_fallback_preserves_same_second_multiplicity(foundation):
    path, output = foundation
    import_snapshot(path, snapshot(output, [message(''), message('')], complete=True))
    assert len(service.list_messages(path)['items']) == 2
    assert service.status(path)['coverage'] == {'unverified': 1}


def test_conflict_never_overwrites_original_or_restores_coverage(foundation):
    path, output = foundation
    import_snapshot(path, snapshot(output, complete=True))
    import_snapshot(path, snapshot(output, [message(content='改变的正文'), message('')], name='群/2026-09-03', complete=True))
    import_snapshot(path, snapshot(output, name='群/2026-09-04', complete=True))
    row = service.list_messages(path)['items'][0]
    assert row['content'] == '推荐 Claude Code'
    assert row['validation_state'] == 'conflict'
    assert service.status(path)['coverage'] == {'conflict': 3}
    assert any(s['raw_record']['content'] == '改变的正文' for s in service.message_sources(path, row['id'])['items'])


def test_orphan_does_not_relink_missing_local_id(foundation):
    path, output = foundation
    import_snapshot(path, snapshot(output, group_id=999))
    assert service.list_messages(path)['items'] == []
    assert service.list_messages(path, include_orphans=True)['items'][0]['group_id'] is None


def test_cross_group_identity_and_context(foundation):
    path, output = foundation
    import_snapshot(path, snapshot(output))
    import_snapshot(path, snapshot(output, [message(group_id='room2')], name='二群/2026-09-02', group_id=2, wechat_group_id='room2'))
    rows = service.list_messages(path)['items']
    assert len(rows) == 2
    assert service.message_context(path, rows[0]['id'])['after'] == []


@pytest.mark.parametrize('complete,rows,expected', [(True, [], 'complete_empty'), (False, [], 'unverified'), (True, [message()], 'complete')])
def test_coverage_not_inferred_from_message_dates(foundation, complete, rows, expected):
    path, output = foundation
    import_snapshot(path, snapshot(output, rows, complete=complete))
    assert service.status(path)['coverage'] == {expected: 1}


def test_duplicate_id_and_path_escape_rejected(foundation):
    _, output = foundation
    with pytest.raises(ValueError, match='重复消息'):
        snapshot(output, [message(), message()])
    with pytest.raises(ValueError, match='越界'):
        load_snapshot(output, '../messages.json')


def test_interrupted_batch_hidden_and_resume_idempotent(foundation):
    path, output = foundation
    data = snapshot(output, [message(str(i)) for i in range(300)])
    def fail(offset):
        if offset == 250:
            raise InterruptedError()
    with pytest.raises(InterruptedError):
        import_snapshot(path, data, checkpoint=fail)
    assert service.list_messages(path)['items'] == []
    import_snapshot(path, data)
    assert service.status(path)['message_count'] == 300


def test_preview_stale_and_cursor_changes_rejected(foundation):
    path, output = foundation
    import_snapshot(path, snapshot(output, [message('1'), message('2')]))
    preview = service.preview_backfill(path, output)
    page = service.list_messages(path, limit=1)
    import_snapshot(path, snapshot(output, [message('3')], name='群/2026-09-03'))
    with pytest.raises(Conflict):
        service.start_backfill(path, output, preview['version'])
    with pytest.raises(Conflict):
        service.list_messages(path, limit=1, cursor=page['next_cursor'])


def test_fencing_expired_owner_cannot_commit(foundation):
    path, _ = foundation
    jobs.enqueue(path, 'import', {'items': []})
    old = jobs.claim(path, 'old', lease_seconds=-1)
    new = jobs.claim(path, 'new')
    with pytest.raises(jobs.LeaseLost):
        jobs.finish(path, old, 'SUCCEEDED', {})
    jobs.finish(path, new, 'SUCCEEDED', {})
    assert service.list_jobs(path)['items'][0]['status'] == 'SUCCEEDED'


def test_unknown_job_cannot_be_retried(foundation):
    path, _ = foundation
    job = jobs.enqueue(path, 'memory', {})
    with connect(path, write=True) as con, transaction(con):
        con.execute("UPDATE knowledge_jobs SET status='HOLD_UNKNOWN'")
    with pytest.raises(Conflict):
        jobs.control(path, job['id'], 'retry')
    assert jobs.claim(path, 'new') is None


def test_worker_stale_input_finishes_partial_without_retry_loop(foundation):
    path, output = foundation
    snapshot(output)
    preview = service.preview_backfill(path, output)
    service.start_backfill(path, output, preview['version'])
    snapshot(output, [message(content='changed')])
    settings = SimpleNamespace(db_path=path, output_dir=output, app_timezone='Asia/Shanghai', knowledge_min_free_bytes=0)
    assert process_one(settings)
    job = service.list_jobs(path)['items'][0]
    assert job['status'] == 'PARTIAL'
    assert service.status(path)['message_count'] == 0


def test_api_disabled_and_evidence(foundation):
    from app.api.knowledge import router
    from app.config.settings import get_settings
    path, output = foundation
    import_snapshot(path, snapshot(output))
    api = FastAPI()
    api.include_router(router)
    api.dependency_overrides[get_settings] = lambda: SimpleNamespace(db_path=path, output_dir=output, knowledge_enabled=False)
    with TestClient(api) as client:
        assert client.get('/api/v2/knowledge/status').json()['worker_enabled'] is False
        assert client.get('/api/v2/messages?limit=101').status_code == 422
        mid = client.get('/api/v2/messages').json()['items'][0]['id']
        assert client.get(f'/api/v2/messages/{mid}/sources').json()['items'][0]['raw_record']['message_id'] == '1'
        assert client.get('/api/v2/messages/9999').status_code == 404
        assert client.post('/api/v2/knowledge/backfill', json={'expected_version': '0'*64}).status_code == 409
