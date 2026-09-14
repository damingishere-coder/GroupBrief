import json
from types import SimpleNamespace

import pytest

from app.knowledge import memory,jobs
from app.knowledge.db import Conflict,connect,transaction,digest
from app.knowledge.ingest import import_snapshot
from app.knowledge.insights import install_schema as insights
from test_knowledge_foundation import foundation,message,snapshot


@pytest.fixture
def memories(foundation):
    path,output=foundation
    insights(path);memory.install_schema(path)
    import_snapshot(path,snapshot(output,[message(str(i),sender_id='u'+str(i),content=f'我推荐工具 {i}，计划 2026-09-08 入职') for i in range(1,5)]))
    return path,output


def candidate(mid=1,subject='u1',title='甲的入职进展',**extra):
    return {'type':'event','title':title,'summary':'发言人提到入职计划','keywords':['入职'],
            'subject_sender_ids':[subject] if subject else [],
            'claims':[{'key':'plan','text':'发言人提到入职计划','sources':[{'message_id':mid,'quote':f'我推荐工具 {mid}，计划 2026-09-08 入职','relation':'reports'}]}],**extra}


def test_create_idempotency_claim_sources(memories):
    path,_=memories
    first=memory.append_candidates(path,1,'legacy:installation',[candidate()],[1])
    again=memory.append_candidates(path,1,'legacy:installation',[candidate()],[1])
    assert first['entries']==again['entries'] and again['reused']==1
    m=memory.detail(path,1)
    assert m['entries'][0]['sources'][0]['message_id']==1
    assert m['entity_keys']==['u1']


def test_subjects_different_not_merged_and_ambiguous_review(memories):
    path,_=memories
    memory.append_candidates(path,1,'legacy:installation',[candidate(),candidate(2,'u2')],[1,2])
    items=memory.list_memories(path)['items']
    assert len(items)==2 and any(m['status']=='review' for m in items)


def test_same_subject_same_title_extends(memories):
    path,output=memories
    import_snapshot(path,snapshot(output,[message('later',sender_id='u1',content='已收到 Offer')],name='群/2026-09-03'))
    later=candidate(5);later['claims'][0]['sources'][0]['quote']='已收到 Offer'
    memory.append_candidates(path,1,'legacy:installation',[candidate(),later],[1,5])
    assert len(memory.list_memories(path)['items'])==1
    assert len(memory.detail(path,1)['entries'])==2


@pytest.mark.parametrize('change',[
    {'claims':[{'key':'x','text':'假的','sources':[{'message_id':999,'quote':'不存在'}]}]},
    {'claims':[{'key':'x','text':'假的','sources':[{'message_id':1,'quote':'不存在'}]}]},
    {'subject_sender_ids':['other']},
    {'event_at':'2026-08-01','event_time_quote':'2026-08-01'},
    {'type':'consensus'},
])
def test_invalid_candidate_quarantined_whole(memories,change):
    path,_=memories
    result=memory.append_candidates(path,1,'legacy:installation',[candidate(**change)],[1])
    assert result['rejected'] and not result['entries']
    assert not memory.list_memories(path)['items']


def test_cross_group_and_conflict_rejected(memories):
    path,_=memories
    assert memory.append_candidates(path,2,'legacy:installation',[candidate()],[1])['rejected']
    with connect(path,write=True) as con,transaction(con):
        con.execute("UPDATE messages SET validation_state='conflict' WHERE id=1")
    assert memory.append_candidates(path,1,'legacy:installation',[candidate()],[1])['rejected']


