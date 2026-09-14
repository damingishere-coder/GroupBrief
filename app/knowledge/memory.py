"""Append-only, claim-level memories; projections never replace source messages."""
from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from app.knowledge.db import Conflict, KnowledgeUnavailable, canonical, connect, digest, now_iso, transaction

MIGRATION_ID = 'knowledge_004_memory'
TYPES = ('topic','event','viewpoint','recommendation','decision','highlight','consensus')
DDL = [
    '''CREATE TABLE memories(id INTEGER PRIMARY KEY, group_id INTEGER NOT NULL REFERENCES groups(id),
       source_scope TEXT NOT NULL,type TEXT NOT NULL,title TEXT NOT NULL,summary TEXT NOT NULL,
       keywords_json TEXT NOT NULL,entity_keys_json TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'active',
       first_source_at TEXT NOT NULL,last_source_at TEXT NOT NULL,evidence_strength TEXT NOT NULL,
       version INTEGER NOT NULL DEFAULT 1,merged_into_id INTEGER REFERENCES memories(id),
       created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
       UNIQUE(id,group_id,source_scope),CHECK(merged_into_id IS NULL OR merged_into_id!=id))''',
    'CREATE INDEX ix_memories_group ON memories(group_id,type,status,last_source_at)',
    '''CREATE TABLE memory_entries(id INTEGER PRIMARY KEY,memory_id INTEGER NOT NULL,group_id INTEGER NOT NULL,
       source_scope TEXT NOT NULL,job_id INTEGER REFERENCES knowledge_jobs(id),entry_key TEXT NOT NULL UNIQUE,
       entry_type TEXT NOT NULL,summary TEXT NOT NULL,observed_start TEXT NOT NULL,observed_end TEXT NOT NULL,
       event_at TEXT,event_time_basis TEXT NOT NULL,participants_json TEXT NOT NULL,claims_json TEXT NOT NULL,
       status TEXT NOT NULL DEFAULT 'active',supersedes_entry_id INTEGER REFERENCES memory_entries(id),
       created_at TEXT NOT NULL,UNIQUE(id,group_id,source_scope),
       FOREIGN KEY(memory_id,group_id,source_scope) REFERENCES memories(id,group_id,source_scope))''',
    'CREATE INDEX ix_memory_entries_time ON memory_entries(memory_id,observed_start,id)',
    'CREATE UNIQUE INDEX uq_message_scope ON messages(id,group_id,source_scope)',
    '''CREATE TABLE memory_sources(entry_id INTEGER NOT NULL,group_id INTEGER NOT NULL,source_scope TEXT NOT NULL,
       claim_key TEXT NOT NULL,message_id INTEGER NOT NULL,relation TEXT NOT NULL,quote TEXT NOT NULL,
       PRIMARY KEY(entry_id,claim_key,message_id),
       FOREIGN KEY(entry_id,group_id,source_scope) REFERENCES memory_entries(id,group_id,source_scope),
       FOREIGN KEY(message_id,group_id,source_scope) REFERENCES messages(id,group_id,source_scope))''',
    'CREATE INDEX ix_memory_sources_message ON memory_sources(message_id,entry_id)',
    '''CREATE TABLE ai_operations(id INTEGER PRIMARY KEY,job_id INTEGER NOT NULL REFERENCES knowledge_jobs(id),
       group_id INTEGER NOT NULL REFERENCES groups(id),operation_key TEXT NOT NULL,attempt_no INTEGER NOT NULL,
       input_hash TEXT NOT NULL,prompt_version TEXT NOT NULL,schema_version TEXT NOT NULL,
       provider TEXT NOT NULL,model TEXT NOT NULL,status TEXT NOT NULL,request_id TEXT NOT NULL DEFAULT '',
       response_path TEXT NOT NULL DEFAULT '',response_hash TEXT NOT NULL DEFAULT '',
       validated_result_json TEXT NOT NULL DEFAULT '{}',usage_json TEXT NOT NULL DEFAULT '{}',
       budget_day TEXT NOT NULL,budget_input INTEGER NOT NULL,budget_output INTEGER NOT NULL,
       created_at TEXT NOT NULL,updated_at TEXT NOT NULL,UNIQUE(operation_key,attempt_no))''',
    "CREATE UNIQUE INDEX uq_ai_active ON ai_operations(operation_key) WHERE status IN ('SUBMITTING','HOLD_UNKNOWN','RECEIVED','VALIDATED')",
    'CREATE INDEX ix_ai_budget ON ai_operations(group_id,budget_day,status)',
    "CREATE VIRTUAL TABLE fts_memories USING fts5(tokens,tokenize='unicode61')",
]
CHECKSUM = digest(DDL)


