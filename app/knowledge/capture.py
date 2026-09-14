"""Optional read-only gap capture, outside the daily generation pipeline."""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.ai.speaker_attribution import build_attribution_contract
from app.data_sources.base import DataSourceStatus
from app.data_sources.wechat_data_analysis import WeChatDataAnalysisSource
from app.knowledge.db import Conflict, canonical, connect, digest
from app.knowledge.ingest import import_snapshot, load_snapshot, safe_path
from app.knowledge.insights import coverage, period_bounds
from app.knowledge.jobs import assert_owner, enqueue, heartbeat
from app.knowledge.service import data_version
from app.providers.history.wechat_data_analysis import WeChatDataAnalysisProvider, _MCP_RANGE_TOOL, _ITEM_LIST_KEYS, _find_list, _extract_message_items, _mcp_timestamp
from app.services.generation_runtime import GenerationBusyError, generation_mutex


class PrimaryBusy(Conflict):
    pass


def require_primary_idle(settings):
    # A brief probe only. The sidecar never holds this lock during source calls.
    try:
        with generation_mutex(timeout_seconds=0.1):
            pass
    except GenerationBusyError as exc:
        raise PrimaryBusy(str(exc)) from exc
    for file in settings.output_dir.glob('*/*/run.json'):
        run = json.loads(file.read_text(encoding='utf-8'))
        execution = run.get('execution') or {}
        if run.get('send_claim_id') or run.get('status') in {'GENERATING','SENDING','IMAGE_GENERATING'} or execution.get('status')=='RUNNING':
            raise PrimaryBusy('主流水线运行中，后台读取延后')


class GuardedProvider(WeChatDataAnalysisProvider):
    def __init__(self, settings):
        super().__init__(settings=settings)
        self.sidecar_settings=settings
        self.raw_items=[]
        self.last_page_explicit=False
        self.rows_valid=True
        self.seen_facts={}
        self.deferred=False

    def _mcp_call(self, method, params, stats, *, timeout=None):
        try:
            require_primary_idle(self.sidecar_settings)
        except PrimaryBusy:
            self.deferred=True
            raise
        result=super()._mcp_call(method,params,stats,timeout=min(timeout or 10,10))
        if method==_MCP_RANGE_TOOL:
            # Only explicit terminal pagination proves coverage; exports/legacy do not.
            value=result.get('hasMore',result.get('has_more'))
            self.last_page_explicit=value is False
            if not isinstance(value,bool):
                self.rows_valid=False
            if _find_list(result,_ITEM_LIST_KEYS) is None:
                self.rows_valid=False
            for row in _extract_message_items(result):
                if not isinstance(row,dict) or _mcp_timestamp(row.get('createTime')) is None:
                    self.rows_valid=False
                    continue
                self.raw_items.append(row)
                identifier=str(row.get('id') or row.get('messageId') or '')
                fact=digest(row)
                if identifier and identifier in self.seen_facts and self.seen_facts[identifier]!=fact:
                    self.rows_valid=False
                if identifier:
                    self.seen_facts[identifier]=fact
        return result


