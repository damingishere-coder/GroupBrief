"""Rebuildable FTS5 projections. All message hits resolve to canonical sources."""
from __future__ import annotations

import base64
import json
import time
from pathlib import Path

from app.knowledge.db import Conflict, KnowledgeUnavailable, canonical, connect, digest, now_iso, transaction
from app.knowledge.ingest import resolve_group, safe_path, utc_stamp
from app.knowledge.search_text import evidence_snippet, query_terms, tokenize
from app.knowledge.service import data_version, visible_where

MIGRATION_ID='knowledge_003_search'
DDL=[
    '''CREATE TABLE search_state(id INTEGER PRIMARY KEY CHECK(id=1),active_slot TEXT NOT NULL CHECK(active_slot IN ('a','b')),
       generation INTEGER NOT NULL,ready INTEGER NOT NULL,watermark_json TEXT NOT NULL,updated_at TEXT NOT NULL)''',
    "INSERT INTO search_state VALUES(1,'a',0,0,'{}','')",
]
for slot in ('a','b'):
    DDL.extend([
        f"CREATE VIRTUAL TABLE fts_messages_{slot} USING fts5(tokens,tokenize='unicode61')",
        f'''CREATE VIRTUAL TABLE fts_reports_{slot} USING fts5(
           ref UNINDEXED,group_id UNINDEXED,kind UNINDEXED,title UNINDEXED,body UNINDEXED,
           locator UNINDEXED,source_hash UNINDEXED,period_start UNINDEXED,tokens,tokenize='unicode61')''',
    ])
CHECKSUM=digest(DDL)


def install_schema(path):
    with connect(path,write=True) as con,transaction(con):
        row=con.execute('SELECT checksum FROM schema_migrations WHERE migration_id=?',(MIGRATION_ID,)).fetchone()
        if row:
            if row[0]!=CHECKSUM:
                raise KnowledgeUnavailable('搜索 Schema 不兼容')
            return False
        for sql in DDL:
            con.execute(sql)
        con.execute('INSERT INTO schema_migrations VALUES(?,?,?)',(MIGRATION_ID,now_iso(),CHECKSUM))
        return True


def source_version(con):
    report_version=con.execute('SELECT coalesce(max(id),0) FROM report_insights').fetchone()[0]
    groups=[tuple(r) for r in con.execute('SELECT id,deleted_at FROM groups ORDER BY id')]
    memory_version=[]
    if con.execute("SELECT 1 FROM sqlite_master WHERE name='memories'").fetchone():
        memory_version=[tuple(r) for r in con.execute('SELECT id,version FROM memories ORDER BY id')]
    return digest([data_version(con),report_version,groups,memory_version])


def state(con):
    row=con.execute('SELECT * FROM search_state WHERE id=1').fetchone()
    if row is None:
        raise KnowledgeUnavailable('搜索索引状态丢失，请重建')
    result=dict(row)
    if result['active_slot'] not in {'a','b'}:
        raise KnowledgeUnavailable('搜索索引状态损坏')
    result['watermark']=json.loads(result.pop('watermark_json'))
    return result


def legacy_hash(output,locator):
    run_path=safe_path(output,locator)
    if run_path.name!='run.json':
        raise ValueError('报告来源无效')
    run=json.loads(run_path.read_text(encoding='utf-8'))
    manifest={'local_group_id':run.get('group_id'),'upstream_group_id':str(run.get('wechat_group_id') or ''),'locator':locator}
    hashes=[]
    for name in ('ranking.txt','image_prompt.txt'):
        file=safe_path(output,(run_path.parent/name).relative_to(output).as_posix())
        if file.is_file() and file.stat().st_size<=1024*1024:
            hashes.append([name,digest(file.read_bytes())])
    return digest([hashes,manifest])


def legacy_documents(path,output):
    documents=[]
    for run_path in sorted(output.glob('*/*/run.json')):
        locator=run_path.relative_to(output).as_posix()
        try:
            run=json.loads(safe_path(output,locator).read_text(encoding='utf-8'))
            if not isinstance(run,dict):
                continue
            texts=[]; hashes=[]
            for name in ('ranking.txt','image_prompt.txt'):
                file=safe_path(output,(run_path.parent/name).relative_to(output).as_posix())
                if file.is_file() and file.stat().st_size<=1024*1024:
                    data=file.read_bytes()
                    texts.append(data.decode('utf-8-sig')); hashes.append([name,digest(data)])
            if not texts:
                continue
            manifest={'local_group_id':run.get('group_id'),'upstream_group_id':str(run.get('wechat_group_id') or ''),'locator':locator}
            with connect(path) as con:
                group_id,_=resolve_group(con,manifest)
            start=run.get('period_start') or run.get('run_date')
            documents.append({'ref':'legacy:'+locator,'group_id':group_id,'kind':'legacy',
                'title':f'{run_path.parent.parent.name} / {run_path.parent.name} 历史群报',
                'body':'\n\n'.join(texts),'locator':locator,'source_hash':digest([hashes,manifest]),
                'period_start':utc_stamp(start) if start else '', 'hashes':hashes, 'identity':manifest})
        except (ValueError,OSError):
            continue
    return documents


