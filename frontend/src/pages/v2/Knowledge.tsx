import { useEffect, useState } from 'react';
import { knowledgeApi, type BackfillPreview, type KnowledgeJob, type KnowledgeStatus, type MessagePage } from '../../knowledgeApi';
import EvidenceDrawer from '../../components/EvidenceDrawer';
import { updateWorkspaceQuery, useWorkspaceQuery } from '../../navigation';
import '../../knowledge.css';

export default function Knowledge() {
  const query = useWorkspaceQuery();
  const [status, setStatus] = useState<KnowledgeStatus>();
  const [jobs, setJobs] = useState<KnowledgeJob[]>([]);
  const [messages, setMessages] = useState<MessagePage>();
  const [preview, setPreview] = useState<BackfillPreview>();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const group = query.get('group') || '';
  const messageId = Number(query.get('message'));
  useEffect(() => {
    let active = true;
    setMessages(undefined); setPreview(undefined); setError('');
    void knowledgeApi.status().then(async s => {
      if (!active) return;
      setStatus(s);
      if (!s.available) return;
      const params = new URLSearchParams();
      if (group) params.set('group_id', group);
      const results = await Promise.allSettled([knowledgeApi.jobs(), knowledgeApi.messages(params)]);
      if (!active) return;
      if (results[0].status === 'fulfilled') setJobs(results[0].value.items); else setError(String(results[0].reason));
      if (results[1].status === 'fulfilled') setMessages(results[1].value); else setError(String(results[1].reason));
    }).catch(e => { if (active) setError(String(e)); });
    return () => { active = false; };
  }, [group, refresh]);
  async function action(fn: () => Promise<unknown>) {
    setBusy(true); setError('');
    try { await fn(); } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  return <div className="knowledge-page">
    <header><div><h1>知识任务与消息来源</h1><p>查看跨日期消息、导入进度和原始证据。</p></div>
      <button disabled={busy} onClick={() => setRefresh(n => n + 1)}>刷新</button></header>
    {error && <p role="alert">{error}</p>}
    {!status && !error && <p>正在检查知识库…</p>}
    {status && !status.available && <section><h2>知识库尚未就绪</h2><p>{status.reason}</p><p>完成扩展数据库迁移后可使用此页面。现有日报和消息归档仍可使用。</p></section>}
    {status?.available && <>
      <section><h2>数据概况</h2><p>{status.message_count} 条消息 · {status.source_count} 份来源归档 · 后台导入{status.worker_enabled ? '已启用' : '未启用'}</p>
        <p>覆盖情况：{Object.entries(status.coverage || {}).map(([k, v]) => `${k} ${v}`).join(' / ') || '尚未导入'}</p>
        <label>群 ID 筛选 <input aria-label="群 ID 筛选" type="number" min="1" value={group} disabled={busy}
          onChange={e => updateWorkspaceQuery({ group: e.target.value || null, message: null })} /></label>
      </section>
      <section><h2>历史本地建库</h2><p>先预览可读取的归档范围与异常。此处只导入本地消息，不调用 AI。</p>
        <button disabled={busy} onClick={() => void action(async () => setPreview(await knowledgeApi.preview(group ? Number(group) : undefined)))}>预览历史导入</button>
        {preview && <div><p>找到 {preview.items.length} 份归档、{preview.record_count} 条来源记录；AI 调用 {preview.ai_calls} 次。</p>
          <p>群归属待确认：{preview.items.filter(i => i.group_id == null).length} 份；无法读取：{preview.errors.length} 份。</p>
          <details><summary>查看范围和异常</summary><ul>{preview.items.map(i => <li key={i.locator}>{i.locator} · {i.coverage_state}</li>)}{preview.errors.map(e => <li key={e.locator}>{e.locator}：{e.error}</li>)}</ul></details>
          <button disabled={busy || !status.worker_enabled || !preview.items.length} onClick={() => void action(async () => {
            await knowledgeApi.backfill(preview); setPreview(undefined); setRefresh(n => n + 1);
          })}>确认本地导入</button>
        </div>}
      </section>
      <section><h2>知识任务</h2>{!jobs.length && <p>暂无任务</p>}
        {jobs.map(j => <article key={j.id}><strong>#{j.id} · {j.job_kind} · {j.status}</strong>
          <p>已处理归档：{j.checkpoint.next_item || 0} {j.error_code}</p>
          {['PENDING', 'RUNNING', 'WAIT_RETRY', 'WAIT_BUDGET'].includes(j.status) && <button disabled={busy || !!j.pause_requested} onClick={() => void action(async () => { await knowledgeApi.control(j.id, 'pause'); setRefresh(n => n + 1); })}>暂停</button>}
          {['FAILED', 'PAUSED', 'PARTIAL', 'WAIT_RETRY', 'WAIT_BUDGET'].includes(j.status) && <button disabled={busy || !status.worker_enabled} onClick={() => void action(async () => { await knowledgeApi.control(j.id, 'retry'); setRefresh(n => n + 1); })}>重试</button>}
          {j.result.errors?.map((e, i) => <p key={i}>{e.locator}：{e.error}</p>)}
          {(j.result.error||j.result.reason)&&<p>{j.result.error||j.result.reason}</p>}
          {j.result.rejected?.map(r=><p key={r.candidate}>候选 {r.candidate+1}：{r.reason}</p>)}
        </article>)}
      </section>
      <section><h2>统一消息</h2>{messages && !messages.items.length && <p>尚无已导入消息</p>}
        {messages?.items.map(m => <article key={m.id}><strong>{m.sender_name || '身份待确认'}</strong> <time>{new Date(m.sent_at).toLocaleString('zh-CN')}</time>
          <p>{m.content}</p><button onClick={() => updateWorkspaceQuery({ message: String(m.id) })}>查看来源 #{m.id}</button>
        </article>)}
        {messages?.next_cursor && <button disabled={busy} onClick={() => void action(async () => {
          const params = new URLSearchParams({ cursor: messages.next_cursor! }); if (group) params.set('group_id', group);
          const next = await knowledgeApi.messages(params); setMessages({ ...next, items: [...messages.items, ...next.items] });
        })}>加载更多消息</button>}
      </section>
    </>}
    {Number.isInteger(messageId) && messageId > 0 && <EvidenceDrawer messageId={messageId} onClose={() => updateWorkspaceQuery({ message: null })} />}
  </div>;
}
