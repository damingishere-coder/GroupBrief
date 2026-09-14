import { useEffect, useState } from 'react';
import { listGroups, type GroupV2 } from '../../api';
import { memoryApi, type Memory, type MergePreview, type MemoryPreview, type MemoryStatus } from '../../memoryApi';
import { navigateToHash, updateWorkspaceQuery, useWorkspaceQuery } from '../../navigation';
import { useUnsavedChanges } from '../../components/useUnsavedChanges';
import EvidenceDrawer from '../../components/EvidenceDrawer';
import '../../knowledge.css';

const types: Record<string,string>={topic:'话题',event:'事件',viewpoint:'观点',recommendation:'推荐',decision:'决定',highlight:'名场面',consensus:'共识'};
export default function Memories() {
  const query=useWorkspaceQuery(); const group=query.get('groupId')||'';const type=query.get('type')||'';
  const id=Number(query.get('memory'));const message=Number(query.get('message'));
  const [groups,setGroups]=useState<GroupV2[]>([]);const [items,setItems]=useState<Memory[]>([]);const [selected,setSelected]=useState<Memory>();
  const [next,setNext]=useState<number|null>(null);const [status,setStatus]=useState<MemoryStatus>();
  const [preview,setPreview]=useState<MergePreview>();const [backfill,setBackfill]=useState<MemoryPreview>();
  const [target,setTarget]=useState('');const [title,setTitle]=useState('');const [state,setState]=useState('active');
  const [undoId,setUndoId]=useState('');const [error,setError]=useState('');const [notice,setNotice]=useState('');const [busy,setBusy]=useState(false);const [refresh,setRefresh]=useState(0);
  const [note,setNote]=useState('');const [limits,setLimits]=useState({calls:3,input:24000,output:4000});
  useUnsavedChanges(Boolean(selected && (title!==selected.title || state!==selected.status)),busy);
  useEffect(()=>{void listGroups().then(setGroups).catch(e=>setError(String(e)));},[]);
  useEffect(()=>{let active=true;setItems([]);setError('');const params=new URLSearchParams();if(group)params.set('group_id',group);if(type)params.set('memory_type',type);
    void memoryApi.list(params).then(r=>{if(active){setItems(r.items);setNext(r.next_offset);}}).catch(e=>{if(active)setError(String(e));});
    void memoryApi.status().then(r=>{if(active){setStatus(r);setLimits(r.limits);}}).catch(e=>{if(active)setError(String(e));});return()=>{active=false;};},[group,type,refresh]);
  useEffect(()=>{let active=true;setSelected(undefined);setPreview(undefined);setUndoId('');if(id>0)void memoryApi.detail(id).then(r=>{if(active){setSelected(r);setTitle(r.title);setState(r.status);setUndoId(r.merge_operation_id?String(r.merge_operation_id):'');}}).catch(e=>{if(active)setError(String(e));});return()=>{active=false;};},[id,refresh]);
  async function act(fn:()=>Promise<void>){setBusy(true);setError('');try{await fn();}catch(e){setError(String(e));}finally{setBusy(false);}}
  return <div className="knowledge-page"><header><div><h1>群聊记忆</h1><p>话题、事件与进展长期保留，每条断言都能回到原消息。</p></div><button onClick={()=>setRefresh(v=>v+1)}>刷新</button></header>
    {error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
    <section className="insight-controls"><label>群聊 <select aria-label="记忆群聊" value={group} onChange={e=>updateWorkspaceQuery({groupId:e.target.value||null,memory:null})}><option value="">全部群</option>{groups.map(g=><option key={g.id} value={g.id}>{g.display_name||g.wechat_group_name}</option>)}</select></label>
      <label>类型 <select aria-label="记忆类型" value={type} onChange={e=>updateWorkspaceQuery({type:e.target.value||null,memory:null})}><option value="">全部记忆</option>{Object.entries(types).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select></label>
      <button onClick={()=>navigateToHash(`storylines${group?"?groupId="+group:""}`,false)}>故事线</button><button onClick={()=>navigateToHash(`search?type=memory${group?'&groupId='+group:''}`,false)}>搜索记忆</button>
    </section>
    {status&&<section><h2>分析与预算</h2><p>记忆处理{status.enabled?'已启用':'未启用'} · 补充 AI {status.ai_enabled?'已启用':'未启用'}。每日每群最多 {status.limits.calls} 次、输入 {status.limits.input}、输出 {status.limits.output} tokens 预算。</p>
      <div className="insight-controls">{(['calls','input','output'] as const).map(key=><label key={key}>{key==='calls'?'每日调用上限':key==='input'?'输入预算':'输出预算'} <input type="number" min="0" value={limits[key]} onChange={e=>setLimits({...limits,[key]:Number(e.target.value)})}/></label>)}<button disabled={busy} onClick={()=>void act(async()=>{await memoryApi.budgets(limits.calls,limits.input,limits.output);setNotice('预算已保存，后台将在下一轮工作前读取。');setRefresh(v=>v+1);})}>保存预算</button></div><p>未提供实际用量时使用保守估算；未知调用继续占用预算。复用海报分析只覆盖部分内容。</p>
      {status.groups.map(g=><p key={g.group_id}>群 {g.group_id}：{g.calls} 次调用，预留输入 {g.reserved_input} / 输出 {g.reserved_output}</p>)}
      <button disabled={busy||!status.enabled} onClick={()=>void act(async()=>setBackfill(await memoryApi.preview(group?Number(group):undefined)))}>预览近 90 天分析复用</button>{' '}
      <button disabled={busy||!status.ai_enabled} onClick={()=>void act(async()=>setBackfill(await memoryApi.preview(group?Number(group):undefined,'supplement')))}>预览补充 AI 与成本</button>
      {backfill&&<div><p>{backfill.filters.start} 至 {backfill.filters.end}，{backfill.message_count} 条消息，预计新增调用 {backfill.ai_calls_estimate} 次，按每日预算逐步处理。</p><button disabled={busy} onClick={()=>void act(async()=>{const r=await memoryApi.backfill(backfill);setNotice(`已建立 ${r.job_ids.length} 个记忆任务`);setBackfill(undefined);})}>确认此范围</button></div>}
      {status.operations.filter(o=>o.status==='HOLD_UNKNOWN').map(o=><article key={o.id}><p>调用 #{o.id} / 任务 #{o.job_id}：结果未知，自动重试已暂停。</p><label>核对依据 <input aria-label={`调用 ${o.id} 核对依据`} value={note} onChange={e=>setNote(e.target.value)} /></label><button disabled={busy||note.trim().length<5} onClick={()=>void act(async()=>{await memoryApi.resolve(o.job_id,o.id,'confirmed_not_submitted',note);setNotice('已记录未提交结论，任务保持暂停；请到知识任务手动重试。');setRefresh(v=>v+1);})}>已确认未提交</button><button disabled={busy||note.trim().length<5} onClick={()=>void act(async()=>{await memoryApi.resolve(o.job_id,o.id,'abandon',note);setRefresh(v=>v+1);})}>保留预算并放弃</button></article>)}
    </section>}
    <section aria-label="记忆列表">{!items.length&&!error&&<p>暂无记忆。可先复用已有分析，或等待新增消息处理。</p>}{items.map(m=><article key={m.id}><small>{types[m.type]||m.type} · {m.status==='review'?'待核对':m.status==='archived'?'已归档':'已保存'} · #{m.id}</small><h2>{m.title}</h2><p>{m.summary}</p>{m.merged_into_id&&<p>已合并到 #{m.merged_into_id}</p>}<button onClick={()=>updateWorkspaceQuery({memory:String(m.id)})}>查看进展与来源</button></article>)}
      {next!==null&&<button disabled={busy} onClick={()=>void act(async()=>{const p=new URLSearchParams({offset:String(next)});if(group)p.set('group_id',group);if(type)p.set('memory_type',type);const r=await memoryApi.list(p);setItems(v=>[...v,...r.items]);setNext(r.next_offset);})}>更多记忆</button>}
    </section>
    {selected&&<aside className="evidence-drawer" aria-label="记忆详情"><header><h2>{selected.title}</h2><button onClick={()=>updateWorkspaceQuery({memory:null})}>关闭记忆</button></header>
      <p>记忆是摘要索引。原文引用正确并不代表发言内容已经证实。</p><label>标题 <input aria-label="记忆标题" value={title} onChange={e=>setTitle(e.target.value)} maxLength={120}/></label><label>状态 <select value={state} onChange={e=>setState(e.target.value)}><option value="active">已保存</option><option value="review">待核对</option><option value="archived">已归档</option></select></label>
      <button disabled={busy} onClick={()=>void act(async()=>{const r=await memoryApi.edit(selected,title,state);setSelected(r);setNotice('已保存');})}>保存标题和状态</button>
      {selected.entries?.map(e=><article key={e.id}><time>{new Date(e.event_at||e.observed_start).toLocaleDateString('zh-CN')} · {e.event_at?'明确事件日期':'发言观察日期'}</time><p>进展 #{e.id}：{e.summary}</p>{e.claims.map(c=><div key={c.key}><strong>{c.text}</strong>{e.sources.filter(s=>s.claim_key===c.key).map(s=><blockquote key={s.message_id}>{s.quote}{s.validation_state!=='valid'&&<p>来源存在冲突，请核对</p>}<button onClick={()=>updateWorkspaceQuery({message:String(s.message_id)})}>查看原消息 #{s.message_id}</button></blockquote>)}</div>)}</article>)}
      {selected.next_offset!=null&&<button disabled={busy} onClick={()=>void act(async()=>{const r=await memoryApi.detail(selected.id,selected.next_offset!);setSelected({...r,entries:[...(selected.entries||[]),...(r.entries||[])]});})}>更多进展</button>}
      {!selected.merged_into_id&&<section><h3>核对合并</h3><label>目标记忆 ID <input aria-label="目标记忆 ID" type="number" min="1" value={target} onChange={e=>{setTarget(e.target.value);setPreview(undefined);}} /></label><button disabled={busy||!target} onClick={()=>void act(async()=>setPreview(await memoryApi.previewMerge(selected.id,Number(target))))}>预览合并</button>
        {preview&&<div><p>将“{preview.source.title}”合并到“{preview.target.title}”</p>{preview.warnings.map(w=><p key={w}>{w}</p>)}<p>原对象：{preview.source.summary}</p><p>目标对象：{preview.target.summary}</p><button disabled={busy} onClick={()=>void act(async()=>{const r=await memoryApi.merge(preview);setNotice(`合并已记录，操作 #${r.operation_id}，可以撤销。`);setRefresh(v=>v+1);})}>确认这次合并</button></div>}
      </section>}
      {selected.merged_into_id&&<section><p>已合并到 #{selected.merged_into_id}，原进展仍保留。</p><label>合并操作 ID <input aria-label="合并操作 ID" type="number" value={undoId} onChange={e=>setUndoId(e.target.value)} /></label><button disabled={busy||!undoId} onClick={()=>void act(async()=>{await memoryApi.undo(selected,Number(undoId));setNotice('合并已撤销');setRefresh(v=>v+1);})}>撤销此合并</button></section>}
    </aside>}
    {message>0&&<EvidenceDrawer messageId={message} onClose={()=>updateWorkspaceQuery({message:null})}/>}
  </div>;
}
