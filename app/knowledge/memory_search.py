"""Rebuildable Memory FTS projection with current-entity validation."""
import base64
import json
import time

from app.knowledge.db import Conflict,KnowledgeUnavailable,canonical,connect,digest,transaction
from app.knowledge.search_text import tokenize,query_terms,evidence_snippet


def update_index(path,fence=None,checkpoint=None):
    with connect(path) as con:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE name='memories'").fetchone():
            return
        ids=[r[0] for r in con.execute('SELECT id FROM memories ORDER BY id')]
    for offset in range(0,len(ids),100):
        with connect(path) as con:
            rows=[dict(r) for r in con.execute('SELECT * FROM memories WHERE id IN (SELECT value FROM json_each(?))',(canonical(ids[offset:offset+100]),))]
        prepared=[(r['id'],tokenize(r['title']+'\n'+r['summary']+'\n'+' '.join(json.loads(r['keywords_json'])))) for r in rows]
        with connect(path,write=True) as con,transaction(con):
            if fence:fence(con)
            con.executemany('INSERT OR REPLACE INTO fts_memories(rowid,tokens) VALUES(?,?)',prepared)
        if checkpoint:checkpoint()


def search(path,query,*,sort='relevance',limit=20,cursor=None,group_id=None,memory_type=None,start=None,end=None,**filters):
    from app.knowledge.search import source_version,state
    from app.knowledge.ingest import utc_stamp
    if filters.get('sender_id') or filters.get('message_type'):
        raise ValueError('用户和消息类型筛选只适用于原始消息')
    terms,match=query_terms(query)
    with connect(path) as con:
        con.execute('BEGIN')
        index=state(con)
        if not index['ready']:raise KnowledgeUnavailable('搜索索引尚未就绪')
        epoch=source_version(con);version=index['generation'];query_hash=digest([query,sort,group_id,memory_type,start,end,filters])
        offset=0
        if cursor:
            try:
                value=json.loads(base64.urlsafe_b64decode(cursor))
                if value['epoch']!=epoch or value['query_hash']!=query_hash:raise Conflict('记忆已变化，请刷新搜索')
                offset=int(value['offset'])
                if offset<0 or offset>100000:raise ValueError('游标超出范围')
            except (ValueError,KeyError) as exc:
                if isinstance(exc,Conflict):raise
                raise ValueError('游标无效') from exc
        clauses=["m.status!='archived'","m.merged_into_id IS NULL"]
        args=[match]
        if not filters.get('include_deleted'):clauses.append('g.deleted_at IS NULL')
        for key,value in [('group_id',group_id),('type',memory_type)]:
            if value is not None:clauses.append(f'm.{key}=?');args.append(value)
        if start:clauses.append('m.last_source_at>=?');args.append(utc_stamp(start))
        if end:clauses.append('m.first_source_at<?');args.append(utc_stamp(end))
        order='fts_memories.rank' if sort=='relevance' else 'm.last_source_at DESC,m.id DESC'
        deadline=time.monotonic()+0.45
        con.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
        rows=con.execute(f'''SELECT m.* FROM fts_memories JOIN memories m ON m.id=fts_memories.rowid
           JOIN groups g ON g.id=m.group_id WHERE fts_memories MATCH ? AND {' AND '.join(clauses)}
           ORDER BY {order} LIMIT ? OFFSET ?''',(*args,1001,offset)).fetchall()
        items=[];used=0
        for row in rows[:1000]:
            used+=1
            snippet=evidence_snippet(row['title']+'\n'+row['summary']+'\n'+' '.join(json.loads(row['keywords_json'])),terms)
            if snippet:
                items.append({'id':row['id'],'type':'memory','group_id':row['group_id'],'title':row['title'],
                              'kind':row['type'],'snippet':snippet,'status':row['status'],'sent_at':row['last_source_at']})
            if len(items)==limit:break
        more=used<len(rows)
        next_cursor=base64.urlsafe_b64encode(canonical({'epoch':epoch,'query_hash':query_hash,'offset':offset+used}).encode()).decode() if more else None
        return {'items':items,'next_cursor':next_cursor,'data_version':epoch,'index_version':version,'ai_calls':0,
                'warnings':['记忆是摘要索引，请点击来源核对原文。']}