def install_schema(path):
    with connect(path,write=True) as con,transaction(con):
        old=con.execute('SELECT checksum FROM schema_migrations WHERE migration_id=?',(MIGRATION_ID,)).fetchone()
        if old:
            if old[0]!=CHECKSUM:
                raise KnowledgeUnavailable('Memory Schema 不兼容')
            return False
        for sql in DDL:
            con.execute(sql)
        con.execute('INSERT INTO schema_migrations VALUES(?,?,?)',(MIGRATION_ID,now_iso(),CHECKSUM))
        return True


class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)


class Source(Strict):
    message_id: int = Field(gt=0)
    quote: str = Field(min_length=1,max_length=4000)
    relation: Literal['supports','contradicts','reports'] = 'reports'


class Claim(Strict):
    key: str = Field(min_length=1,max_length=64)
    text: str = Field(min_length=1,max_length=2000)
    sources: list[Source] = Field(min_length=1,max_length=30)


class Candidate(Strict):
    type: Literal['topic','event','viewpoint','recommendation','decision','highlight','consensus']
    title: str = Field(min_length=1,max_length=120)
    summary: str = Field(min_length=1,max_length=2000)
    keywords: list[str] = Field(default_factory=list,max_length=15)
    # Canonical message sender IDs only. Free-form names cannot establish subjects.
    subject_sender_ids: list[str] = Field(default_factory=list,max_length=10)
    claims: list[Claim] = Field(min_length=1,max_length=15)
    event_at: str | None = None
    event_time_quote: str | None = None


class Extraction(Strict):
    candidates: list[Candidate] = Field(max_length=20)


def validate_candidate(con,group_id,scope,item,allowed_ids):
    candidate=Candidate.model_validate(item)
    if len(set(c.key for c in candidate.claims))!=len(candidate.claims):
        raise ValueError('断言 key 重复')
    ids={s.message_id for c in candidate.claims for s in c.sources}
    if not ids.issubset(set(allowed_ids)):
        raise ValueError('来源超出冻结输入；整条候选已隔离')
    rows={r['id']:dict(r) for r in con.execute('SELECT * FROM messages WHERE id IN (SELECT value FROM json_each(?))',(canonical(sorted(ids)),))}
    if set(rows)!=ids or any(r['group_id']!=group_id or r['source_scope']!=scope or r['validation_state']!='valid' for r in rows.values()):
        raise ValueError('来源不存在、跨群、跨账号或存在冲突')
    for claim in candidate.claims:
        if len({s.message_id for s in claim.sources})!=len(claim.sources):
            raise ValueError('断言来源重复')
        for source in claim.sources:
            if source.quote not in rows[source.message_id]['content']:
                raise ValueError('引文与原消息不一致')
    reliable={r['sender_id'] for r in rows.values() if r['sender_id'] and r['sender_identity_quality']=='reliable'}
    if not set(candidate.subject_sender_ids).issubset(reliable):
        raise ValueError('主体必须有可靠发言身份，昵称不能建立长期身份')
    if candidate.type=='consensus':
        supporters={rows[s.message_id]['sender_id'] for c in candidate.claims for s in c.sources if s.relation=='supports'}
        if len(supporters & reliable)<3:
            raise ValueError('共识缺少三个可靠身份的明确支持来源')
    event_at=None
    if candidate.event_at:
        # First release only accepts dates literally stated in evidence.
        from app.knowledge.ingest import utc_stamp
        from datetime import date
        day=date.fromisoformat(candidate.event_at).isoformat()
        if not candidate.event_time_quote or day not in candidate.event_time_quote or not any(candidate.event_time_quote in r['content'] for r in rows.values()):
            raise ValueError('事件日期必须由原文明确给出 ISO 日期；相对日期保留为观察时间')
        event_at=utc_stamp(day)
    participants=sorted({(r['sender_id'],r['sender_name'],r['sender_identity_quality']) for r in rows.values()})
    return candidate,rows,participants,event_at


