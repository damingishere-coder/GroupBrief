"""Small timelines that reference existing evidence-bearing memory entries."""
import json

from app.knowledge.db import Conflict,KnowledgeUnavailable,canonical,connect,digest,now_iso,transaction
from app.knowledge.memory import audit,decode

MIGRATION_ID='knowledge_005_storylines'
DDL=[
    '''CREATE TABLE storylines(id INTEGER PRIMARY KEY,group_id INTEGER NOT NULL REFERENCES groups(id),
       source_scope TEXT NOT NULL,title TEXT NOT NULL,summary TEXT NOT NULL,subject_keys_json TEXT NOT NULL,
       status TEXT NOT NULL DEFAULT 'active',version INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
       UNIQUE(id,group_id,source_scope))''',
    'CREATE INDEX ix_storylines_group ON storylines(group_id,status,updated_at)',
    '''CREATE TABLE storyline_entries(storyline_id INTEGER NOT NULL,memory_entry_id INTEGER NOT NULL,
       group_id INTEGER NOT NULL,source_scope TEXT NOT NULL,label TEXT NOT NULL,link_origin TEXT NOT NULL,
       PRIMARY KEY(storyline_id,memory_entry_id),
       FOREIGN KEY(storyline_id,group_id,source_scope) REFERENCES storylines(id,group_id,source_scope),
       FOREIGN KEY(memory_entry_id,group_id,source_scope) REFERENCES memory_entries(id,group_id,source_scope))''',
    'CREATE INDEX ix_storyline_entry ON storyline_entries(memory_entry_id,storyline_id)',
    'ALTER TABLE report_evidence ADD COLUMN memory_entry_id INTEGER REFERENCES memory_entries(id)',
    '''CREATE TRIGGER report_entry_group_insert BEFORE INSERT ON report_evidence WHEN NEW.memory_entry_id IS NOT NULL
       AND NOT EXISTS(SELECT 1 FROM memory_entries WHERE id=NEW.memory_entry_id AND group_id=NEW.group_id)
       BEGIN SELECT RAISE(ABORT,'cross-group memory report evidence'); END''',
    '''CREATE TRIGGER report_entry_group_update BEFORE UPDATE ON report_evidence WHEN NEW.memory_entry_id IS NOT NULL
       AND NOT EXISTS(SELECT 1 FROM memory_entries WHERE id=NEW.memory_entry_id AND group_id=NEW.group_id)
       BEGIN SELECT RAISE(ABORT,'cross-group memory report evidence'); END''',
]
CHECKSUM=digest(DDL)


def install_schema(path):
    with connect(path,write=True) as con,transaction(con):
        old=con.execute('SELECT checksum FROM schema_migrations WHERE migration_id=?',(MIGRATION_ID,)).fetchone()
        if old:
            if old[0]!=CHECKSUM:raise KnowledgeUnavailable('Storyline Schema 不兼容')
            return False
        for sql in DDL:con.execute(sql)
        con.execute('INSERT INTO schema_migrations VALUES(?,?,?)',(MIGRATION_ID,now_iso(),CHECKSUM))
        return True


def preview(con,group_id,entry_ids,title='',storyline_id=None):
    ids=sorted(set(entry_ids))
    if not 1<=len(ids)<=100:raise ValueError('每次关联 1—100 条进展')
    entries=[dict(r) for r in con.execute('''SELECT e.*,m.entity_keys_json,m.title memory_title FROM memory_entries e
        JOIN memories m ON m.id=e.memory_id WHERE e.id IN (SELECT value FROM json_each(?))
        ORDER BY coalesce(e.event_at,e.observed_start),e.id''',(canonical(ids),))]
    if len(entries)!=len(ids) or any(e['group_id']!=group_id or e['status']!='active' for e in entries):
        raise ValueError('进展不存在、已失效或不属于此群')
    identities={(e['source_scope'],e['entity_keys_json']) for e in entries}
    if len(identities)!=1 or not json.loads(entries[0]['entity_keys_json']):
        raise ValueError('故事线进展必须属于同一账号及相同的可靠主体；不能凭昵称串联')
    invalid=con.execute("""SELECT 1 FROM memory_sources s JOIN messages m ON m.id=s.message_id
        WHERE s.entry_id IN (SELECT value FROM json_each(?)) AND m.validation_state!='valid' LIMIT 1""",(canonical(ids),)).fetchone()
    if invalid:raise ValueError('进展来源存在冲突，请先核对')
    existing=None
    if storyline_id:
        row=con.execute('SELECT * FROM storylines WHERE id=?',(storyline_id,)).fetchone()
        if not row:raise KeyError(storyline_id)
        existing=dict(row)
        if (row['group_id'],row['source_scope'],row['subject_keys_json'])!=(group_id,entries[0]['source_scope'],entries[0]['entity_keys_json']):
            raise ValueError('主体或群归属与已有故事线不一致')
        title=row['title']
    if not 1<=len(title.strip())<=120:raise ValueError('故事线标题需为 1—120 字符')
    data={'group_id':group_id,'entry_ids':ids,'title':title.strip(),'storyline_id':storyline_id,'existing':existing,
          'entries':entries,'subjects':json.loads(entries[0]['entity_keys_json']),'source_scope':entries[0]['source_scope']}
    return {**data,'version':digest(data),'warnings':['未明确给出事件日期的节点按发言观察时间排列，不推测真实发生时间。']}