def test_merge_preview_versions_undo_and_cycle(memories):
    path,_=memories
    memory.append_candidates(path,1,'legacy:installation',[candidate(),candidate(2,'u2')],[1,2])
    p=memory.merge_preview(path,1,2)
    with pytest.raises(Conflict): memory.merge(path,1,2,'x'*64)
    op=memory.merge(path,1,2,p['version'])
    assert len(memory.detail(path,2)['entries'])==2
    with pytest.raises(Conflict): memory.merge_preview(path,2,1)
    source=memory.detail(path,1)
    memory.undo_merge(path,1,op['operation_id'],source['version'])
    assert len(memory.detail(path,2)['entries'])==1
    assert memory.detail(path,1)['merged_into_id'] is None
    with pytest.raises(Conflict): memory.undo_merge(path,1,op['operation_id'],source['version'])


def test_empty_extraction_and_date(memories):
    path,_=memories
    assert memory.append_candidates(path,1,'legacy:installation',[],[])['entries']==[]
    result=memory.append_candidates(path,1,'legacy:installation',[candidate(event_at='2026-09-08',event_time_quote='计划 2026-09-08 入职')],[1])
    assert not result['rejected']
    assert memory.detail(path,1)['entries'][0]['event_time_basis']=='explicit_date'


def ai_setup(memories):
    path,output=memories
    from app.config.settings import Settings
    s=Settings(_env_file=None,database_url=f'sqlite:///{path.as_posix()}',output_root_override=str(output),
               knowledge_memory_enabled=True,knowledge_memory_ai_enabled=True,summary_provider_primary='codex')
    jobs.enqueue(path,'memory',{'input':1},group_id=1)
    return s,jobs.claim(path,'test')


def test_ai_response_reuse_no_duplicate_payment(memories,monkeypatch):
    from app.knowledge.ai_operations import call
    monkeypatch.setattr('app.knowledge.capture.require_primary_idle',lambda s:None)
    s,j=ai_setup(memories);calls=[]
    invoke=lambda m:(calls.append(m) or '{"candidates":[]}')
    assert call(s,j,[{'role':'user','content':'test'}],invoke=invoke)=={'candidates':[]}
    assert call(s,j,[{'role':'user','content':'test'}],invoke=invoke)=={'candidates':[]}
    assert len(calls)==1


def test_unknown_never_retries_and_lease_does_not_block_other_jobs(memories,monkeypatch):
    from app.knowledge.ai_operations import call,HoldUnknown
    monkeypatch.setattr('app.knowledge.capture.require_primary_idle',lambda s:None)
    s,j=ai_setup(memories)
    def unknown(_): raise TimeoutError('unknown')
    with pytest.raises(HoldUnknown): call(s,j,[],invoke=unknown)
    with connect(s.db_path,write=True) as con,transaction(con):
        con.execute("UPDATE knowledge_jobs SET lease_until='2000-01-01' WHERE id=?",(j['id'],))
    jobs.enqueue(s.db_path,'index',{'later':True})
    assert jobs.claim(s.db_path,'next')['job_kind']=='index'
    with pytest.raises(Conflict): jobs.control(s.db_path,j['id'],'retry')


def test_invalid_json_local_reparse_only(memories,monkeypatch):
    from app.knowledge.ai_operations import call
    monkeypatch.setattr('app.knowledge.capture.require_primary_idle',lambda s:None)
    s,j=ai_setup(memories);calls=[]
    for _ in range(2):
        with pytest.raises(ValueError,match='JSON'):
            call(s,j,[],invoke=lambda m:(calls.append(m) or 'not json'))
    assert len(calls)==1
    with connect(s.db_path) as con:
        op=con.execute('SELECT * FROM ai_operations').fetchone()
        assert (s.output_dir/op['response_path']).read_text()=='not json'


def test_budget_keeps_work_pending(memories,monkeypatch):
    from app.knowledge.ai_operations import call,WaitBudget
    monkeypatch.setattr('app.knowledge.capture.require_primary_idle',lambda s:None)
    s,j=ai_setup(memories);s.knowledge_memory_call_budget=0
    with pytest.raises(WaitBudget): call(s,j,[],invoke=lambda _:pytest.fail('must not submit'))


