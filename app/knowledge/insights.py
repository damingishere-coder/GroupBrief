"""Versioned deterministic period statistics; never invokes an AI or sender."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app.data_sources.base import V2Message
from app.knowledge.db import Conflict, KnowledgeUnavailable, canonical, connect, digest, now_iso, transaction
from app.knowledge.service import VISIBLE, data_version
from app.ranking.engine import RankingEngine
from app.services.speaker_identity import speaker_identity_key

METRICS_VERSION = 'period-1'
MIGRATION_ID = 'knowledge_002_insights'
DDL = [
    '''CREATE TABLE report_insights (
      id INTEGER PRIMARY KEY, group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE RESTRICT,
      kind TEXT NOT NULL CHECK(kind IN ('weekly','monthly')), period_start TEXT NOT NULL, period_end TEXT NOT NULL,
      revision INTEGER NOT NULL, is_current INTEGER NOT NULL CHECK(is_current IN (0,1)),
      metrics_version TEXT NOT NULL, input_hash TEXT NOT NULL, input_manifest_json TEXT NOT NULL,
      coverage_json TEXT NOT NULL, metrics_json TEXT NOT NULL, sections_json TEXT NOT NULL DEFAULT '[]',
      status TEXT NOT NULL, ai_status TEXT NOT NULL DEFAULT 'DISABLED',
      created_at TEXT NOT NULL, UNIQUE(group_id,kind,period_start,revision),
      UNIQUE(group_id,kind,period_start,input_hash), CHECK(period_start<period_end), UNIQUE(id,group_id))''',
    'CREATE UNIQUE INDEX uq_insight_current ON report_insights(group_id,kind,period_start) WHERE is_current=1',
    'CREATE INDEX ix_insight_period ON report_insights(group_id,kind,period_start)',
    'CREATE UNIQUE INDEX uq_messages_id_group ON messages(id,group_id)',
    '''CREATE TABLE report_evidence (
      report_id INTEGER NOT NULL, group_id INTEGER NOT NULL, section_key TEXT NOT NULL, claim_key TEXT NOT NULL,
      message_id INTEGER NOT NULL,
      PRIMARY KEY(report_id,section_key,claim_key,message_id),
      FOREIGN KEY(report_id,group_id) REFERENCES report_insights(id,group_id) ON DELETE RESTRICT,
      FOREIGN KEY(message_id,group_id) REFERENCES messages(id,group_id) ON DELETE RESTRICT)''',
    'CREATE INDEX ix_report_evidence_message ON report_evidence(message_id,report_id)',
]
CHECKSUM = digest(DDL)


def install_schema(path):
    with connect(path, write=True) as con, transaction(con):
        row = con.execute('SELECT checksum FROM schema_migrations WHERE migration_id=?', (MIGRATION_ID,)).fetchone()
        if row:
            if row[0] != CHECKSUM:
                raise KnowledgeUnavailable('周期洞察 Schema 不兼容')
            return False
        for sql in DDL:
            con.execute(sql)
        con.execute('INSERT INTO schema_migrations VALUES(?,?,?)', (MIGRATION_ID, now_iso(), CHECKSUM))
        return True


def period_bounds(kind, day, tz='Asia/Shanghai'):
    selected = date.fromisoformat(day)
    if kind == 'weekly':
        start = selected-timedelta(days=selected.weekday())
        end = start+timedelta(days=7)
        previous = start-timedelta(days=7)
    elif kind == 'monthly':
        start = selected.replace(day=1)
        end = (start.replace(day=28)+timedelta(days=4)).replace(day=1)
        previous = (start-timedelta(days=1)).replace(day=1)
    else:
        raise ValueError('不支持的周期类型')
    def stamp(d):
        return datetime.combine(d, time(), ZoneInfo(tz)).astimezone(timezone.utc).isoformat(timespec='microseconds')
    return stamp(start), stamp(end), stamp(previous)


def coverage(con, group_id, start, end):
    batches = [dict(r) for r in con.execute('''SELECT id,range_start,range_end,coverage_state,source_scope
        FROM source_batches WHERE group_id=? AND import_status='complete'
        AND range_start<? AND range_end>? ORDER BY range_start,range_end''', (group_id, end, start))]
    intervals = [(max(start, b['range_start']), min(end, b['range_end'])) for b in batches
                 if b['coverage_state'] in {'complete','complete_empty'}]
    position, gaps = start, []
    for a, b in sorted(intervals):
        if a > position:
            gaps.append({'start': position, 'end': a})
        position = max(position, b)
    if position < end:
        gaps.append({'start': position, 'end': end})
    invalid = con.execute(f"SELECT count(*) FROM messages m WHERE {VISIBLE} AND group_id=? AND sent_at>=? AND sent_at<? AND validation_state!='valid'", (group_id,start,end)).fetchone()[0]
    scopes = {b['source_scope'] for b in batches}
    return {'complete': not gaps and not invalid and len(scopes)==1,
            'gaps': gaps, 'invalid_messages': invalid, 'source_scopes': sorted(scopes),
            'batch_ids': [b['id'] for b in batches], 'state': 'complete' if not gaps and not invalid and len(scopes)==1 else 'partial'}


def compute(rows, start, end, policy, tz='Asia/Shanghai'):
    values = [V2Message(str(r['id']), str(r['group_id']), '',
                       (r['source_scope']+':'+r['sender_id']) if r['sender_id'] else '',
                       r['sender_name'], datetime.fromisoformat(r['sent_at']), r['message_type'], r['content']) for r in rows]
    engine = RankingEngine()
    ranking = engine.compute(values, '', start, end, top_limit=max(1,len(rows)), count_policy=policy).to_dict()
    countable = [(r,m) for r,m in zip(rows,values) if engine._countable(m)]
    days, speaker_days = Counter(), defaultdict(set)
    unknown = 0
    known_ids = set()
    for row, m in countable:
        day = m.timestamp.astimezone(ZoneInfo(tz)).date().isoformat()
        days[day] += 1
        identity = speaker_identity_key(m.sender_id,m.sender_name)
        if m.sender_id:
            known_ids.add(m.sender_id)
        else:
            unknown += 1
        if identity:
            import hashlib
            key = hashlib.sha256(f'{identity[0]}:{identity[1]}'.encode()).hexdigest()[:16]
            speaker_days[key].add(day)
    start_day = datetime.fromisoformat(start).astimezone(ZoneInfo(tz)).date()
    end_day = datetime.fromisoformat(end).astimezone(ZoneInfo(tz)).date()
    for offset in range((end_day-start_day).days):
        days.setdefault((start_day+timedelta(days=offset)).isoformat(), 0)
    members = ranking.pop('top_speakers')
    for member in members:
        member['active_days'] = len(speaker_days[member['identity_key']])
    return {**ranking, 'members': members, 'daily_counts': [{'date': d, 'count': days[d]} for d in sorted(days)],
            'uncertain_identity_messages': unknown, 'active_people_min': len(known_ids),
            'active_people_max': len(known_ids)+unknown}


def change(current, previous, comparable):
    if not comparable:
        return {'current': current, 'previous': previous, 'delta': None, 'percent': None, 'state': 'not_comparable'}
    return {'current': current, 'previous': previous, 'delta': current-previous,
            'percent': round((current-previous)/previous*100, 2) if previous else None,
            'state': 'zero_baseline' if not previous else 'comparable'}


def build(path, group_id, kind, day, tz='Asia/Shanghai', fence=None):
    start, end, previous = period_bounds(kind, day, tz)
    if end > datetime.now(timezone.utc).isoformat(timespec='microseconds'):
        raise ValueError('仅生成已结束的完整自然周期')
    with connect(path) as con:
        con.execute('BEGIN')
        group = con.execute('SELECT * FROM groups WHERE id=? AND deleted_at IS NULL', (group_id,)).fetchone()
        if not group:
            raise KeyError(group_id)
        policy = group['ranking_count_policy'] if 'ranking_count_policy' in group.keys() else 'all_messages'
        version = data_version(con)
        rows = [dict(r) for r in con.execute(f'SELECT m.* FROM messages m WHERE {VISIBLE} AND group_id=? AND sent_at>=? AND sent_at<? ORDER BY sent_at,id', (group_id,previous,end))]
        current_rows = [r for r in rows if r['sent_at']>=start]
        previous_rows = [r for r in rows if r['sent_at']<start]
        coverage_now, coverage_before = coverage(con,group_id,start,end), coverage(con,group_id,previous,start)
        from app.knowledge.insight_content import collect
        content=collect(con,group_id,start,end,previous,tz)
    current = compute(current_rows,start,end,policy,tz)
    before = compute(previous_rows,previous,start,policy,tz)
    comparable = coverage_now['complete'] and coverage_before['complete'] and coverage_now['source_scopes']==coverage_before['source_scopes']
    current['comparison'] = {k: change(current[k],before[k],comparable and (k!='speaker_count' or not (current['uncertain_identity_messages'] or before['uncertain_identity_messages']))) for k in ('message_count','speaker_count')}
    previous_members = {m['identity_key']: m for m in before['members']}
    current_keys = {m['identity_key'] for m in current['members']}
    for member in current['members']:
        old = previous_members.get(member['identity_key'])
        # Name-only identities cannot establish a cross-period personal identity.
        identity_safe = not (current['uncertain_identity_messages'] or before['uncertain_identity_messages'])
        valid = comparable and identity_safe
        member.update(previous_rank=old['rank'] if old and valid else None,
                      rank_change=old['rank']-member['rank'] if old and valid else None,
                      count_change=member['count']-(old['count'] if old else 0) if valid else None,
                      active_days_change=member['active_days']-(old['active_days'] if old else 0) if valid else None,
                      newly_active=valid and old is None,
                      new_to_top=valid and member['rank']<=15 and (old is None or old['rank']>15))
    current['inactive_previous_members'] = [m for m in before['members'] if m['identity_key'] not in current_keys] if comparable and not (current['uncertain_identity_messages'] or before['uncertain_identity_messages']) else []
    current['champion'] = current['members'][0] if current['members'] and coverage_now['complete'] and not current['uncertain_identity_messages'] else None
    # A zero on a partial day is unknown, rather than an observed empty day.
    for day_count in current['daily_counts']:
        a = datetime.combine(date.fromisoformat(day_count['date']),time(),ZoneInfo(tz)).astimezone(timezone.utc)
        with connect(path) as con:
            day_count['complete'] = coverage(con,group_id,a.isoformat(timespec='microseconds'),(a+timedelta(days=1)).isoformat(timespec='microseconds'))['complete']
        if not day_count['complete'] and day_count['count']==0:
            day_count['count'] = None
    manifest = {'metrics_version': METRICS_VERSION, 'policy': policy, 'timezone': tz,
                'period': [start,end,previous], 'messages': [[r['id'],r['fact_sha256'],r['validation_state']] for r in rows],
                'coverage': {'current': coverage_now,'previous': coverage_before}}
    if content['version']:
        manifest['content']={'version':content['version'],'entries':content['manifest'],'sections':content['sections']}
    input_hash = digest(manifest)
    with connect(path, write=True) as con, transaction(con):
        if fence:
            fence(con)
        if data_version(con) != version:
            raise Conflict('统计期间消息发生变化，请重新计算')
        from app.knowledge.insight_content import version as content_version
        if content_version(con,group_id)!=content['version']:
            raise Conflict('记忆或故事线已变化，请重新计算')
        if not con.execute('SELECT 1 FROM groups WHERE id=? AND deleted_at IS NULL', (group_id,)).fetchone():
            raise Conflict('群已归档')
        old = con.execute('SELECT id FROM report_insights WHERE group_id=? AND kind=? AND period_start=? AND input_hash=?', (group_id,kind,start,input_hash)).fetchone()
        if old:
            return {'id':old['id'],'reused':True}
        revision = con.execute('SELECT coalesce(max(revision),0)+1 FROM report_insights WHERE group_id=? AND kind=? AND period_start=?',(group_id,kind,start)).fetchone()[0]
        con.execute('UPDATE report_insights SET is_current=0 WHERE group_id=? AND kind=? AND period_start=?',(group_id,kind,start))
        cursor = con.execute('''INSERT INTO report_insights(group_id,kind,period_start,period_end,revision,is_current,
            metrics_version,input_hash,input_manifest_json,coverage_json,metrics_json,status,created_at)
            VALUES(?,?,?,?,?,1,?,?,?,?,?,?,?)''', (group_id,kind,start,end,revision,METRICS_VERSION,input_hash,canonical(manifest),canonical(manifest['coverage']),canonical(current),'READY' if coverage_now['complete'] else 'PARTIAL',now_iso()))
        report_id=cursor.lastrowid
        if content['version']:
            con.execute('UPDATE report_insights SET sections_json=? WHERE id=?',(canonical(content['sections']),report_id))
            for ev in content['evidence']:
                con.execute('''INSERT INTO report_evidence(report_id,group_id,section_key,claim_key,message_id,memory_entry_id)
                    VALUES(?,?,?,?,?,?)''',(report_id,group_id,ev['section_key'],ev['claim_key'],ev['message_id'],ev['memory_entry_id']))
        return {'id':report_id,'revision':revision,'reused':False}


def list_reports(path,group_id=None,kind=None,include_history=False,limit=30):
    clauses,args = ['g.deleted_at IS NULL'],[]
    if not include_history:
        clauses.append('r.is_current=1')
    for key,value in [('group_id',group_id),('kind',kind)]:
        if value is not None:
            clauses.append(f'r.{key}=?');args.append(value)
    with connect(path) as con:
        rows=con.execute(f'''SELECT r.id,r.group_id,r.kind,r.period_start,r.period_end,r.revision,r.status,r.ai_status,r.created_at
            FROM report_insights r JOIN groups g ON g.id=r.group_id WHERE {' AND '.join(clauses)}
            ORDER BY r.period_start DESC,r.id DESC LIMIT ?''',(*args,limit)).fetchall()
        return {'items':[dict(r) for r in rows]}


def detail(path,report_id):
    with connect(path) as con:
        row=con.execute('SELECT * FROM report_insights WHERE id=?',(report_id,)).fetchone()
        if not row:
            raise KeyError(report_id)
        result=dict(row)
        for key in ('metrics','coverage','sections','input_manifest'):
            result[key]=json.loads(result.pop(key+'_json'))
        manifest=result['input_manifest']
        manifest['message_count']=len(manifest['messages'])
        manifest['message_set_hash']=digest(manifest.pop('messages'))
        result['evidence_refs']=[dict(r) for r in con.execute('SELECT * FROM report_evidence WHERE report_id=? ORDER BY section_key,claim_key,message_id',(report_id,))]
        manifest.pop('content',None)
        return result


def report_messages(path, report_id, offset=0, limit=20):
    with connect(path) as con:
        report=con.execute('SELECT * FROM report_insights WHERE id=?',(report_id,)).fetchone()
        if not report:
            raise KeyError(report_id)
        manifest=json.loads(report['input_manifest_json'])
        ids=[r[0] for r in manifest['messages']]
        rows=[dict(r) for r in con.execute('''SELECT m.* FROM messages m JOIN json_each(?) j ON m.id=j.value
            WHERE m.sent_at>=? AND m.sent_at<? ORDER BY m.sent_at,m.id LIMIT ? OFFSET ?''',
            (canonical(ids),report['period_start'],report['period_end'],limit+1,offset))]
        return {'items':rows[:limit],'next_offset':offset+limit if len(rows)>limit else None}
