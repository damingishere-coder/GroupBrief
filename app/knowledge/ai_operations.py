"""Paid-call ledger. Uncertain submissions cannot be retried by job recovery."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.knowledge.db import Conflict, canonical, connect, digest, now_iso, transaction
from app.knowledge.jobs import assert_owner
from app.providers.ai.base import ExternalCallNotSubmittedError, ExternalCallResultUnknownError


class WaitBudget(RuntimeError):
    pass


class HoldUnknown(RuntimeError):
    pass


def write_response(file: Path,text: str):
    file.parent.mkdir(parents=True,exist_ok=True)
    temp=file.with_suffix('.tmp')
    with temp.open('w',encoding='utf-8',newline='') as stream:
        stream.write(text);stream.flush();os.fsync(stream.fileno())
    os.replace(temp,file)


def call(settings,job,messages,*,purpose='memory',invoke=None):
    from app.knowledge.capture import require_primary_idle
    from app.providers.ai.codex import CodexGPTProvider
    from app.knowledge.memory import Extraction
    path=settings.db_path
    if not settings.knowledge_memory_ai_enabled:
        raise WaitBudget('记忆补充 AI 未启用')
    require_primary_idle(settings)
    # Conservative UTF-8 byte estimate, explicitly not provider-reported tokens.
    input_estimate=len(canonical(messages).encode('utf-8'))
    output_limit=min(1000,settings.knowledge_memory_output_budget)
    day=datetime.now(ZoneInfo(settings.app_timezone)).date().isoformat()
    scope=json.loads(job['scope_json'])
    if scope.get('history'):
        clock=datetime.now(ZoneInfo(settings.app_timezone)).strftime('%H:%M')
        if not '11:30'<=clock<'17:30':
            raise WaitBudget('历史 AI 仅在 11:30—17:30 空闲窗口运行')
    model=settings.codex_summary_model
    key=digest([job['id'],purpose,messages,'memory-1',model])
    with connect(path,write=True) as con,transaction(con):
        assert_owner(con,job)
        old=con.execute('SELECT * FROM ai_operations WHERE operation_key=? ORDER BY attempt_no DESC LIMIT 1',(key,)).fetchone()
        if old and old['status'] in {'HOLD_UNKNOWN','SUBMITTING','ABANDONED'}:
            raise HoldUnknown('调用是否完成未知，禁止自动重提')
        if old and old['status'] in {'VALIDATED','RECEIVED','INVALID'}:
            operation=dict(old)
        else:
            if scope.get('history'):
                first=con.execute("""SELECT o.group_id,json_extract(j.scope_json,'$.source_day') source_day FROM ai_operations o
                   JOIN knowledge_jobs j ON j.id=o.job_id WHERE o.budget_day=? AND o.status!='NOT_SUBMITTED'
                   AND json_extract(j.scope_json,'$.history')=1 ORDER BY o.id LIMIT 1""",(day,)).fetchone()
                if first and (first['group_id'],first['source_day'])!=(job['group_id'],scope.get('source_day')):
                    raise WaitBudget('历史 AI 每天仅推进一个群的一天，其他范围继续排队')
            sums=con.execute("SELECT count(*),coalesce(sum(budget_input),0),coalesce(sum(budget_output),0) FROM ai_operations WHERE group_id=? AND budget_day=? AND status!='NOT_SUBMITTED'",(job['group_id'],day)).fetchone()
            if sums[0]>=settings.knowledge_memory_call_budget or sums[1]+input_estimate>settings.knowledge_memory_input_budget or sums[2]+output_limit>settings.knowledge_memory_output_budget:
                raise WaitBudget('本群今日补充调用预算不足，任务保留待处理')
            attempt=old['attempt_no']+1 if old else 1
            if attempt>3:
                raise ValueError('明确未提交重试已达上限')
            cur=con.execute('''INSERT INTO ai_operations(job_id,group_id,operation_key,attempt_no,input_hash,prompt_version,schema_version,
               provider,model,status,budget_day,budget_input,budget_output,created_at,updated_at)
               VALUES(?,?,?,?,?,'memory-1','memory-1','codex_gpt',?,'SUBMITTING',?,?,?,?,?)''',
               (job['id'],job['group_id'],key,attempt,digest(messages),model,day,input_estimate,output_limit,now_iso(),now_iso()))
            operation=dict(con.execute('SELECT * FROM ai_operations WHERE id=?',(cur.lastrowid,)).fetchone())
        # Extend only this fenced job through the bounded external call.
        until=(datetime.now(ZoneInfo('UTC'))+timedelta(seconds=max(120,settings.codex_summary_timeout_seconds+60))).isoformat(timespec='microseconds')
        con.execute('UPDATE knowledge_jobs SET lease_until=? WHERE id=?',(until,job['id']))
    file=settings.output_dir/'.knowledge'/'operations'/str(operation['id'])/'response.txt'
    if operation['status']=='VALIDATED':
        return json.loads(operation['validated_result_json'])
    if operation['status'] in {'RECEIVED','INVALID'}:
        if not file.is_file() or digest(file.read_bytes())!=operation['response_hash']:
            raise HoldUnknown('已调用但原始响应丢失或变化，禁止自动重提')
        text=file.read_text(encoding='utf-8')
    else:
        try:
            if invoke is None:
                # Same existing OpenAI login and model. No fallback/provider switch.
                if settings.summary_provider_primary not in {'codex','codex_gpt','gpt'}:
                    raise ExternalCallNotSubmittedError('记忆 AI 当前仅支持现有 Codex 主 Provider；不自动切换配置')
                provider=CodexGPTProvider(settings.model_copy(update={'summary_provider_fallback':'none'}))
                text=provider._codex_chat(messages,response_format='text')
            else:
                text=invoke(messages)
            write_response(file,text)
            write_response(file.with_name('receipt.json'),canonical({'operation_key':key,'input_hash':operation['input_hash'],
                           'response_hash':digest(text.encode('utf-8'))}))
        except ExternalCallNotSubmittedError:
            with connect(path,write=True) as con,transaction(con):
                assert_owner(con,job)
                con.execute("UPDATE ai_operations SET status='NOT_SUBMITTED',updated_at=? WHERE id=?",(now_iso(),operation['id']))
            raise
        except Exception as exc:
            with connect(path,write=True) as con,transaction(con):
                assert_owner(con,job)
                con.execute("UPDATE ai_operations SET status='HOLD_UNKNOWN',updated_at=? WHERE id=?",(now_iso(),operation['id']))
            raise HoldUnknown('调用结果未知或响应未持久化，已保留预算占用') from exc
        with connect(path,write=True) as con,transaction(con):
            assert_owner(con,job)
            con.execute("UPDATE ai_operations SET status='RECEIVED',response_path=?,response_hash=?,usage_json=?,updated_at=? WHERE id=?",
                        (file.relative_to(settings.output_dir).as_posix(),digest(text.encode('utf-8')),
                         canonical({'input_estimate':input_estimate,'output_estimate':len(text.encode('utf-8')),'basis':'utf8_bytes_upper_estimate','actual_tokens':None}),now_iso(),operation['id']))
            con.execute('UPDATE ai_operations SET budget_output=max(budget_output,?) WHERE id=?',(len(text.encode('utf-8')),operation['id']))
    try:
        from app.providers.ai.codex import _strip_json_fence
        value=Extraction.model_validate_json(_strip_json_fence(text)).model_dump()
    except ValueError:
        with connect(path,write=True) as con,transaction(con):
            assert_owner(con,job)
            con.execute("UPDATE ai_operations SET status='INVALID',updated_at=? WHERE id=?",(now_iso(),operation['id']))
        raise ValueError('AI JSON 无效，原响应已保存；重跑仅重新解析，不再次付费')
    with connect(path,write=True) as con,transaction(con):
        assert_owner(con,job)
        con.execute("UPDATE ai_operations SET status='VALIDATED',validated_result_json=?,updated_at=? WHERE id=?",(canonical(value),now_iso(),operation['id']))
    return value


def recover(con):
    now=now_iso()
    rows=con.execute("SELECT id FROM knowledge_jobs WHERE job_kind='memory' AND status='RUNNING' AND lease_until<?",(now,)).fetchall()
    for row in rows:
        unknown=con.execute("SELECT 1 FROM ai_operations WHERE job_id=? AND status IN ('SUBMITTING','HOLD_UNKNOWN')",(row['id'],)).fetchone()
        con.execute("UPDATE ai_operations SET status='HOLD_UNKNOWN',updated_at=? WHERE job_id=? AND status='SUBMITTING'",(now,row['id']))
        con.execute("UPDATE knowledge_jobs SET status=?,lease_owner='',lease_token='',lease_until='',updated_at=? WHERE id=?",('HOLD_UNKNOWN' if unknown else 'WAIT_RETRY',now,row['id']))


def recover_artifacts(settings):
    """A complete local response receipt can close the response/DB crash window."""
    with connect(settings.db_path) as con:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE name='ai_operations'").fetchone():return
        rows=[dict(r) for r in con.execute("""SELECT o.* FROM ai_operations o JOIN knowledge_jobs j ON j.id=o.job_id
            WHERE o.status IN ('SUBMITTING','HOLD_UNKNOWN') AND
            (j.status='HOLD_UNKNOWN' OR (j.status='RUNNING' AND j.lease_until<?))""",(now_iso(),))]
    for op in rows:
        file=settings.output_dir/'.knowledge'/'operations'/str(op['id'])/'response.txt'
        try:
            receipt=json.loads(file.with_name('receipt.json').read_text(encoding='utf-8'))
            raw=file.read_bytes();sha=digest(raw)
            if receipt!={'operation_key':op['operation_key'],'input_hash':op['input_hash'],'response_hash':sha}:continue
        except (OSError,ValueError):continue
        with connect(settings.db_path,write=True) as con,transaction(con):
            job=con.execute('SELECT * FROM knowledge_jobs WHERE id=?',(op['job_id'],)).fetchone()
            current=con.execute('SELECT status FROM ai_operations WHERE id=?',(op['id'],)).fetchone()
            if current[0] not in {'SUBMITTING','HOLD_UNKNOWN'} or not(job['status']=='HOLD_UNKNOWN' or (job['status']=='RUNNING' and job['lease_until']<now_iso())):continue
            con.execute("UPDATE ai_operations SET status='RECEIVED',response_path=?,response_hash=?,budget_output=max(budget_output,?),updated_at=? WHERE id=?",
                        (file.relative_to(settings.output_dir).as_posix(),sha,len(raw),now_iso(),op['id']))
            con.execute("UPDATE knowledge_jobs SET status='WAIT_RETRY',lease_owner='',lease_token='',lease_until='',error_code='',updated_at=? WHERE id=?",(now_iso(),op['job_id']))


def resolve_unknown(path,job_id,operation_id,resolution,note):
    if resolution not in {'confirmed_not_submitted','abandon'} or len(note.strip())<5:
        raise ValueError('需要明确核对结论和依据；不能凭等待时间认定未提交')
    with connect(path,write=True) as con,transaction(con):
        job=con.execute("SELECT * FROM knowledge_jobs WHERE id=? AND status='HOLD_UNKNOWN'",(job_id,)).fetchone()
        op=con.execute("SELECT * FROM ai_operations WHERE id=? AND job_id=? AND status='HOLD_UNKNOWN'",(operation_id,job_id)).fetchone()
        if not job or not op:
            raise Conflict('未知调用状态已变化')
        con.execute('UPDATE ai_operations SET status=?,usage_json=?,updated_at=? WHERE id=?',
                    ('NOT_SUBMITTED' if resolution=='confirmed_not_submitted' else 'ABANDONED',canonical({'resolution':resolution,'note':note,'actor':'local_user'}),now_iso(),operation_id))
        con.execute('UPDATE knowledge_jobs SET status=?,updated_at=? WHERE id=?',('PAUSED' if resolution=='confirmed_not_submitted' else 'FAILED',now_iso(),job_id))
        return {'job_id':job_id,'operation_id':operation_id,'resolution':resolution}


def status(settings):
    day=datetime.now(ZoneInfo(settings.app_timezone)).date().isoformat()
    with connect(settings.db_path) as con:
        rows=[dict(r) for r in con.execute('SELECT group_id,count(*) calls,sum(budget_input) reserved_input,sum(budget_output) reserved_output FROM ai_operations WHERE budget_day=? AND status!=? GROUP BY group_id',(day,'NOT_SUBMITTED'))]
        operations=[dict(r) for r in con.execute('SELECT id,job_id,group_id,status,provider,model,budget_day,response_hash,usage_json FROM ai_operations ORDER BY id DESC LIMIT 50')]
        return {'enabled':settings.knowledge_memory_enabled,'ai_enabled':settings.knowledge_memory_ai_enabled,
                'limits':{'calls':settings.knowledge_memory_call_budget,'input':settings.knowledge_memory_input_budget,'output':settings.knowledge_memory_output_budget},
                'day':day,'groups':rows,'operations':operations,'actual_cost':None}