def capture_day(settings, job, source=None):
    scope=json.loads(job['scope_json'])
    if not settings.knowledge_capture_enabled or scope['source_scope']!=settings.knowledge_source_scope:
        raise InterruptedError('独立补齐已关闭或来源范围发生变化')
    require_primary_idle(settings)
    group_id=scope['group_id']
    with connect(settings.db_path) as con:
        group=con.execute('SELECT * FROM groups WHERE id=? AND deleted_at IS NULL AND enabled=1',(group_id,)).fetchone()
        if not group or group['wechat_group_id']!=scope['upstream_group_id']:
            raise Conflict('群归属已变化或已停用')
    selected=date.fromisoformat(scope['day'])
    start=datetime.combine(selected,time())
    end=start+timedelta(days=1)-timedelta(seconds=1)
    checkpoint=json.loads(job['checkpoint_json'])
    if checkpoint.get('captured_locator'):
        folder=safe_path(settings.output_dir,checkpoint['captured_locator']).parent
    else:
        folder=settings.output_dir/'.knowledge'/'captures'/str(job['id'])/job['lease_token']
    locator=(folder/'messages.json').relative_to(settings.output_dir).as_posix()
    if not (folder/'run.json').exists():
        if source is None:
            local_settings=settings.model_copy(update={'wechat_fetch_total_timeout_seconds':30,'wechat_mcp_range_timeout_seconds':10,'wechat_runtime_export_fallback_enabled':False})
            provider=GuardedProvider(local_settings)
            source=WeChatDataAnalysisSource(settings=local_settings,provider=provider)
        else:
            provider=None
        result=source.fetch_messages(scope['upstream_group_id'],start,end)
        if provider and provider.deferred:
            raise PrimaryBusy('主流水线启动，已停止读取下一页')
        if result.status not in {DataSourceStatus.OK,DataSourceStatus.EMPTY_RESULT}:
            raise ValueError('消息补齐失败：'+result.detail[:200])
        rows=[]
        raw_by_id=defaultdict(list)
        if provider:
            for raw in provider.raw_items:
                raw_by_id[str(raw.get('id') or raw.get('messageId') or '')].append(raw)
        for m in result.messages:
            row=m.to_dict()
            upstream=str(m.raw.get('source_message_id') or '')
            row['message_id']=upstream  # Missing IDs must preserve multiplicity.
            row['provenance']={'identity_kind':'upstream' if upstream else 'capture_fallback','source':m.raw}
            if provider and upstream:
                row['provenance']['raw_records']=raw_by_id[upstream]
            rows.append(row)
        complete=bool(provider and provider.last_page_explicit and provider.rows_valid and result.meta.get('read_strategy')!='legacy_anchor')
        meta={'group_id':group_id,'wechat_group_id':scope['upstream_group_id'],'source_scope':scope['source_scope'],
              'period_start':start.isoformat(),'period_end':end.isoformat(),
              'message_snapshot_sha256':build_attribution_contract(rows).message_snapshot_sha256,
              'fetch_metrics':{**result.meta,'coverage_complete':complete}}
        heartbeat(settings.db_path,job,{'capture_received':True})
        folder.mkdir(parents=True,exist_ok=True)
        # run.json is the commit marker. A crash before it allows safe read-only recapture.
        (folder/'messages.json').write_text(canonical(rows),encoding='utf-8')
        if provider:
            (folder/'upstream.json').write_text(canonical(provider.raw_items),encoding='utf-8')
        temporary=folder/'run.tmp'
        temporary.write_text(canonical(meta),encoding='utf-8')
        temporary.replace(folder/'run.json')
    heartbeat(settings.db_path,job,{'captured_locator':locator})
    return import_snapshot(settings.db_path,load_snapshot(settings.output_dir,locator,tz=settings.app_timezone),
                           fence=lambda con:assert_owner(con,job),
                           checkpoint=lambda offset:heartbeat(settings.db_path,job,{'captured_locator':locator,'row_offset':offset}))


def schedule_knowledge(settings, now=None):
    now=now or datetime.now(ZoneInfo(settings.app_timezone))
    allowed={int(v.strip()) for v in settings.knowledge_group_ids.split(',') if v.strip()}
    if not allowed:
        return
    with connect(settings.db_path) as con:
        # This is a separate additive migration, so old Phase 0 installations stay usable.
        if not con.execute("SELECT 1 FROM sqlite_master WHERE name='report_insights'").fetchone():
            return
        groups=[dict(g) for g in con.execute('SELECT id,wechat_group_id FROM groups WHERE enabled=1 AND deleted_at IS NULL') if g['id'] in allowed]
        version=data_version(con)
        from app.knowledge.insight_content import version as content_version
        content_versions={g['id']:content_version(con,g['id']) for g in groups}
    for group in groups:
        if settings.knowledge_capture_enabled and settings.knowledge_source_scope and now.time()>=time(9,15):
            for offset in range(1,8):
                day=now.date()-timedelta(days=offset)
                a=datetime.combine(day,time(),now.tzinfo).isoformat()
                b=datetime.combine(day+timedelta(days=1),time(),now.tzinfo).isoformat()
                from app.knowledge.ingest import utc_stamp
                with connect(settings.db_path) as con:
                    missing=not coverage(con,group['id'],utc_stamp(a),utc_stamp(b))['complete']
                if missing:
                    enqueue(settings.db_path,'capture',{'group_id':group['id'],'upstream_group_id':group['wechat_group_id'],
                        'day':day.isoformat(),'source_scope':settings.knowledge_source_scope,'check_date':now.date().isoformat()},group_id=group['id'],priority=3)
        if now.time()>=time(10):
            day=(now.date()-timedelta(days=now.weekday()+1)).isoformat()
            enqueue(settings.db_path,'insight',{'group_id':group['id'],'kind':'weekly','day':day,'data_version':version,
                    'content_version':content_versions[group['id']]},group_id=group['id'],priority=5)
