"""Frozen, evidence-backed period content; topic counts describe identified evidence."""
import json
from collections import defaultdict

from app.knowledge.db import canonical,digest


def version(con,group_id):
    if not con.execute("SELECT 1 FROM sqlite_master WHERE name='storylines'").fetchone():return ''
    return digest([[tuple(r) for r in con.execute('SELECT id,version FROM memories WHERE group_id=? ORDER BY id',(group_id,))],
                   [tuple(r) for r in con.execute('SELECT id,version FROM storylines WHERE group_id=? ORDER BY id',(group_id,))]])


def collect(con,group_id,start,end,previous,tz):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    content_version=version(con,group_id)
    if not content_version:return {'version':'','sections':[],'evidence':[],'manifest':[]}
    rows=[dict(r) for r in con.execute("""SELECT e.*,m.type,m.title,m.status memory_status FROM memory_entries e JOIN memories m ON m.id=e.memory_id
        WHERE e.group_id=? AND e.status='active' AND m.status!='archived' AND e.observed_end>=? AND e.observed_end<?
        ORDER BY e.observed_start,e.id""",(group_id,previous,end))]
    memories={r['id']:dict(r) for r in con.execute('SELECT * FROM memories WHERE group_id=?',(group_id,))}
    for entry in rows:
        root=memories[entry['memory_id']]
        while root['merged_into_id']:
            root=memories[root['merged_into_id']]
        entry['memory_id']=root['id'];entry['title']=root['title'];entry['memory_status']=root['status']
    rows=[entry for entry in rows if entry['memory_status']!='archived']
    current=[e for e in rows if e['observed_end']>=start]
    sections=[];evidence=[];manifest=[];topics=defaultdict(list)
    for e in current:
        sources=[dict(r) for r in con.execute('''SELECT s.*,m.sent_at,m.sender_id,m.validation_state,m.fact_sha256 FROM memory_sources s
            JOIN messages m ON m.id=s.message_id WHERE s.entry_id=? ORDER BY s.claim_key,s.message_id''',(e['id'],))]
        if not sources or any(s['validation_state']!='valid' for s in sources):continue
        # Only date-local evidence contributes to current period counts and excerpts.
        active=[s for s in sources if start<=s['sent_at']<end]
        if not active:continue
        section_key=f'entry:{e["id"]}'
        claims=json.loads(e['claims_json'])
        item={'key':section_key,'kind':e['type'],'title':e['title'],'summary':e['summary'],
              'memory_id':e['memory_id'],'memory_entry_id':e['id'],'observed_start':e['observed_start'],'review_status':e['memory_status'],
              'claims':[{'key':c['key'],'text':c['text'],'message_ids':sorted({s['message_id'] for s in sources if s['claim_key']==c['key']})} for c in claims]}
        sections.append(item)
        for s in sources:evidence.append({'section_key':section_key,'claim_key':s['claim_key'],'message_id':s['message_id'],'memory_entry_id':e['id']})
        manifest.append([e['id'],e['entry_key'],[[s['message_id'],s['fact_sha256']] for s in sources]])
        topics[e['memory_id']].append((e,active))
    topic_items=[]
    for mid,items in topics.items():
        all_sources=[s for _,sources in items for s in sources]
        first=memories[mid]['first_source_at']
        before=any(e['memory_id']==mid and e['observed_start']<start for e in rows)
        topic_items.append({'memory_id':mid,'title':items[0][0]['title'],'type':items[0][0]['type'],
                            'discussion_days':len({datetime.fromisoformat(s['sent_at']).astimezone(ZoneInfo(tz)).date() for s in all_sources}),
                            'participants':len({s['sender_id'] for s in all_sources if s['sender_id']}),
                            'evidence_messages':len({s['message_id'] for s in all_sources}),
                            'entry_count':len(items),'lifecycle':'newly_observed' if first>=start else 'continuing' if before else 'reactivated'})
    topic_items.sort(key=lambda t:(-t['discussion_days'],-t['evidence_messages'],t['memory_id']))
    sections.insert(0,{'key':'topics','kind':'topic_trends','title':'基于已识别讨论的话题变化','items':topic_items,
                       'note':'证据消息量不是全群话题消息总量；新出现表示在当前已索引历史中首次观察到。'})
    ids=[e['id'] for e in current if any(m[0]==e['id'] for m in manifest)]
    for story in con.execute('SELECT * FROM storylines WHERE group_id=? AND status=?',(group_id,'active')):
        linked=[r[0] for r in con.execute('SELECT memory_entry_id FROM storyline_entries WHERE storyline_id=?',(story['id'],))]
        updates=sorted(set(linked)&set(ids))
        if updates:
            sections.append({'key':f'storyline:{story["id"]}','kind':'storyline','title':story['title'],
                             'storyline_id':story['id'],'entry_ids':updates,'summary':'\n'.join(e['summary'] for e in current if e['id'] in updates)})
    return {'version':content_version,'sections':sections,'evidence':evidence,'manifest':manifest}
