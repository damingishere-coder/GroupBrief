from datetime import date,timedelta
import json

import pytest

from app.knowledge import insights,memory,storylines,jobs
from app.knowledge.db import connect,transaction
from app.knowledge.ingest import import_snapshot
from test_knowledge_foundation import foundation,message,snapshot


@pytest.fixture
def month_data(foundation):
    path,output=foundation;insights.install_schema(path)
    import_snapshot(path,snapshot(output,[],name='群/2024-02-01',complete=True,period_start='2024-01-01T00:00:00',period_end='2024-01-31T23:59:59'))
    rows=[message('first',timestamp='2024-02-01T00:00:00',content='推荐显示器'),message('last',timestamp='2024-02-29T23:59:59',sender_id='u2',sender_name='乙',content='显示器推荐')]
    import_snapshot(path,snapshot(output,rows,name='群/2024-03-01',complete=True,period_start='2024-02-01T00:00:00',period_end='2024-02-29T23:59:59'))
    import_snapshot(path,snapshot(output,[message('march',timestamp='2024-03-01T00:00:00')],name='群/2024-03-02',complete=True,period_start='2024-03-01T00:00:00',period_end='2024-03-01T23:59:59'))
    return path,output


def test_monthly_leap_year_week_intersections_and_zero_baseline(month_data):
    path,_=month_data
    r=insights.detail(path,insights.build(path,1,'monthly','2024-02-17')['id'])
    m=r['metrics']
    assert m['month_days']==29 and len(m['daily_counts'])==29
    assert m['message_count']==2 and m['speaker_count']==2
    assert [w['days'] for w in m['weekly_trends']]==[4,7,7,7,4]
    assert sum(w['known_messages'] for w in m['weekly_trends'])==2
    assert all(w['complete'] for w in m['weekly_trends'])
    assert m['known_daily_average']==0.07
    assert m['comparison']['message_count']['state']=='zero_baseline'
    assert {'word':'显示','message_count':2} in m['keywords']


@pytest.mark.parametrize('month,days',[('2023-02-01',28),('2024-02-01',29),('2024-04-30',30),('2024-12-31',31)])
def test_month_lengths_no_weekly_dependency(foundation,month,days):
    path,_=foundation;insights.install_schema(path)
    r=insights.detail(path,insights.build(path,1,'monthly',month)['id'])
    assert r['metrics']['month_days']==days
    assert r['metrics']['comparison']['message_count']['state']=='not_comparable'
    assert all(d['count'] is None for d in r['metrics']['daily_counts'])


def test_monthly_replay_and_late_data_revision(month_data):
    path,output=month_data
    first=insights.build(path,1,'monthly','2024-02-01')
    assert insights.build(path,1,'monthly','2024-02-20')['reused']
    import_snapshot(path,snapshot(output,[message('late',timestamp='2024-02-12T12:00:00')],name='群/补录',period_start='2024-02-12T00:00:00',period_end='2024-02-12T23:59:59'))
    second=insights.build(path,1,'monthly','2024-02-01')
    assert second['revision']==2
    assert insights.detail(path,first['id'])['metrics']['message_count']==2
    assert insights.detail(path,second['id'])['metrics']['message_count']==3


def test_no_disappearance_claim_without_analysis_coverage(month_data):
    path,_=month_data;memory.install_schema(path);storylines.install_schema(path)
    r=insights.detail(path,insights.build(path,1,'monthly','2024-02-01')['id'])
    lifecycle=next(s for s in r['sections'] if s['kind']=='lifecycle')
    assert not lifecycle['items']
    assert '分析覆盖不足' in lifecycle['note']


def test_monthly_api_queues_without_ai_or_send(month_data):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.knowledge import router
    from app.config.settings import get_settings
    from types import SimpleNamespace
    path,output=month_data
    app=FastAPI();app.include_router(router);app.dependency_overrides[get_settings]=lambda:SimpleNamespace(db_path=path,output_dir=output,app_timezone='Asia/Shanghai',knowledge_enabled=True)
    client=TestClient(app)
    response=client.post('/api/v2/insights/build',json={'group_id':1,'kind':'monthly','day':'2024-02-01'})
    assert response.status_code==202
    with connect(path) as con:
        job=con.execute('SELECT * FROM knowledge_jobs WHERE id=?',(response.json()['job_id'],)).fetchone()
        assert job['job_kind']=='insight' and json.loads(job['scope_json'])['kind']=='monthly'
        assert con.execute('SELECT status FROM runs WHERE id=1').fetchone()[0]=='SENT'


def test_monthly_automation_can_pause_independently(month_data):
    path,_=month_data
    jobs.enqueue(path,'insight',{'kind':'monthly','automatic':True},group_id=1,priority=1)
    jobs.enqueue(path,'index',{},priority=5)
    assert jobs.claim(path,'worker',allow_monthly=False)['job_kind']=='index'
