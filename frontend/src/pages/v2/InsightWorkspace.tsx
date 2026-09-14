import { useEffect, useState } from 'react';
import { listGroups, type GroupV2 } from '../../api';
import { insightApi, type InsightDetail, type InsightSummary, type MetricChange } from '../../insightApi';
import { type MessageRecord } from '../../knowledgeApi';
import EvidenceDrawer from '../../components/EvidenceDrawer';
import { navigateToHash, updateWorkspaceQuery, useWorkspaceQuery } from '../../navigation';
import '../../knowledge.css';

export function comparisonText(value: MetricChange | undefined) {
  if (!value || value.state === 'not_comparable') return '数据不足，暂不可比较';
  if (value.state === 'zero_baseline') return `从 0 增至 ${value.current}`;
  return `环比 ${value.percent! > 0 ? '+' : ''}${value.percent}%`;
}

export default function InsightWorkspace() {
  const query = useWorkspaceQuery();
  const group = query.get('insightGroup') || '';
  const reportId = Number(query.get('insight'));
  const evidenceId = Number(query.get('message'));
  const [groups, setGroups] = useState<GroupV2[]>([]);
  const [reports, setReports] = useState<InsightSummary[]>([]);
  const [report, setReport] = useState<InsightDetail>();
  const [sources, setSources] = useState<{ items: MessageRecord[]; next_offset: number | null }>();
  const [history, setHistory] = useState(false);
  const [day, setDay] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => { void listGroups().then(setGroups).catch(e => setError(String(e))); }, []);
  useEffect(() => {
    let active = true;
    setError(''); setReports([]);
    void insightApi.list(group, history).then(r => { if (active) setReports(r.items); }).catch(e => { if (active) setError(String(e)); });
    return () => { active = false; };
  }, [group, history, refresh]);
  useEffect(() => {
    let active = true;
    setReport(undefined); setSources(undefined);
    if (reportId > 0) void insightApi.detail(reportId).then(r => { if (active) setReport(r); }).catch(e => { if (active) setError(String(e)); });
    return () => { active = false; };
  }, [reportId, refresh]);
  async function build() {
    setBusy(true); setError('');
    try { const task = await insightApi.build(Number(group), day); setNotice(`计算任务 #${task.job_id} 已提交，完成后刷新作品列表。`); }
    catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  async function loadSources() {
    setBusy(true);
    try { const next = await insightApi.messages(reportId, sources?.next_offset || 0); setSources({ ...next, items: [...(sources?.items || []), ...next.items] }); }
    catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  return <div className="knowledge-page insight-workspace">
    <section><h2>Weekly Insight · 周度变化</h2><p>按消息发生日期计算自然周，话题与事件来自有来源的长期记忆。</p>
      <div className="insight-controls"><label>群聊 <select aria-label="洞察群聊" value={group} disabled={busy} onChange={e => updateWorkspaceQuery({ insightGroup: e.target.value || null, insight: null, message: null })}>
        <option value="">全部群</option>{groups.map(g => <option key={g.id} value={g.id}>{g.display_name || g.wechat_group_name}</option>)}</select></label>
      <label>周期内任一天 <input aria-label="洞察日期" type="date" value={day} onChange={e => setDay(e.target.value)} /></label>
      <button disabled={busy || !group || !day} onClick={() => void build()}>计算周度洞察</button>
      <button disabled={busy} onClick={() => setRefresh(v => v + 1)}>刷新洞察</button>
      <label><input type="checkbox" checked={history} onChange={e => setHistory(e.target.checked)} />显示旧版本</label></div>
      {notice && <p role="status">{notice}</p>}{error && <p role="alert">{error}</p>}
    </section>
    <div className="insight-columns"><section><h2>周报列表</h2>{!reports.length && <p>尚无周期洞察。数据底座就绪后可以计算；旧日报仍可在日报工作区查看。</p>}
      {reports.map(r => <article key={r.id}><p>{groups.find(g => g.id === r.group_id)?.display_name || `群 ${r.group_id}`} · {new Date(r.period_start).toLocaleDateString('zh-CN')} 起</p>
        <button disabled={busy} onClick={() => updateWorkspaceQuery({ insight: String(r.id), message: null })}>查看周报 #{r.id} · v{r.revision}</button><p>{r.status === 'READY' ? '统计完整' : '统计尚未完整'}</p></article>)}
    </section>
    {report && <section aria-label="周期洞察详情"><h2>本周概况 · v{report.revision}</h2>
      <p>{report.coverage.current.complete ? '本期消息覆盖完整' : '本期仍有消息缺口或身份冲突'}；{report.coverage.previous.complete ? '基准期完整' : '基准期不完整'}。</p>
      <div className="insight-metrics"><div><strong>{report.metrics.message_count}</strong><p>已知消息数</p><small>{comparisonText(report.metrics.comparison.message_count)}</small></div>
        <div><strong>{report.metrics.speaker_count}</strong><p>活跃身份数</p><small>{comparisonText(report.metrics.comparison.speaker_count)}</small></div></div>
      {!!report.metrics.uncertain_identity_messages && <p>有 {report.metrics.uncertain_identity_messages} 条消息的身份不确定。人数范围：{report.metrics.active_people_min}—{report.metrics.active_people_max}。</p>}
      <h3>每日消息趋势</h3><div className="insight-bars">{report.metrics.daily_counts.map(d => <div key={d.date}><span>{d.date.slice(5)}</span><meter min="0" max={Math.max(1, ...report.metrics.daily_counts.map(v => v.count || 0))} value={d.count || 0} /><span>{d.count ?? '未知'}{!d.complete && d.count != null ? '（已知）' : ''}</span></div>)}</div>
      <h3>全员排行与变化</h3><p>口径：{report.metrics.count_policy === 'text_primary' ? '文字优先' : '全部可计数消息'}；同分按现有稳定姓名规则排序。</p>
      <div className="insight-table"><table><thead><tr><th>名次</th><th>成员</th><th>消息</th><th>活跃天</th><th>名次变化</th><th>消息变化</th></tr></thead><tbody>{report.metrics.members.map(m => <tr key={m.identity_key}><td>{m.rank}</td><td>{m.name}{m.new_to_top ? ' · 新晋 Top' : ''}{m.newly_active ? ' · 本期出现' : ''}</td><td>{m.count}</td><td>{m.active_days}</td><td>{m.rank_change ?? '—'}</td><td>{m.count_change ?? '—'}</td></tr>)}</tbody></table></div>
      <p>周冠军：{report.metrics.champion?.name || '覆盖或身份不足，暂不确认'}</p>
      {!!report.metrics.inactive_previous_members.length && <p>上期活跃、本期未发言：{report.metrics.inactive_previous_members.map(m => m.name).join('、')}</p>}
      <details><summary>统计口径与覆盖缺口</summary><p>{report.metrics_version}</p><pre>{JSON.stringify(report.coverage, null, 2)}</pre></details>
      <h3>讨论重点与值得记住的事情</h3>{!report.sections?.length&&<p>尚无可用的记忆分析，统计仍可独立查看。</p>}
      {report.sections?.map(s=><article key={s.key}><h4>{s.title}</h4>{s.note&&<p>{s.note}</p>}{s.summary&&<p>{s.summary}</p>}{s.review_status==='review'&&<p>该记忆仍待核对</p>}
        {s.items?.slice(0,15).map(t=><p key={t.memory_id}>{t.title} · {t.discussion_days} 个讨论日 · {t.participants} 位参与者 · {t.evidence_messages} 条证据 · {t.lifecycle==='newly_observed'?'首次观察到':t.lifecycle==='continuing'?'持续讨论':'再次活跃'}</p>)}
        {s.claims?.map(c=><div key={c.key}><p>{c.text}</p>{c.message_ids.map(mid=><button key={mid} onClick={()=>updateWorkspaceQuery({message:String(mid)})}>查看断言来源 #{mid}</button>)}</div>)}
        {s.memory_id&&<button onClick={()=>navigateToHash(`memories?memory=${s.memory_id}`,false)}>查看当前记忆</button>}{s.storyline_id&&<button onClick={()=>navigateToHash(`storylines?storyline=${s.storyline_id}`,false)}>查看故事线</button>}
      </article>)}
      <h3>统计来源</h3>{sources?.items.map(m => <article key={m.id}><p>{m.sender_name}：{m.content}</p><button onClick={() => updateWorkspaceQuery({ message: String(m.id) })}>查看来源 #{m.id}</button></article>)}
      {(!sources || sources.next_offset != null) && <button disabled={busy} onClick={() => void loadSources()}>{sources ? '更多统计来源' : '查看统计来源'}</button>}
    </section>}
    </div>
    {evidenceId > 0 && <EvidenceDrawer messageId={evidenceId} onClose={() => updateWorkspaceQuery({ message: null })} />}
  </div>;
}