def link_preview(path,**kwargs):
    with connect(path) as con:return preview(con,**kwargs)


def link(path,expected_version,**kwargs):
    with connect(path,write=True) as con,transaction(con):
        previous=con.execute("SELECT id,result_json FROM knowledge_jobs WHERE job_kind='storyline_link' AND json_extract(result_json,'$.preview_version')=? LIMIT 1",(expected_version,)).fetchone()
        if previous:
            return {'id':json.loads(previous['result_json'])['storyline_id'],'operation_id':previous['id'],'reused':True}
        p=preview(con,**kwargs)
        if p['version']!=expected_version:raise Conflict('进展或故事线已变化，请重新预览')
        sid=p['storyline_id']
        if not sid:
            cur=con.execute('''INSERT INTO storylines(group_id,source_scope,title,summary,subject_keys_json,created_at,updated_at)
                VALUES(?,?,?,'',?,?,?)''',(p['group_id'],p['source_scope'],p['title'],canonical(p['subjects']),now_iso(),now_iso()))
            sid=cur.lastrowid
        for e in p['entries']:
            con.execute('INSERT INTO storyline_entries VALUES(?,?,?,?,?,?) ON CONFLICT DO NOTHING',
                        (sid,e['id'],p['group_id'],p['source_scope'],e['memory_title'],'local_user'))
        project(con,sid)
        return {'id':sid,'operation_id':audit(con,'storyline_link',p['group_id'],{'storyline_id':sid,'entry_ids':p['entry_ids'],'actor':'local_user','preview_version':expected_version})}


def project(con,sid):
    rows=con.execute('''SELECT e.summary FROM memory_entries e JOIN storyline_entries s ON s.memory_entry_id=e.id
        WHERE s.storyline_id=? ORDER BY coalesce(e.event_at,e.observed_start) DESC,e.id DESC LIMIT 3''',(sid,)).fetchall()
    con.execute('UPDATE storylines SET summary=?,version=version+1,updated_at=? WHERE id=?',('\n'.join(r[0] for r in reversed(rows)),now_iso(),sid))


def unlink(path,sid,entry_id,expected_version):
    with connect(path,write=True) as con,transaction(con):
        row=con.execute('SELECT * FROM storylines WHERE id=?',(sid,)).fetchone()
        if not row:raise KeyError(sid)
        if row['version']!=expected_version:raise Conflict('故事线已变化，请刷新')
        old=con.execute('SELECT * FROM storyline_entries WHERE storyline_id=? AND memory_entry_id=?',(sid,entry_id)).fetchone()
        if not old:raise KeyError(entry_id)
        con.execute('DELETE FROM storyline_entries WHERE storyline_id=? AND memory_entry_id=?',(sid,entry_id))
        project(con,sid)
        return {'operation_id':audit(con,'storyline_unlink',row['group_id'],{'removed_link':dict(old),'actor':'local_user'}),
                'note':'只解除关联，进展与原消息保留，可以重新关联。'}


def list_storylines(path,group_id=None,limit=50):
    with connect(path) as con:
        return {'items':[dict(r) for r in con.execute('''SELECT s.* FROM storylines s JOIN groups g ON g.id=s.group_id
            WHERE g.deleted_at IS NULL AND (? IS NULL OR s.group_id=?) ORDER BY s.updated_at DESC,s.id DESC LIMIT ?''',(group_id,group_id,limit))]}


def detail(path,sid):
    with connect(path) as con:
        row=con.execute('SELECT * FROM storylines WHERE id=?',(sid,)).fetchone()
        if not row:raise KeyError(sid)
        value=dict(row);value['subjects']=json.loads(value.pop('subject_keys_json'))
        entries=con.execute('''SELECT e.*,s.label,s.link_origin FROM memory_entries e JOIN storyline_entries s ON s.memory_entry_id=e.id
            WHERE s.storyline_id=? ORDER BY coalesce(e.event_at,e.observed_start),e.id''',(sid,)).fetchall()
        value['entries']=[]
        for row in entries:
            entry=decode(row)
            entry['sources']=[dict(r) for r in con.execute('SELECT s.*,m.validation_state FROM memory_sources s JOIN messages m ON m.id=s.message_id WHERE entry_id=?',(row['id'],))]
            value['entries'].append(entry)
        return value
