import pytest

from app.knowledge import memory,storylines,insights
from app.knowledge.db import Conflict,connect
from app.knowledge.ingest import import_snapshot
from test_knowledge_foundation import foundation,message,snapshot
from test_knowledge_memory import memories,candidate


@pytest.fixture
def storyline_data(memories):
    path,output=memories;storylines.install_schema(path)
    texts=['2026-08-01 准备离职','2026-08-10 第一次面试','2026-08-18 收到 Offer','2026-09-01 正式入职']
    import_snapshot(path,snapshot(output,[message('stage'+str(i),sender_id='u1',content=text) for i,text in enumerate(texts)],name='群/2026-09-04'))
    values=[]
    for mid,text in enumerate(texts,5):
        c=candidate(mid,'u1',title='甲的换工作过程',summary=text,event_at=text[:10],event_time_quote=text)
        c['claims'][0]['sources'][0]['quote']=text;values.append(c)
    result=memory.append_candidates(path,1,'legacy:installation',values,list(range(5,9)))
    assert not result['rejected']
    return path,output,result['entries']


def test_timeline_source_dates_idempotent_and_unlink(storyline_data):
    path,_,entries=storyline_data
    args={'group_id':1,'entry_ids':entries,'title':'甲的换工作过程'}
    p=storylines.link_preview(path,**args);first=storylines.link(path,p['version'],**args)
    assert storylines.link(path,p['version'],**args)['id']==first['id']
    detail=storylines.detail(path,first['id'])
    assert len(detail['entries'])==4
    assert [e['event_at'][:10] for e in detail['entries']]==['2026-07-31','2026-08-09','2026-08-17','2026-08-31'] # UTC storage
    assert all(e['sources'] for e in detail['entries'])
    storylines.unlink(path,detail['id'],entries[0],detail['version'])
    assert len(storylines.detail(path,detail['id'])['entries'])==3
    with connect(path) as con:assert con.execute('SELECT count(*) FROM memory_entries').fetchone()[0]==4


def test_storyline_subject_and_group_protection(storyline_data):
    path,_,entries=storyline_data
    other=memory.append_candidates(path,1,'legacy:installation',[candidate(2,'u2')],[2])['entries'][0]
    with pytest.raises(ValueError,match='主体'):storylines.link_preview(path,group_id=1,entry_ids=[entries[0],other],title='错误的经历')
    with pytest.raises(ValueError,match='属于此群'):storylines.link_preview(path,group_id=2,entry_ids=entries,title='错误群')


def test_report_freezes_content_and_claim_evidence(storyline_data):
    path,_,entries=storyline_data
    p=storylines.link_preview(path,group_id=1,entry_ids=entries,title='换工作故事')
    storylines.link(path,p['version'],group_id=1,entry_ids=entries,title='换工作故事')
    first=insights.build(path,1,'weekly','2026-09-01');original=insights.detail(path,first['id'])
    assert any(s['kind']=='storyline' for s in original['sections'])
    assert {e['memory_entry_id'] for e in original['evidence_refs']}==set(entries)
    m=memory.detail(path,1);memory.patch(path,1,m['version'],title='改过的标题')
    second=insights.build(path,1,'weekly','2026-09-01')
    assert second['revision']==2
    assert insights.detail(path,first['id'])['sections']==original['sections']
    assert insights.build(path,1,'weekly','2026-09-01')['reused']


def test_conflicted_source_excluded_from_new_insight(storyline_data):
    from app.knowledge.db import transaction
    path,_,entries=storyline_data
    with connect(path,write=True) as con,transaction(con):con.execute("UPDATE messages SET validation_state='conflict' WHERE id=5")
    with pytest.raises(ValueError,match='冲突'):storylines.link_preview(path,group_id=1,entry_ids=entries,title='换工作')
    report=insights.detail(path,insights.build(path,1,'weekly','2026-09-01')['id'])
    assert 5 not in {e['message_id'] for e in report['evidence_refs']}


def test_storyline_api_preview_link_and_detail(storyline_data):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.knowledge import router
    from app.config.settings import get_settings
    from types import SimpleNamespace
    path,_,entries=storyline_data
    app=FastAPI();app.include_router(router);app.dependency_overrides[get_settings]=lambda:SimpleNamespace(db_path=path)
    client=TestClient(app);body={'group_id':1,'entry_ids':entries,'title':'换工作'}
    p=client.post('/api/v2/storylines/link-preview',json=body)
    assert p.status_code==200
    r=client.post('/api/v2/storylines/link',json={**body,'expected_version':p.json()['version']})
    assert r.status_code==200
    assert len(client.get('/api/v2/storylines/'+str(r.json()['id'])).json()['entries'])==4