def database_reports(con,report_id=None):
    columns={r[1] for r in con.execute('PRAGMA table_info(reports)')}
    if not {'ranking_text','prompt_text','group_run_id'}.issubset(columns):
        return []
    query='''SELECT r.id,r.ranking_text,r.prompt_text,gr.group_id,run.report_date
        FROM reports r LEFT JOIN group_runs gr ON gr.id=r.group_run_id LEFT JOIN runs run ON run.id=gr.run_id'''
    rows=con.execute(query+(' WHERE r.id=?' if report_id is not None else ' ORDER BY r.id'),(report_id,) if report_id is not None else ()).fetchall()
    documents=[]
    for row in rows:
        body='\n\n'.join(filter(None,[row['ranking_text'],row['prompt_text']]))
        if not body:
            continue
        documents.append({'ref':f"v1:{row['id']}",'group_id':row['group_id'],'kind':'legacy',
            'title':f"历史数据库群报 #{row['id']} / {row['report_date'] or ''}",'body':body,'locator':'',
            'source_hash':digest([body,row['group_id'],row['report_date']]),
            'period_start':utc_stamp(str(row['report_date'])) if row['report_date'] else ''})
    return documents


def index_manifest(path,output):
    with connect(path) as con:
        batches=[r[0] for r in con.execute("SELECT id FROM source_batches WHERE import_status='complete' ORDER BY id")]
        reports=[r[0] for r in con.execute('SELECT id FROM report_insights ORDER BY id')]
        version=source_version(con)
        historical=database_reports(con)
    legacy=legacy_documents(path,output)+historical
    return {'batches':batches,'reports':reports,'legacy':legacy,'data_version':version}


def build_index(path,output,*,rebuild=False,fence=None,checkpoint=None):
    manifest=index_manifest(path,output)
    with connect(path) as con:
        initial=state(con)
    slot=('b' if initial['active_slot']=='a' else 'a') if rebuild else initial['active_slot']
    message_table=f'fts_messages_{slot}'; report_table=f'fts_reports_{slot}'
    previous={} if rebuild else initial['watermark']
    known_batches=set(previous.get('batches',[])); known_reports=set(previous.get('reports',[]))
    known_legacy=previous.get('legacy',{})
    def write(operation):
        if checkpoint:
            checkpoint()
        with connect(path,write=True) as con,transaction(con):
            if fence:
                fence(con)
            if state(con)['active_slot']!=initial['active_slot']:
                raise Conflict('索引已切换，请重新领取任务')
            operation(con)
            if not rebuild:
                con.execute('UPDATE search_state SET generation=generation+1,updated_at=? WHERE id=1',(now_iso(),))
    if rebuild:
        # Clear only the inactive derived index, in bounded batches.
        for table in (message_table,report_table):
            while True:
                with connect(path) as con:
                    ids=[r[0] for r in con.execute(f'SELECT rowid FROM {table} LIMIT 250')]
                if not ids:
                    break
                write(lambda con:con.executemany(f'DELETE FROM {table} WHERE rowid=?',[(i,) for i in ids]))
    for batch_id in manifest['batches']:
        if batch_id in known_batches:
            continue
        ordinal=-1
        while True:
            with connect(path) as con:
                rows=con.execute('''SELECT ms.row_ordinal,m.id,m.content FROM message_sources ms JOIN messages m ON m.id=ms.message_id
                    WHERE ms.batch_id=? AND ms.row_ordinal>? ORDER BY ms.row_ordinal LIMIT 250''',(batch_id,ordinal)).fetchall()
            if not rows:
                break
            prepared=[(r['id'],tokenize(r['content'])) for r in rows]
            write(lambda con:con.executemany(f'INSERT OR REPLACE INTO {message_table}(rowid,tokens) VALUES(?,?)',prepared))
            ordinal=rows[-1]['row_ordinal']
    documents=[]
    with connect(path) as con:
        for report_id in manifest['reports']:
            if report_id in known_reports:
                continue
            report=con.execute('SELECT * FROM report_insights WHERE id=?',(report_id,)).fetchone()
            metrics=json.loads(report['metrics_json'])
            sections=json.loads(report['sections_json'])
            body='\n'.join(['周度洞察' if report['kind']=='weekly' else '月度洞察',
                '消息数 '+str(metrics['message_count']),'活跃人数 '+str(metrics['speaker_count']),
                *(m['name'] for m in metrics.get('members',[])),canonical(sections)])
            documents.append({'ref':f'insight:{report_id}','group_id':report['group_id'],'kind':report['kind'],
                'title':f"{report['kind']} {report['period_start']} v{report['revision']}",'body':body,
                'locator':'','source_hash':report['input_hash'],'period_start':report['period_start']})
    documents.extend(d for d in manifest['legacy'] if known_legacy.get(d['ref'])!=d['source_hash'])
    for document in documents:
        prepared=tuple(document[k] for k in ('ref','group_id','kind','title','body','locator','source_hash','period_start'))+(tokenize(document['title']+'\n'+document['body']),)
        def put(con):
            con.execute(f'DELETE FROM {report_table} WHERE ref=?',(document['ref'],))
            con.execute(f'INSERT INTO {report_table} VALUES(?,?,?,?,?,?,?,?,?)',prepared)
        write(put)
    # Removed legacy files must disappear; current insights are checked at query time.
    removed=set(known_legacy)-{d['ref'] for d in manifest['legacy']}
    for ref in removed:
        write(lambda con:con.execute(f'DELETE FROM {report_table} WHERE ref=?',(ref,)))
    from app.knowledge.memory_search import update_index
    update_index(path,fence=fence,checkpoint=checkpoint)
    watermark={'batches':manifest['batches'],'reports':manifest['reports'],
               'legacy':{d['ref']:d['source_hash'] for d in manifest['legacy']},'data_version':manifest['data_version']}
    if checkpoint:
        checkpoint()
    with connect(path,write=True) as con,transaction(con):
        if fence:
            fence(con)
        if state(con)['active_slot']!=initial['active_slot']:
            raise Conflict('索引已切换')
        con.execute('UPDATE search_state SET active_slot=?,generation=generation+1,ready=1,watermark_json=?,updated_at=? WHERE id=1',
            (slot,canonical(watermark),now_iso()))
    return {'slot':slot,'batches':len(manifest['batches']),'reports':len(manifest['reports']),
            'legacy_reports':len(manifest['legacy']),'data_version':manifest['data_version']}