def test_patch_optimistic_lock_preserves_source(memories):
    path,_=memories
    memory.append_candidates(path,1,'legacy:installation',[candidate()],[1])
    current=memory.detail(path,1)
    changed=memory.patch(path,1,current['version'],title='新的标题')
    assert changed['title']=='新的标题'
    assert changed['entries']==current['entries']
    with pytest.raises(Conflict): memory.patch(path,1,current['version'],title='过期')


def test_topic_continues_without_personal_subject_and_search(memories):
    from app.knowledge import search
    path,output=memories
    search.install_schema(path)
    values=[candidate(i,'',title='AI 求职话题',type='topic') for i in (1,2)]
    memory.append_candidates(path,1,'legacy:installation',values,[1,2])
    assert len(memory.list_memories(path)['items'])==1
    search.build_index(path,output)
    hit=search.search(path,output,'求职',object_type='memory')['items'][0]
    assert hit['id']==1
    m=memory.detail(path,1);memory.patch(path,1,m['version'],status='archived')
    assert search.search(path,output,'求职',object_type='memory')['items']==[]


def test_reuse_validates_snapshot_and_keeps_unselected_candidate(memories):
    from app.knowledge.memory_pipeline import reusable
    path,output=memories
    file=output/'群/2026-09-02/run.json';run=json.loads(file.read_text())
    run['prompt_meta']={'topic_selection':{'message_snapshot_sha256':run['message_snapshot_sha256'],'candidates':[
        {'title':'求职与新工具','message_ids':['1'],'selected':False,'evidence_dialogue':[{'message_id':'1','original_text':'我推荐工具 1，计划 2026-09-08 入职'}]}]}}
    file.write_text(json.dumps(run),encoding='utf-8')
    s=SimpleNamespace(db_path=path,output_dir=output)
    with connect(path) as con:batch=dict(con.execute('SELECT * FROM source_batches').fetchone())
    assert len(reusable(s,batch,{1,2,3,4}))==1
    run['prompt_meta']['topic_selection']['message_snapshot_sha256']='bad';file.write_text(json.dumps(run))
    with pytest.raises(ValueError,match='hash'):reusable(s,batch,{1})


def test_memory_api_evidence_and_conflicts(memories):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.knowledge import router
    from app.config.settings import get_settings
    path,output=memories
    memory.append_candidates(path,1,'legacy:installation',[candidate(),candidate(2,'u2')],[1,2])
    app=FastAPI();app.include_router(router);app.dependency_overrides[get_settings]=lambda:SimpleNamespace(db_path=path,output_dir=output)
    client=TestClient(app)
    assert client.get('/api/v2/memories/1').json()['entries'][0]['sources'][0]['message_id']==1
    p=client.post('/api/v2/memories/merge-preview',json={'source_id':1,'target_id':2}).json()
    body={'source_id':1,'target_id':2,'expected_version':p['version']}
    assert client.post('/api/v2/memories/merge',json=body).status_code==200
    assert client.post('/api/v2/memories/merge',json=body).status_code==409


def test_raw_response_bytes_are_stable_on_windows(tmp_path):
    from app.knowledge.ai_operations import write_response
    f=tmp_path/'response.txt';write_response(f,'{\n"candidates": []\n}')
    assert f.read_bytes()==b'{\n"candidates": []\n}'


def test_supplement_preview_does_not_resubmit_scheduled_messages(memories):
    from app.knowledge.memory_pipeline import preview,start_backfill
    path,output=memories
    s=SimpleNamespace(db_path=path,output_dir=output,app_timezone='Asia/Shanghai',knowledge_memory_enabled=True,knowledge_memory_ai_enabled=True)
    first=preview(s,1,'2026-09-01','2026-09-03','supplement')
    assert first['message_count']==4
    start_backfill(s,first['version'],**first['filters'])
    assert preview(s,1,'2026-09-01','2026-09-03','supplement')['message_count']==0
