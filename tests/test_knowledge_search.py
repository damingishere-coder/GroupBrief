import pytest

from app.knowledge import search as search_module
from app.knowledge.db import Conflict, KnowledgeUnavailable, connect
from app.knowledge.ingest import import_snapshot
from app.knowledge.search_text import evidence_snippet, query_terms
from test_knowledge_foundation import message,snapshot
from test_knowledge_insights import foundation,periods


@pytest.fixture
def indexed(periods):
    path,output=periods
    search_module.install_schema(path)
    return path,output


@pytest.mark.parametrize('query,content',[
    ('手','苹果手机不错'),('手机','苹果手机不错'),('苹果手机','这款苹果手机不错'),
    ('Claude Code','推荐 CLAUDE Code 开发工具'),('iPhone','iPhone 18'),
    ('"Claude Code"','使用 Claude   Code'),('显示器','🐮🐴 推荐显示器'),
    ('ＡＩ','本群讨论 AI 工具'),('iPhone:','iPhone: 推荐'),
])
def test_search_original_and_safe_highlights(indexed,query,content):
    path,output=indexed
    import_snapshot(path,snapshot(output,[message(content=content)]))
    search_module.build_index(path,output)
    hits=search_module.search(path,output,query)['items']
    assert len(hits)==1
    assert hits[0]['id']>0
    assert ''.join(p['text'] for p in hits[0]['snippet'])==content
    assert any(p['match'] for p in hits[0]['snippet'])


def test_token_overlap_does_not_fake_contiguous_phrase(indexed):
    path,output=indexed
    import_snapshot(path,snapshot(output,[message(content='电脑 脑显 显示 示器')]))
    search_module.build_index(path,output)
    assert search_module.search(path,output,'电脑显示器')['items']==[]


def test_no_ready_index_is_not_empty_success(indexed):
    path,output=indexed
    with pytest.raises(KnowledgeUnavailable,match='尚未就绪'):
        search_module.search(path,output,'手机')


def test_incremental_cursor_and_rebuild(indexed):
    path,output=indexed
    import_snapshot(path,snapshot(output,[message(str(i),content='苹果手机') for i in range(4)]))
    search_module.build_index(path,output)
    first=search_module.search(path,output,'手机',limit=2)
    second=search_module.search(path,output,'手机',limit=2,cursor=first['next_cursor'])
    assert {v['id'] for v in first['items']}.isdisjoint({v['id'] for v in second['items']})
    previous=search_module.search_status(path)['index_version']
    search_module.build_index(path,output,rebuild=True)
    assert search_module.search_status(path)['index_version']>previous
    with pytest.raises(Conflict):
        search_module.search(path,output,'手机',limit=2,cursor=first['next_cursor'])
    assert len(search_module.search(path,output,'手机')['items'])==4


def test_group_sender_date_and_orphan_filters(indexed):
    path,output=indexed
    import_snapshot(path,snapshot(output,[message('1',sender_id='one',content='苹果手机'),message('2',sender_id='two',content='苹果手机')]))
    import_snapshot(path,snapshot(output,[message('3',content='苹果手机')],name='孤儿/2026-09-02',group_id=999))
    search_module.build_index(path,output)
    assert len(search_module.search(path,output,'手机',group_id=1,sender_id='one')['items'])==1
    assert len(search_module.search(path,output,'手机',include_orphans=True)['items'])==3
    assert search_module.search(path,output,'手机',start='2026-09-02')['items']==[]


def test_legacy_report_text_source_and_refresh_guard(indexed):
    path,output=indexed
    snapshot(output)
    file=output/'群/2026-09-02/image_prompt.txt'
    file.write_text('群里讨论 iPhone 和苹果手机',encoding='utf-8')
    search_module.build_index(path,output)
    hit=search_module.search(path,output,'iPhone',object_type='report')['items'][0]
    original=search_module.legacy_report(path,output,hit['ref'],hit['source_hash'])
    assert original['evidence_refs']==[]
    assert original['warnings']
    file.write_text('changed',encoding='utf-8')
    result=search_module.search(path,output,'iPhone',object_type='report')
    assert result['items']==[] and result['warnings']
    with pytest.raises(Conflict):
        search_module.legacy_report(path,output,hit['ref'],hit['source_hash'])