def search(path,output,query,*,object_type='message',sort='relevance',limit=20,cursor=None,**filters):
    if object_type=='memory':
        from app.knowledge.memory_search import search as memory_search
        return memory_search(path,query,sort=sort,limit=limit,cursor=cursor,**filters)
    terms,match=query_terms(query)
    if object_type not in {'message','report'} or sort not in {'relevance','time'}:
        raise ValueError('不支持的搜索类型或排序')
    offset=0
    query_hash=digest([query,object_type,sort,filters])
    deadline=time.monotonic()+0.45
    with connect(path) as con:
        con.execute('BEGIN')
        index=state(con)
        if not index['ready']:
            raise KnowledgeUnavailable('搜索索引尚未就绪，请先完成本地索引任务')
        current_version=source_version(con)
        epoch=digest([index['generation'],current_version])
        if cursor:
            try:
                token=json.loads(base64.urlsafe_b64decode(cursor.encode()))
                if token['epoch']!=epoch or token['query']!=query_hash:
                    raise Conflict('索引或筛选已变化，请重新搜索')
                offset=int(token['offset'])
                if offset<0 or offset>100000:
                    raise ValueError('分页超出允许范围，请缩小查询范围')
            except (ValueError,KeyError,TypeError) as exc:
                if isinstance(exc,Conflict):
                    raise
                raise ValueError('分页游标无效') from exc
        con.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
        table=f"fts_{'messages' if object_type=='message' else 'reports'}_{index['active_slot']}"
        if object_type=='message':
            where,args=visible_where(**filters)
            sql=f'''SELECT m.*,{table}.rank AS score FROM {table} JOIN messages m ON m.id={table}.rowid
                WHERE {table} MATCH ? AND {where}'''
            # Let FTS5 consume its rank order instead of sorting every joined hit.
            # Pagination is tied to an immutable index generation/data version.
            order=f'{table}.rank' if sort=='relevance' else 'm.sent_at DESC,m.id DESC'
        else:
            clauses,args=[],[]
            if not filters.get('include_deleted'):
                clauses.append(f'({table}.group_id IS NULL OR EXISTS(SELECT 1 FROM groups g WHERE g.id={table}.group_id AND g.deleted_at IS NULL))')
            if not filters.get('include_orphans'):
                clauses.append(f'{table}.group_id IS NOT NULL')
            for key,operator,value in [('group_id','=',filters.get('group_id')),('period_start','>=',filters.get('start')),('period_start','<',filters.get('end'))]:
                if value is not None:
                    clauses.append(f'{table}.{key}{operator}?');args.append(utc_stamp(value) if key=='period_start' else value)
            if filters.get('sender_id') or filters.get('message_type'):
                raise ValueError('用户和消息类型筛选仅用于原始消息')
            where=' AND '.join(clauses) or '1=1'
            sql=f'SELECT {table}.*,rowid,rank AS score FROM {table} WHERE {table} MATCH ? AND {where}'
            order=f'{table}.rank' if sort=='relevance' else 'period_start DESC,rowid DESC'
        results=[]; examined=0; exhausted=False; stale=False
        while len(results)<limit and examined<1000:
            rows=con.execute(sql+f' ORDER BY {order} LIMIT 100 OFFSET ?', (match,*args,offset)).fetchall()
            if not rows:
                exhausted=True;break
            for row in rows:
                offset+=1;examined+=1
                value=dict(row)
                text=value['content'] if object_type=='message' else value['title']+'\n'+value['body']
                snippet=evidence_snippet(text,terms)
                if snippet is None:
                    continue
                if object_type=='report' and value['ref'].startswith('insight:'):
                    identifier=int(value['ref'].split(':')[1])
                    valid=con.execute('SELECT id FROM report_insights WHERE id=? AND is_current=1 AND input_hash=?',(identifier,value['source_hash'])).fetchone()
                    if not valid:
                        continue
                    value['insight_id']=identifier
                if object_type=='report' and value['ref'].startswith('legacy:'):
                    try:
                        valid=legacy_hash(output,value['locator'])==value['source_hash']
                    except (ValueError,OSError):
                        valid=False
                    if not valid:
                        stale=True
                        continue
                if object_type=='report' and value['ref'].startswith('v1:'):
                    current=database_reports(con,int(value['ref'].split(':')[1]))
                    if not current or current[0]['source_hash']!=value['source_hash']:
                        stale=True
                        continue
                # Full canonical messages/records are fetched only on evidence open.
                keep=('id','group_id','sender_name','sender_id','sent_at','message_type','validation_state') if object_type=='message' else ('ref','group_id','kind','title','locator','source_hash','period_start','insight_id')
                results.append({**{k:value[k] for k in keep if k in value},'type':object_type,'snippet':snippet})
                if len(results)>=limit:
                    break
            if len(rows)<100 and len(results)<limit:
                exhausted=True;break
        next_cursor=None if exhausted else base64.urlsafe_b64encode(canonical({'epoch':epoch,'query':query_hash,'offset':offset}).encode()).decode()
        return {'items':results,'next_cursor':next_cursor,'data_version':current_version,'index_version':index['generation'],
                'warnings':(['索引尚有增量未处理'] if index['watermark'].get('data_version')!=current_version else [])+
                           (['候选仍有未检查项，请继续翻页'] if examined>=1000 else [])+
                           (['部分历史报告已变化，等待重建索引'] if stale else []),'ai_calls':0}