def descendants(con,memory_id):
    return [r[0] for r in con.execute('''WITH RECURSIVE tree(id) AS (SELECT ? UNION SELECT m.id FROM memories m JOIN tree t ON m.merged_into_id=t.id)
       SELECT id FROM tree''',(memory_id,))]


def project(con,memory_id):
    ids=descendants(con,memory_id)
    entries=con.execute("SELECT * FROM memory_entries WHERE memory_id IN (SELECT value FROM json_each(?)) AND status='active' ORDER BY observed_start,id",(canonical(ids),)).fetchall()
    if entries:
        summaries=list(dict.fromkeys(r['summary'] for r in entries))[-5:]
        con.execute('UPDATE memories SET summary=?,first_source_at=?,last_source_at=?,updated_at=?,version=version+1 WHERE id=?',
                    ('\n'.join(summaries)[:8000],min(r['observed_start'] for r in entries),max(r['observed_end'] for r in entries),now_iso(),memory_id))


def recall(con,group_id,scope,candidate):
    # Bounded output, full history for exact subjects/title. Similarity is never proof.
    rows=con.execute("SELECT * FROM memories WHERE group_id=? AND source_scope=? AND merged_into_id IS NULL AND status IN ('active','review') ORDER BY (title=?) DESC,last_source_at DESC,id DESC",(group_id,scope,candidate.title))
    result=[]
    words=set(candidate.keywords)
    for row in rows:
        entities=json.loads(row['entity_keys_json'])
        same_subject=bool(candidate.subject_sender_ids) and entities==sorted(set(candidate.subject_sender_ids))
        if row['title']==candidate.title or same_subject or words.intersection(json.loads(row['keywords_json'])):
            result.append(dict(row))
        if len(result)==20:
            break
    return result


