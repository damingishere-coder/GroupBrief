import { useEffect, useRef, useState } from 'react';
import { knowledgeApi, type MessageContext, type SourcePage } from '../knowledgeApi';
import '../knowledge.css';

export default function EvidenceDrawer({ messageId, onClose }: { messageId: number; onClose: () => void }) {
  const [context, setContext] = useState<MessageContext>();
  const [sources, setSources] = useState<SourcePage>();
  const [error, setError] = useState('');
  const [loadingMore, setLoadingMore] = useState(false);
  const target = useRef<HTMLLIElement>(null);
  const generation = useRef(0);
  useEffect(() => {
    const current = ++generation.current;
    setContext(undefined); setSources(undefined); setError(''); setLoadingMore(false);
    void Promise.allSettled([knowledgeApi.context(messageId), knowledgeApi.sources(messageId)]).then(([a, b]) => {
      if (current !== generation.current) return;
      if (a.status === 'fulfilled') setContext(a.value); else setError(String(a.reason));
      if (b.status === 'fulfilled') setSources(b.value); else setError(String(b.reason));
    });
    return () => { generation.current++; };
  }, [messageId]);
  useEffect(() => { target.current?.scrollIntoView?.({ block: 'center' }); }, [context]);
  async function more() {
    if (sources?.next_offset == null) return;
    const current = generation.current;
    setLoadingMore(true);
    try {
      const next = await knowledgeApi.sources(messageId, sources.next_offset);
      if (current === generation.current) setSources({ ...next, items: [...sources.items, ...next.items] });
    } catch (e) { if (current === generation.current) setError(String(e)); }
    finally { if (current === generation.current) setLoadingMore(false); }
  }
  return <aside className="evidence-drawer" aria-label="来源消息">
    <header><h2>来源消息 #{messageId}</h2><button onClick={onClose}>关闭来源</button></header>
    {error && <p role="alert">{error}</p>}
    {!context && !error && <p>正在读取来源…</p>}
    {context && <><h3>聊天上下文</h3><ol className="evidence-messages">
      {[...context.before, context.message, ...context.after].map(m => <li key={m.id}
        ref={m.id === messageId ? target : undefined} className={m.id === messageId ? 'is-source' : ''}>
        <strong>{m.sender_name || '身份待确认'}</strong> <time>{new Date(m.sent_at).toLocaleString('zh-CN')}</time>
        <p>{m.content}</p>{m.validation_state !== 'valid' && <small>来源状态：{m.validation_state}</small>}
      </li>)}
    </ol></>}
    <h3>原归档记录</h3>
    {sources?.items.map(s => <details key={`${s.batch_id}:${s.row_ordinal}`}>
      <summary>{s.source_locator} · 第 {s.row_ordinal + 1} 条</summary>
      <p>覆盖状态：{s.coverage_state}</p><p className="evidence-hash">SHA-256：{s.artifact_sha256}</p>
      <pre>{JSON.stringify(s.raw_record, null, 2)}</pre>
    </details>)}
    {sources?.next_offset != null && <button disabled={loadingMore} onClick={() => void more()}>更多来源</button>}
  </aside>;
}