def search_status(path):
    with connect(path) as con:
        index=state(con)
        return {'ready':bool(index['ready']),'index_version':index['generation'],
                'data_version':source_version(con),'indexed_data_version':index['watermark'].get('data_version'),
                'updated_at':index['updated_at']}


def enqueue_index(path,output,rebuild=False):
    from app.knowledge.jobs import enqueue
    manifest=index_manifest(path,output)
    scope={'version':digest([manifest['data_version'],[(d['ref'],d['source_hash']) for d in manifest['legacy']]]),'rebuild':rebuild}
    if rebuild:
        scope['requested_at']=now_iso()
    return enqueue(path,'index',scope,priority=7)


def legacy_report(path,output,ref,expected_hash=None):
    with connect(path) as con:
        index=state(con)
        table=f"fts_reports_{index['active_slot']}"
        row=con.execute(f'SELECT * FROM {table} WHERE ref=?',(ref,)).fetchone()
        if not row or not ref.startswith(('legacy:','v1:')):
            raise KeyError(ref)
        if expected_hash and expected_hash!=row['source_hash']:
            raise Conflict('报告索引版本已变化，请重新搜索')
        current=database_reports(con,int(ref.split(':')[1])) if ref.startswith('v1:') else None
        current_hash=(current[0]['source_hash'] if current else '') if ref.startswith('v1:') else legacy_hash(output,row['locator'])
        if current_hash!=row['source_hash']:
            raise Conflict('原报告已变化，请更新索引')
        return {'title':row['title'],'body':row['body'],'locator':row['locator'] or ref,'source_hash':row['source_hash'],
                'evidence_refs':[],'warnings':['历史内容，尚未建立消息级引用；生图提示词不等于已发布图片正文']}
