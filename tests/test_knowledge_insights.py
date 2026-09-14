from datetime import datetime
from zoneinfo import ZoneInfo
from types import SimpleNamespace

import pytest

from app.knowledge import insights, jobs
from app.knowledge.db import connect, transaction
from app.knowledge.ingest import import_snapshot
from app.knowledge.worker import process_one
from test_knowledge_foundation import foundation, message, snapshot


@pytest.fixture
def periods(foundation):
    path,output=foundation
    insights.install_schema(path)
    return path,output


def week(output,rows=None,previous=False,complete=True):
    start='2026-08-24' if previous else '2026-08-31'
    end='2026-08-30' if previous else '2026-09-06'
    return snapshot(output,rows or [],name=f'群/{end}',complete=complete,
        period_start=f'{start}T00:00:00',period_end=f'{end}T23:59:59')


def test_full_members_cross_day_distinct_and_comparison(periods):
    path,output=periods
    rows=[message(f'a{i}',sender_id='a',sender_name='A',timestamp=f'2026-09-0{i+1}T12:00:00') for i in range(6)]
    rows += [message('b',sender_id='b',sender_name='B')]
    import_snapshot(path,week(output,rows))
    import_snapshot(path,week(output,[message('old',sender_id='b',sender_name='B',timestamp='2026-08-25T12:00:00')],previous=True))
    first=insights.build(path,1,'weekly','2026-09-03')
    report=insights.detail(path,first['id'])
    assert report['coverage']['current']['complete']
    metrics=report['metrics']
    assert metrics['message_count']==7
    assert metrics['speaker_count']==2
    assert metrics['comparison']['message_count']['percent']==600
    assert metrics['members'][0]['name']=='A'
    assert metrics['members'][0]['active_days']==6
    assert metrics['members'][0]['new_to_top']
    assert metrics['members'][1]['rank_change']==-1
    assert insights.build(path,1,'weekly','2026-09-03')['reused']


def test_late_messages_create_new_revision_without_mutating_report(periods):
    path,output=periods
    import_snapshot(path,week(output,[message()]))
    one=insights.build(path,1,'weekly','2026-09-03')
    frozen=insights.detail(path,one['id'])['metrics']
    import_snapshot(path,snapshot(output,[message('late')],name='群/late'))
    two=insights.build(path,1,'weekly','2026-09-03')
    assert two['revision']==2
    assert insights.detail(path,one['id'])['metrics']==frozen
    assert len(insights.list_reports(path)['items'])==1
    assert len(insights.list_reports(path,include_history=True)['items'])==2


def test_missing_and_zero_are_distinct(periods):
    path,output=periods
    import_snapshot(path,week(output,[message()],complete=False))
    report=insights.detail(path,insights.build(path,1,'weekly','2026-09-03')['id'])
    assert report['metrics']['comparison']['message_count']['state']=='not_comparable'
    assert report['metrics']['daily_counts'][0]['count'] is None
    assert report['metrics']['champion'] is None
    import_snapshot(path,week(output,previous=True))
    import_snapshot(path,week(output,[message()],complete=True))
    metrics=insights.detail(path,insights.build(path,1,'weekly','2026-09-03')['id'])['metrics']
    assert metrics['comparison']['message_count']['state']=='zero_baseline'
    assert metrics['comparison']['message_count']['percent'] is None
    assert metrics['daily_counts'][0]['count']==0


def test_name_only_identity_suppresses_personal_comparisons(periods):
    path,output=periods
    import_snapshot(path,week(output,[message(sender_id='')]))
    import_snapshot(path,week(output,previous=True))
    metrics=insights.detail(path,insights.build(path,1,'weekly','2026-09-03')['id'])['metrics']
    assert metrics['comparison']['speaker_count']['state']=='not_comparable'
    assert metrics['champion'] is None


def test_system_messages_do_not_count(periods):
    path,output=periods
    import_snapshot(path,week(output,[message('system',message_type='system'),message('user')]))
    metrics=insights.detail(path,insights.build(path,1,'weekly','2026-09-03')['id'])['metrics']
    assert metrics['message_count']==1


@pytest.mark.parametrize('kind,day,start,end,previous',[
    ('weekly','2026-01-01','2025-12-28T16','2026-01-04T16','2025-12-21T16'),
    ('monthly','2024-02-14','2024-01-31T16','2024-02-29T16','2023-12-31T16'),
])
def test_period_boundaries(kind,day,start,end,previous):
    values=insights.period_bounds(kind,day)
    assert all(actual.startswith(expected) for actual,expected in zip(values,[start,end,previous]))


def test_independent_insight_job(periods):
    path,output=periods
    import_snapshot(path,week(output,[message()]))
    jobs.enqueue(path,'insight',{'group_id':1,'kind':'weekly','day':'2026-09-03'})
    assert process_one(SimpleNamespace(db_path=path,output_dir=output,app_timezone='Asia/Shanghai',knowledge_min_free_bytes=0))
    assert insights.list_reports(path)['items'][0]['kind']=='weekly'


def test_capture_disabled_schedule_only_enqueues_reports(periods):
    from app.knowledge.capture import schedule_knowledge
    path,output=periods
    with connect(path,write=True) as con,transaction(con):
        con.execute('ALTER TABLE groups ADD COLUMN enabled INTEGER DEFAULT 1')
    settings=SimpleNamespace(db_path=path,output_dir=output,knowledge_group_ids='1',knowledge_capture_enabled=False,knowledge_source_scope='',app_timezone='Asia/Shanghai')
    schedule_knowledge(settings,datetime(2026,9,7,10,tzinfo=ZoneInfo('Asia/Shanghai')))
    with connect(path) as con:
        assert [r[0] for r in con.execute('SELECT job_kind FROM knowledge_jobs')]==['insight']