def append_candidates(path,group_id,scope,candidates,allowed_ids,*,job_id=None,fence=None):
    result={'entries':[],'reused':0,'rejected':[]}
    for index,item in enumerate(candidates):
        try:
            with connect(path,write=True) as con,transaction(con):
                if fence:
                    fence(con)
                if not con.execute('SELECT 1 FROM groups WHERE id=? AND deleted_at IS NULL',(group_id,)).fetchone():
                    raise ValueError('群不存在或已归档')
                c,rows,participants,event_at=validate_candidate(con,group_id,scope,item,allowed_ids)
                # Evidence and literal claims define an entry independently of task/run IDs.
                entry_key=digest([group_id,scope,c.type,sorted((s.message_id,s.quote,s.relation) for claim in c.claims for s in claim.sources)])
                old=con.execute('SELECT id FROM memory_entries WHERE entry_key=?',(entry_key,)).fetchone()
                if old:
                    result['entries'].append(old[0]);result['reused']+=1
                    continue
                matches=recall(con,group_id,scope,c)
                entities=sorted(set(c.subject_sender_ids))
                clear=[m for m in matches if m['type']==c.type and m['title']==c.title and json.loads(m['entity_keys_json'])==entities
                       and (entities or (c.type=='topic' and len(c.title.strip())>=4))]
                start=min(r['sent_at'] for r in rows.values());end=max(r['sent_at'] for r in rows.values())
                if len(clear)==1:
                    memory_id=clear[0]['id']
                else:
                    cur=con.execute('''INSERT INTO memories(group_id,source_scope,type,title,summary,keywords_json,entity_keys_json,status,
                       first_source_at,last_source_at,evidence_strength,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                       (group_id,scope,c.type,c.title,c.summary,canonical(c.keywords),canonical(entities),'review' if matches else 'active',start,end,'quoted_sources',now_iso(),now_iso()))
                    memory_id=cur.lastrowid
                cur=con.execute('''INSERT INTO memory_entries(memory_id,group_id,source_scope,job_id,entry_key,entry_type,summary,
                   observed_start,observed_end,event_at,event_time_basis,participants_json,claims_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                   (memory_id,group_id,scope,job_id,entry_key,c.type,c.summary,start,end,event_at,'explicit_date' if event_at else 'message_time',
                    canonical([{'sender_id':p[0],'sender_name':p[1],'identity_quality':p[2]} for p in participants]),canonical([x.model_dump() for x in c.claims]),now_iso()))
                entry_id=cur.lastrowid
                for claim in c.claims:
                    for s in claim.sources:
                        con.execute('INSERT INTO memory_sources VALUES(?,?,?,?,?,?,?)',(entry_id,group_id,scope,claim.key,s.message_id,s.relation,s.quote))
                project(con,memory_id)
                result['entries'].append(entry_id)
        except ValueError as exc:
            if isinstance(exc,Conflict):
                raise
            result['rejected'].append({'candidate':index,'reason':str(exc)[:500]})
    return result


def decode(row):
    result=dict(row)
    for key in ('keywords','entity_keys','participants','claims'):
        if key+'_json' in result:
            result[key]=json.loads(result.pop(key+'_json'))
    return result


def list_memories(path,group_id=None,memory_type=None,status=None,limit=50,offset=0):
    clauses=['g.deleted_at IS NULL'];args=[]
    for key,value in [('group_id',group_id),('type',memory_type),('status',status)]:
        if value is not None:
            clauses.append(f'm.{key}=?');args.append(value)
    with connect(path) as con:
        rows=con.execute(f"SELECT m.* FROM memories m JOIN groups g ON g.id=m.group_id WHERE {' AND '.join(clauses)} ORDER BY m.last_source_at DESC,m.id DESC LIMIT ? OFFSET ?",(*args,limit+1,offset)).fetchall()
        return {'items':[decode(r) for r in rows[:limit]],'next_offset':offset+limit if len(rows)>limit else None}


def detail(path,memory_id,offset=0,limit=30):
    with connect(path) as con:
        row=con.execute('SELECT * FROM memories WHERE id=?',(memory_id,)).fetchone()
        if not row:
            raise KeyError(memory_id)
        ids=descendants(con,memory_id)
        entries=con.execute('SELECT * FROM memory_entries WHERE memory_id IN (SELECT value FROM json_each(?)) ORDER BY observed_start,id LIMIT ? OFFSET ?',(canonical(ids),limit+1,offset)).fetchall()
        result=decode(row);result['entries']=[]
        merge_op=con.execute("SELECT id FROM knowledge_jobs WHERE job_kind='memory_merge' AND json_extract(result_json,'$.source_id')=? ORDER BY id DESC LIMIT 1",(memory_id,)).fetchone()
        result['merge_operation_id']=merge_op['id'] if merge_op and row['merged_into_id'] else None
        for entry in entries[:limit]:
            value=decode(entry)
            value['sources']=[dict(r) for r in con.execute('''SELECT s.*,m.validation_state,m.sender_name,m.sent_at FROM memory_sources s
               JOIN messages m ON m.id=s.message_id WHERE entry_id=? ORDER BY claim_key,message_id''',(entry['id'],))]
            result['entries'].append(value)
        result['next_offset']=offset+limit if len(entries)>limit else None
        return result


def merge_preview(path,source_id,target_id):
    with connect(path) as con:
        return _preview(con,source_id,target_id)


def _preview(con,source_id,target_id):
    if source_id==target_id:
        raise ValueError('不能合并到自身')
    rows={r['id']:dict(r) for r in con.execute('SELECT * FROM memories WHERE id IN (?,?)',(source_id,target_id))}
    if len(rows)!=2:
        raise KeyError(source_id)
    source,target=rows[source_id],rows[target_id]
    if (source['group_id'],source['source_scope'])!=(target['group_id'],target['source_scope']):
        raise ValueError('禁止跨群或账号合并')
    if source['merged_into_id'] or target['merged_into_id'] or target_id in descendants(con,source_id):
        raise Conflict('对象已合并或会形成循环，请刷新')
    return {'source':decode(source),'target':decode(target),'version':digest([source,target]),
            'warnings':['合并只建立重定向，原始进展和来源保留；不同主体请保持分开。']}


def audit(con,kind,group_id,result):
    key=digest([kind,result,now_iso()])
    cur=con.execute('''INSERT INTO knowledge_jobs(job_key,job_kind,group_id,scope_json,input_hash,status,result_json,created_at,updated_at)
       VALUES(?,?,?,'{}',?,'SUCCEEDED',?,?,?)''',(key,kind,group_id,key,canonical(result),now_iso(),now_iso()))
    return cur.lastrowid


def merge(path,source_id,target_id,expected_version):
    with connect(path,write=True) as con,transaction(con):
        preview=_preview(con,source_id,target_id)
        if preview['version']!=expected_version:
            raise Conflict('预览已过期，请重新核对')
        con.execute('UPDATE memories SET merged_into_id=?,version=version+1,updated_at=? WHERE id=?',(target_id,now_iso(),source_id))
        project(con,target_id)
        result={'source_id':source_id,'target_id':target_id,'before':preview,'actor':'local_user'}
        result['after_versions']={str(r['id']):r['version'] for r in con.execute('SELECT id,version FROM memories WHERE id IN (?,?)',(source_id,target_id))}
        return {'operation_id':audit(con,'memory_merge',preview['source']['group_id'],result),**result}


def undo_merge(path,memory_id,operation_id,expected_version):
    with connect(path,write=True) as con,transaction(con):
        op=con.execute("SELECT * FROM knowledge_jobs WHERE id=? AND job_kind='memory_merge'",(operation_id,)).fetchone()
        if not op:
            raise KeyError(operation_id)
        saved=json.loads(op['result_json']);source_id=saved['source_id'];target_id=saved['target_id']
        if memory_id!=source_id:
            raise ValueError('操作不属于该记忆')
        source=con.execute('SELECT * FROM memories WHERE id=?',(source_id,)).fetchone()
        target=con.execute('SELECT * FROM memories WHERE id=?',(target_id,)).fetchone()
        if source['version']!=expected_version or source['merged_into_id']!=target_id or target['merged_into_id']:
            raise Conflict('合并关系已变化，请刷新后核对')
        con.execute('UPDATE memories SET merged_into_id=NULL,version=version+1,updated_at=? WHERE id=?',(now_iso(),source_id))
        project(con,source_id);project(con,target_id)
        return {'operation_id':audit(con,'memory_undo_merge',source['group_id'],{'undoes':operation_id,'source_id':source_id,'target_id':target_id,'actor':'local_user'})}


def patch(path,memory_id,expected_version,*,title=None,keywords=None,status=None):
    if status is not None and status not in {'active','review','archived'}:
        raise ValueError('状态无效')
    if title is not None and not 1<=len(title.strip())<=120:
        raise ValueError('标题长度无效')
    with connect(path,write=True) as con,transaction(con):
        row=con.execute('SELECT * FROM memories WHERE id=?',(memory_id,)).fetchone()
        if not row:
            raise KeyError(memory_id)
        if row['version']!=expected_version:
            raise Conflict('记忆已更新，请刷新')
        con.execute('UPDATE memories SET title=?,keywords_json=?,status=?,version=version+1,updated_at=? WHERE id=?',
                    (title.strip() if title else row['title'],canonical(keywords) if keywords is not None else row['keywords_json'],status or row['status'],now_iso(),memory_id))
        audit(con,'memory_edit',row['group_id'],{'memory_id':memory_id,'before':dict(row),'changes':{'title':title,'keywords':keywords,'status':status},'actor':'local_user'})
    return detail(path,memory_id)
