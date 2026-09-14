import { useEffect, useRef, useState, type FormEvent } from 'react';
import { listGroups, type GroupV2 } from '../../api';
import { searchApi, type LegacySearchReport, type SearchPage } from '../../searchApi';
import { navigateToHash, updateWorkspaceQuery, useWorkspaceQuery } from '../../navigation';
import EvidenceDrawer from '../../components/EvidenceDrawer';
import '../../knowledge.css';

export default function Search() {
  const route = useWorkspaceQuery();
  const q = route.get('q') || '';
  const type = route.get('type') === 'report' ? 'report' : route.get('type') === 'memory' ? 'memory' : 'message';
  const group = route.get('groupId') || '';
  const start = route.get('start') || '';
  const end = route.get('end') || '';
  const sender = route.get('sender') || '';
  const sort = route.get('sort') === 'time' ? 'time' : 'relevance';
  const archive = route.get('archived') === '1';
  const evidenceId = Number(route.get('message'));
  const [input, setInput] = useState(q);
  const [groups, setGroups] = useState<GroupV2[]>([]);
  const [result, setResult] = useState<SearchPage>();
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [legacy, setLegacy] = useState<LegacySearchReport>();
  const [refresh, setRefresh] = useState(0);
  const generation = useRef(0);
  const legacyRef = route.get('reportRef') || '';
  const reportHash = route.get('reportHash') || '';
  function params() {
    const values = new URLSearchParams({ q, object_type: type, sort });
    if (group) values.set('group_id', group);
    if (start) values.set('start', start);
    if (end) values.set('end', end);
    if (sender && type === 'message') values.set('sender_id', sender);
    if (archive) { values.set('include_deleted', 'true'); values.set('include_orphans', 'true'); }
    return values;
  }
  useEffect(() => { void listGroups().then(setGroups).catch(e => setError(String(e))); }, []);
  useEffect(() => { setInput(q); }, [q]);
  useEffect(() => {
    const current = ++generation.current;
    setResult(undefined); setError('');
    if (!q) { setBusy(false); return; }
    setBusy(true);
    void searchApi.query(params()).then(r => { if (current === generation.current) setResult(r); })
      .catch(e => { if (current === generation.current) setError(String(e)); })
      .finally(() => { if (current === generation.current) setBusy(false); });
    return () => { generation.current++; };
  }, [q, type, group, start, end, sender, sort, archive, refresh]);
  useEffect(() => {
    let active = true;
    setLegacy(undefined);
    if (legacyRef) void searchApi.report(legacyRef, reportHash).then(r => { if (active) setLegacy(r); }).catch(e => { if (active) setError(String(e)); });
    return () => { active = false; };
  }, [legacyRef, reportHash]);
  function submit(e: FormEvent) {
    e.preventDefault(); updateWorkspaceQuery({ q: input.trim() || null, message: null, reportRef: null });
  }
  async function more() {
    if (!result?.next_cursor) return;
    const current = generation.current;
    const values = params(); values.set('cursor', result.next_cursor);
    setBusy(true);
    try { const next = await searchApi.query(values); if (current === generation.current) setResult({ ...next, items: [...result.items, ...next.items] }); }
    catch (e) { if (current === generation.current) setError(String(e)); }
    finally { if (current === generation.current) setBusy(false); }
  }
  return <div className="knowledge-page search-workspace"><header><div><h1>搜索群聊</h1><p>找到过去的讨论，直接查看原消息。查询在本地执行，不调用 AI。</p></div></header>
    <section><form onSubmit={submit} className="insight-controls"><label>关键词 <input aria-label="搜索关键词" value={input} placeholder="Claude Code、手机、显示器…" onChange={e => setInput(e.target.value)} maxLength={256} /></label><button disabled={busy || !input.trim()}>搜索</button></form>
      <div className="insight-controls search-filters"><label>搜索对象 <select aria-label="搜索对象" value={type} onChange={e => updateWorkspaceQuery({ type: e.target.value, sender: null })}><option value="message">原始消息</option><option value="report">群报文字</option><option value="memory">长期记忆</option></select></label>
        <label>群聊 <select aria-label="搜索群聊筛选" value={group} onChange={e => updateWorkspaceQuery({ groupId: e.target.value || null })}><option value="">全部群</option>{groups.map(g => <option value={g.id} key={g.id}>{g.display_name || g.wechat_group_name}</option>)}</select></label>
        <label>开始日期 <input type="date" aria-label="搜索开始日期" value={start} onChange={e => updateWorkspaceQuery({ start: e.target.value || null })} /></label>
        <label>结束日期（不含） <input type="date" aria-label="搜索结束日期" value={end} onChange={e => updateWorkspaceQuery({ end: e.target.value || null })} /></label>
        {type === 'message' && <label>用户 ID <input aria-label="搜索用户 ID" value={sender} onChange={e => updateWorkspaceQuery({ sender: e.target.value || null })} /></label>}
        <label>排序 <select aria-label="搜索排序" value={sort} onChange={e => updateWorkspaceQuery({ sort: e.target.value })}><option value="relevance">相关度</option><option value="time">最新消息</option></select></label>
        <label><input type="checkbox" checked={archive} onChange={e => updateWorkspaceQuery({ archived: e.target.checked ? '1' : null })} />包含已归档群和未关联归档</label>
      </div><p>多个词默认同时匹配；双引号表示短语。中文支持单字和双字。自然语言语义理解尚未启用。</p>
      <button disabled={busy} onClick={() => { setBusy(true); void searchApi.index().then(r => setNotice(`索引任务 #${r.job_id} 已提交，完成后刷新结果。`)).catch(e => setError(String(e))).finally(() => setBusy(false)); }}>更新本地索引</button>{' '}
      <button disabled={busy} onClick={() => setRefresh(n => n + 1)}>刷新结果</button>
    </section>
    {notice && <p role="status">{notice}</p>}{error && <p role="alert">{error}</p>}
    {busy && <p role="status">正在查询…</p>}
    {result && <section aria-label="搜索结果"><p>已显示 {result.items.length} 条结果 · 索引版本 {result.index_version}</p>{result.warnings.map(w => <p key={w}>{w}</p>)}
      {!result.items.length && !result.warnings.length && <p>当前索引中没有匹配结果。</p>}
      {result.items.map((hit, i) => <article key={`${hit.type}:${hit.id || hit.ref}:${i}`}><strong>{hit.sender_name || hit.title}</strong> <small>{hit.group_id ? (groups.find(g => g.id === hit.group_id)?.display_name || `群 ${hit.group_id}`) : '归属待确认'} · {new Date(hit.sent_at || hit.period_start || '').toLocaleString('zh-CN')}</small>
        <p>{hit.snippet.map((part, n) => part.match ? <mark key={n}>{part.text}</mark> : <span key={n}>{part.text}</span>)}</p>
        {hit.validation_state && hit.validation_state !== 'valid' && <p>来源状态：{hit.validation_state}</p>}
        {hit.type === 'message' ? <button onClick={() => updateWorkspaceQuery({ message: String(hit.id) })}>查看来源 #{hit.id}</button>
          : hit.type === 'memory' ? <button onClick={() => navigateToHash(`memories?memory=${hit.id}`,false)}>查看记忆与证据</button>
          : hit.insight_id ? <button onClick={() => navigateToHash(`images?view=weekly&insight=${hit.insight_id}`, false)}>查看周期洞察</button>
          : <button onClick={() => updateWorkspaceQuery({ reportRef: hit.ref || null, reportHash: hit.source_hash || null })}>查看历史群报文字</button>}
      </article>)}{result.next_cursor && <button disabled={busy} onClick={() => void more()}>更多结果</button>}
    </section>}
    {evidenceId > 0 && <EvidenceDrawer messageId={evidenceId} onClose={() => updateWorkspaceQuery({ message: null })} />}
    {legacy && <aside className="evidence-drawer" aria-label="历史群报文字"><header><h2>{legacy.title}</h2><button onClick={() => updateWorkspaceQuery({ reportRef: null })}>关闭报告</button></header>{legacy.warnings.map(w => <p key={w}>{w}</p>)}<p>{legacy.body}</p><p>{legacy.locator}</p><p className="evidence-hash">SHA-256：{legacy.source_hash}</p></aside>}
  </div>;
}