@pytest.mark.parametrize('payload,complete',[
    ({'messages':[],'hasMore':False},True),
    ({'messages':[]},False),
    ({'messages':[],'hasMore':'false'},False),
    ({'hasMore':False},False),
    ({'messages':[{'createTime':'bad'}],'hasMore':False},False),
])
def test_strict_capture_pagination_evidence(monkeypatch,payload,complete):
    from app.knowledge.capture import GuardedProvider
    from app.providers.history.wechat_data_analysis import WeChatDataAnalysisProvider, _MCP_RANGE_TOOL
    monkeypatch.setattr('app.knowledge.capture.require_primary_idle',lambda s:None)
    monkeypatch.setattr(WeChatDataAnalysisProvider,'_mcp_call',lambda *a,**kw:payload)
    p=object.__new__(GuardedProvider)
    p.sidecar_settings=None;p.raw_items=[];p.last_page_explicit=False;p.rows_valid=True;p.seen_facts={};p.deferred=False
    p._mcp_call(_MCP_RANGE_TOOL,{}, {})
    assert (p.last_page_explicit and p.rows_valid)==complete


def test_primary_send_claim_prevents_capture(periods):
    from app.knowledge.capture import PrimaryBusy, require_primary_idle
    path,output=periods
    snapshot(output,send_claim_id='in-flight-send')
    with pytest.raises(PrimaryBusy):
        require_primary_idle(SimpleNamespace(output_dir=output,app_timezone='Asia/Shanghai'))


def test_report_evidence_frozen_after_late_message(periods):
    path,output=periods
    import_snapshot(path,week(output,[message()]))
    first=insights.build(path,1,'weekly','2026-09-03')
    import_snapshot(path,snapshot(output,[message('late')],name='群/late'))
    assert len(insights.report_messages(path,first['id'])['items'])==1


def test_period_api_queue_and_detail(periods):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.knowledge import router
    from app.config.settings import get_settings
    path,output=periods
    import_snapshot(path,week(output,[message()]))
    api=FastAPI();api.include_router(router)
    settings=SimpleNamespace(db_path=path,output_dir=output,knowledge_enabled=True,app_timezone='Asia/Shanghai',knowledge_min_free_bytes=0)
    api.dependency_overrides[get_settings]=lambda:settings
    with TestClient(api) as client:
        assert client.post('/api/v2/insights/build',json={'group_id':1,'day':'bad'}).status_code==422
        response=client.post('/api/v2/insights/build',json={'group_id':1,'day':'2026-09-03'})
        assert response.status_code==202
        process_one(settings)
        report_id=client.get('/api/v2/insights').json()['items'][0]['id']
        assert client.get(f'/api/v2/insights/{report_id}').json()['metrics']['message_count']==1
        assert len(client.get(f'/api/v2/insights/{report_id}/messages').json()['items'])==1


def test_capture_resume_uses_committed_local_result(periods, monkeypatch):
    from app.knowledge.capture import capture_day
    from app.data_sources.base import FetchResult,V2Message
    path,output=periods
    with connect(path,write=True) as con,transaction(con):
        con.execute('ALTER TABLE groups ADD COLUMN enabled INTEGER DEFAULT 1')
    scope={'group_id':1,'upstream_group_id':'room1','day':'2026-09-01','source_scope':'test-account'}
    jobs.enqueue(path,'capture',scope)
    job=jobs.claim(path,'one')
    settings=SimpleNamespace(db_path=path,output_dir=output,app_timezone='Asia/Shanghai',knowledge_capture_enabled=True,knowledge_source_scope='test-account')
    calls=[]
    class Source:
        def fetch_messages(self,*args):
            calls.append(args)
            return FetchResult([V2Message('upstream','room1','', 'user','甲',datetime(2026,9,1,12),content='消息',raw={'source_message_id':'upstream'})])
    capture_day(settings,job,Source())
    with connect(path,write=True) as con,transaction(con):
        con.execute("UPDATE knowledge_jobs SET lease_until='2000-01-01'")
    resumed=jobs.claim(path,'two')
    capture_day(settings,resumed,Source())
    assert len(calls)==1
    with connect(path) as con:
        assert con.execute('SELECT count(*) FROM messages').fetchone()[0]==1
    settings.knowledge_capture_enabled=False
    with pytest.raises(InterruptedError):
        capture_day(settings,resumed,Source())


def test_report_evidence_cannot_cross_groups(periods):
    import sqlite3
    path,output=periods
    import_snapshot(path,week(output,[message()]))
    report=insights.build(path,1,'weekly','2026-09-03')
    import_snapshot(path,snapshot(output,[message(group_id='room2')],name='二群/2026-09-02',group_id=2,wechat_group_id='room2'))
    with connect(path,write=True) as con,transaction(con):
        other=con.execute('SELECT id FROM messages WHERE group_id=2').fetchone()[0]
        with pytest.raises(sqlite3.IntegrityError):
            con.execute('INSERT INTO report_evidence VALUES(?,?,?,?,?)',(report['id'],1,'section','claim',other))
