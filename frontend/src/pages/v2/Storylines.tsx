import {useEffect,useState} from 'react';
import {listGroups,type GroupV2} from '../../api';
import {storylineApi,type Storyline,type StoryPreview} from '../../storylineApi';
import {navigateToHash,updateWorkspaceQuery,useWorkspaceQuery} from '../../navigation';
import EvidenceDrawer from '../../components/EvidenceDrawer';
import {useUnsavedChanges} from '../../components/useUnsavedChanges';
import '../../knowledge.css';

export default function Storylines(){
  const q=useWorkspaceQuery();const group=q.get('groupId')||'';const id=Number(q.get('storyline'));const message=Number(q.get('message'));
  const [groups,setGroups]=useState<GroupV2[]>([]);const [items,setItems]=useState<Storyline[]>([]);const [selected,setSelected]=useState<Storyline>();
  const [title,setTitle]=useState('');const [entries,setEntries]=useState(q.get('entries')||'');const [preview,setPreview]=useState<StoryPreview>();
  const [error,setError]=useState('');const [notice,setNotice]=useState('');const [busy,setBusy]=useState(false);const [refresh,setRefresh]=useState(0);
  const clearDirty=useUnsavedChanges(Boolean(title||entries),busy);
  useEffect(()=>{void listGroups().then(setGroups).catch(e=>setError(String(e)));},[]);
  useEffect(()=>{let active=true;setItems([]);setPreview(undefined);void storylineApi.list(group).then(r=>{if(active)setItems(r.items);}).catch(e=>{if(active)setError(String(e));});return()=>{active=false;};},[group,refresh]);
  useEffect(()=>{let active=true;setSelected(undefined);if(id>0)void storylineApi.detail(id).then(r=>{if(active)setSelected(r);}).catch(e=>{if(active)setError(String(e));});return()=>{active=false;};},[id,refresh]);
  async function act(fn:()=>Promise<void>){setBusy(true);setError('');try{await fn();}catch(e){setError(String(e));}finally{setBusy(false);}}
  return <div className="knowledge-page"><header><div><h1>故事线</h1><p>将同一主体的已有进展连接成时间轴，每个节点保留原消息。</p></div><button onClick={()=>navigateToHash('memories',false)}>群聊记忆</button></header>
    {error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
    <section><label>群聊 <select aria-label="故事线群聊" value={group} onChange={e=>updateWorkspaceQuery({groupId:e.target.value||null,storyline:null})}><option value="">全部群</option>{groups.map(g=><option key={g.id} value={g.id}>{g.display_name||g.wechat_group_name}</option>)}</select></label>
      <h2>{selected?'为当前故事线增加进展':'建立故事线'}</h2><p>在记忆详情中查看进展编号。仅允许相同可靠主体关联，身份不足时先保留独立记忆。</p>
      {!selected&&<label>标题 <input aria-label="故事线标题" value={title} maxLength={120} onChange={e=>{setTitle(e.target.value);setPreview(undefined);}}/></label>}
      <label>进展编号（逗号分隔） <input aria-label="故事线进展编号" value={entries} onChange={e=>{setEntries(e.target.value);setPreview(undefined);}}/></label>
      <button disabled={busy||(!group&&!selected)||!entries||(!selected&&!title)} onClick={()=>void act(async()=>setPreview(await storylineApi.preview({group_id:selected?.group_id||Number(group),title:selected?.title||title,entry_ids:entries.split(/[,，\s]+/).filter(Boolean).map(Number),storyline_id:selected?.id||null}))) }>预览关联</button>
      {preview&&<section><h3>{preview.title}</h3><p>主体：{preview.subjects.join('、')}</p>{preview.warnings.map(w=><p key={w}>{w}</p>)}{preview.entries.map(e=><p key={e.id}>#{e.id} · {e.summary}</p>)}<button disabled={busy} onClick={()=>void act(async()=>{const r=await storylineApi.link(preview);setTitle('');setEntries('');setPreview(undefined);setNotice('进展关联已保存');clearDirty();updateWorkspaceQuery({storyline:String(r.id),entries:null});setRefresh(v=>v+1);})}>确认关联这些进展</button></section>}
    </section>
    <section aria-label="故事线列表">{!items.length&&!error&&<p>暂无故事线。</p>}{items.map(s=><article key={s.id}><h2>{s.title}</h2><p>{s.summary}</p><button onClick={()=>updateWorkspaceQuery({storyline:String(s.id)})}>查看时间轴 #{s.id}</button></article>)}</section>
    {selected&&<section aria-label="故事线时间轴"><h2>{selected.title}</h2><ol>{selected.entries?.map(e=><li key={e.id}><time>{new Date(e.event_at||e.observed_start).toLocaleDateString('zh-CN')} · {e.event_at?'明确事件日期':'发言观察日期'}</time><p>进展 #{e.id}：{e.summary}</p>{e.sources.map(s=><blockquote key={`${s.claim_key}:${s.message_id}`}>{s.quote}<button onClick={()=>updateWorkspaceQuery({message:String(s.message_id)})}>查看来源 #{s.message_id}</button></blockquote>)}<button disabled={busy} onClick={()=>void act(async()=>{await storylineApi.unlink(selected,e.id);setNotice('已解除关联，进展与原消息保留，可重新关联。');setRefresh(v=>v+1);})}>解除进展 #{e.id} 的关联</button></li>)}</ol></section>}
    {message>0&&<EvidenceDrawer messageId={message} onClose={()=>updateWorkspaceQuery({message:null})}/>}
  </div>;
}