def test_failed_shadow_rebuild_keeps_old_index(indexed):
    path,output=indexed
    import_snapshot(path,snapshot(output,[message(content='手机')]))
    search_module.build_index(path,output)
    initial=search_module.search_status(path)['index_version']
    def interrupted():
        raise InterruptedError()
    with pytest.raises(InterruptedError):
        search_module.build_index(path,output,rebuild=True,checkpoint=interrupted)
    assert search_module.search_status(path)['index_version']==initial
    assert len(search_module.search(path,output,'手机')['items'])==1


@pytest.mark.parametrize('query',['"unclosed','*','NEAR()','""'])
def test_query_syntax_never_executes_fts_operators(query):
    if query=='NEAR()':
        assert query_terms(query)[1]=='"wnear"'
    else:
        with pytest.raises(ValueError):
            query_terms(query)


def test_unicode_highlights_are_original_spans():
    result=evidence_snippet('😀 ＡＩ 与显示器',['ai','显示器'])
    assert [p['text'] for p in result if p['match']]==['ＡＩ','显示器']


def test_equal_rank_pagination_is_stable_across_connections(indexed):
    path,output=indexed
    import_snapshot(path,snapshot(output,[message(str(i),content='同分手机记录') for i in range(321)]))
    search_module.build_index(path,output)
    def all_ids():
        result=[];cursor=None
        while True:
            page=search_module.search(path,output,'手机',limit=20,cursor=cursor)
            result.extend(r['id'] for r in page['items'])
            cursor=page['next_cursor']
            if not cursor:
                return result
    first=all_ids()
    assert len(first)==len(set(first))==321
    assert all_ids()==first


def test_old_database_report_is_searchable_without_message_fabrication(indexed):
    from app.knowledge.db import transaction
    path,output=indexed
    with connect(path,write=True) as con,transaction(con):
        con.execute('ALTER TABLE reports ADD COLUMN group_run_id INTEGER')
        con.execute('ALTER TABLE reports ADD COLUMN ranking_text TEXT')
        con.execute('ALTER TABLE reports ADD COLUMN prompt_text TEXT')
        con.execute('ALTER TABLE group_runs ADD COLUMN group_id INTEGER')
        con.execute('ALTER TABLE group_runs ADD COLUMN run_id INTEGER')
        con.execute('ALTER TABLE runs ADD COLUMN report_date TEXT')
        con.execute("UPDATE runs SET report_date='2026-09-01'")
        con.execute('INSERT INTO group_runs VALUES(1,1,1)')
        con.execute("INSERT INTO reports VALUES(1,1,'历史排行','iPhone 群聊内容')")
    search_module.build_index(path,output)
    hit=search_module.search(path,output,'iPhone',object_type='report')['items'][0]
    assert hit['ref']=='v1:1'
    result=search_module.legacy_report(path,output,hit['ref'])
    assert result['evidence_refs']==[]
    assert 'iPhone' in result['body']


def test_search_api_evidence_and_index_jobs(indexed):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from types import SimpleNamespace
    from app.api.knowledge import router
    from app.config.settings import get_settings
    path,output=indexed
    import_snapshot(path,snapshot(output,[message(content='苹果手机')]))
    search_module.build_index(path,output)
    api=FastAPI();api.include_router(router)
    api.dependency_overrides[get_settings]=lambda:SimpleNamespace(db_path=path,output_dir=output,knowledge_enabled=True)
    with TestClient(api) as client:
        response=client.get('/api/v2/search',params={'q':'手机'})
        assert response.status_code==200 and response.json()['ai_calls']==0
        mid=response.json()['items'][0]['id']
        assert client.get(f'/api/v2/messages/{mid}/sources').json()['items'][0]['raw_record']['content']=='苹果手机'
        assert client.get('/api/v2/search',params={'q':'"'}).status_code==422
        assert client.post('/api/v2/knowledge/index',json={'rebuild':True}).status_code==202
