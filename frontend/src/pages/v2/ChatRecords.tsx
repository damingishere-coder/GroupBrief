import { useEffect, useMemo, useState } from "react";
import {
  ArrowsClockwise,
  ChatDots,
  Funnel,
  MagnifyingGlass,
  UserCircle,
} from "@phosphor-icons/react";
import {
  ArchivedMessage,
  getRunDetail,
  getRuns,
  readV2JsonFile,
  V2Run,
} from "../../api";
import {
  Button,
  EmptyState,
  LoadingState,
  PageHeader,
  StatusBadge,
  Toast,
} from "../../components/common";
import { useToast } from "../../components/ui";
import { shanghaiDateInputValue } from "../../date";
import { ContentSwap } from "../../components/motion";

const STATUS_LABELS: Record<string, string> = {
  PENDING: "待生成",
  DATA_READY: "数据就绪",
  RANKING_READY: "排行完成",
  PROMPT_READY: "Prompt 完成",
  IMAGE_READY: "图片完成",
  READY_TO_SEND: "待发送",
  SENT: "已发送",
  FAILED: "失败",
};

interface RunEntry {
  run: V2Run;
  files: string[];
  detailError?: string;
}

interface ParsedMessages {
  messages: ArchivedMessage[];
  warning: string;
}

function runKey(run: V2Run): string {
  return `${run.group_name}\u0000${run.run_date}`;
}

function statusTone(status: string): "success" | "warning" | "danger" | "info" | "neutral" {
  if (["SENT", "IMAGE_READY", "READY_TO_SEND"].includes(status)) return "success";
  if (status === "FAILED") return "danger";
  if (["PROMPT_READY", "RANKING_READY"].includes(status)) return "info";
  if (["PENDING", "DATA_READY"].includes(status)) return "warning";
  return "neutral";
}

function StatusPill({ status }: { status: string }) {
  const normalized = status.toUpperCase();
  return <StatusBadge tone={statusTone(normalized)}>{STATUS_LABELS[normalized] || status || "未知"}</StatusBadge>;
}

function valueAsText(value: unknown): string {
  return typeof value === "string" ? value : value === null || value === undefined ? "" : String(value);
}

function objectValue(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function parseMessages(value: unknown): ParsedMessages {
  let rows: unknown[] | null = Array.isArray(value) ? value : null;
  if (!rows) {
    const container = objectValue(value);
    const nested = container?.messages;
    rows = Array.isArray(nested) ? nested : null;
  }
  if (!rows) return { messages: [], warning: "messages.json 不是消息数组格式，无法解析归档消息。" };

  let malformed = 0;
  const messages = rows.flatMap((row) => {
    const record = objectValue(row);
    if (!record) {
      malformed += 1;
      return [];
    }
    return [{
      message_id: valueAsText(record.message_id),
      group_id: valueAsText(record.group_id),
      group_name: valueAsText(record.group_name),
      sender_id: valueAsText(record.sender_id),
      sender_name: valueAsText(record.sender_name),
      timestamp: valueAsText(record.timestamp),
      message_type: valueAsText(record.message_type) || "unknown",
      content: valueAsText(record.content),
    }];
  });
  return {
    messages,
    warning: malformed ? `已忽略 ${malformed} 条无法解析的消息记录。` : "",
  };
}

function sortMessages(messages: ArchivedMessage[]): ArchivedMessage[] {
  return messages
    .map((message, index) => ({ message, index, time: Date.parse(message.timestamp) }))
    .sort((left, right) => {
      const leftValid = Number.isFinite(left.time);
      const rightValid = Number.isFinite(right.time);
      if (leftValid && rightValid && left.time !== right.time) return left.time - right.time;
      if (leftValid !== rightValid) return leftValid ? -1 : 1;
      return left.index - right.index;
    })
    .map(({ message }) => message);
}

function formatTimestamp(value: string): string {
  if (!value) return "时间未提供";
  return value.replace("T", " ").slice(0, 19);
}

function initials(name: string): string {
  const trimmed = name.trim();
  return trimmed ? trimmed.slice(0, 1).toUpperCase() : "?";
}

export default function ChatRecords() {
  const { msg, toast } = useToast();
  const [entries, setEntries] = useState<RunEntry[]>([]);
  const [dateFilter, setDateFilter] = useState(shanghaiDateInputValue);
  const [groupFilter, setGroupFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [selectedKey, setSelectedKey] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [messages, setMessages] = useState<ArchivedMessage[]>([]);
  const [messageWarning, setMessageWarning] = useState("");
  const [messageError, setMessageError] = useState("");
  const [messagesLoading, setMessagesLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [typeFilter, setTypeFilter] = useState("all");
  const [visibleCount, setVisibleCount] = useState(80);

  const loadEntries = () => {
    setLoading(true);
    setLoadError("");
    getRuns(dateFilter || undefined, { includeFiles: true })
      .then(async (data) => {
        const detailed = await Promise.all(data.runs.map(async (run): Promise<RunEntry> => {
          if (Array.isArray(run.files)) {
            return {
              run,
              files: run.files.filter((file): file is string => typeof file === "string"),
            };
          }
          try {
            const detail = await getRunDetail(run.group_name, run.run_date);
            return { run: detail.run, files: detail.files };
          } catch (reason) {
            return { run, files: [], detailError: `运行详情读取失败：${String(reason)}` };
          }
        }));
        setEntries(detailed);
        setSelectedKey((current) => {
          if (detailed.some((entry) => runKey(entry.run) === current && entry.files.includes("messages.json"))) return current;
          const withMessages = detailed.find((entry) => entry.files.includes("messages.json"));
          return withMessages ? runKey(withMessages.run) : "";
        });
      })
      .catch((reason: unknown) => {
        setLoadError(`聊天归档加载失败：${String(reason)}`);
        toast(`聊天归档加载失败：${String(reason)}`);
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    loadEntries();
    // 日期筛选变化时重新读取真实运行详情。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dateFilter]);

  const filteredEntries = useMemo(() => {
    const query = groupFilter.trim().toLocaleLowerCase();
    return entries.filter((entry) => {
      const groupName = String(entry.run.group_name || "");
      const status = String(entry.run.status || "").toUpperCase();
      return (!query || groupName.toLocaleLowerCase().includes(query)) && (statusFilter === "all" || status === statusFilter);
    });
  }, [entries, groupFilter, statusFilter]);

  useEffect(() => {
    if (!filteredEntries.some((entry) => runKey(entry.run) === selectedKey)) {
      const withMessages = filteredEntries.find((entry) => entry.files.includes("messages.json"));
      setSelectedKey(withMessages ? runKey(withMessages.run) : filteredEntries[0] ? runKey(filteredEntries[0].run) : "");
    }
  }, [filteredEntries, selectedKey]);

  const selectedEntry = entries.find((entry) => runKey(entry.run) === selectedKey) || null;

  useEffect(() => {
    setMessages([]);
    setMessageWarning("");
    setMessageError("");
    setVisibleCount(80);
    setSearch("");
    setTypeFilter("all");
    if (!selectedEntry) return;
    if (!selectedEntry.files.includes("messages.json")) {
      setMessageError(selectedEntry.detailError || "该运行详情没有 messages.json，暂无可回看的归档消息。");
      return;
    }
    setMessagesLoading(true);
    readV2JsonFile<unknown>(selectedEntry.run.group_name, selectedEntry.run.run_date, "messages.json")
      .then((payload) => {
        const parsed = parseMessages(payload);
        setMessages(sortMessages(parsed.messages));
        setMessageWarning(parsed.warning);
      })
      .catch((reason: unknown) => setMessageError(`messages.json 读取失败：${String(reason)}`))
      .finally(() => setMessagesLoading(false));
  }, [selectedEntry]);

  const messageTypes = useMemo(() => Array.from(new Set(messages.map((message) => message.message_type))).sort(), [messages]);
  const filteredMessages = useMemo(() => {
    const query = search.trim().toLocaleLowerCase();
    return messages.filter((message) => {
      const matchesType = typeFilter === "all" || message.message_type === typeFilter;
      if (!matchesType) return false;
      if (!query) return true;
      return [message.content, message.sender_name, message.group_name, message.message_id]
        .some((value) => value.toLocaleLowerCase().includes(query));
    });
  }, [messages, search, typeFilter]);
  const visibleMessages = filteredMessages.slice(0, visibleCount);

  if (loading && entries.length === 0) return <LoadingState label="正在读取聊天归档…" />;
  if (loadError && entries.length === 0) {
    return <EmptyState title="聊天记录加载失败" description={loadError} action={<Button tone="secondary" onClick={loadEntries}>重新加载</Button>} />;
  }

  return (
    <div className="chat-records-page">
      <PageHeader
        title="聊天记录"
        description="只读查看真实 messages.json 归档；这是历史记录，不是实时聊天。"
        actions={<><StatusBadge tone="neutral">归档记录，不是实时聊天</StatusBadge><Button tone="ghost" onClick={loadEntries} busy={loading}><ArrowsClockwise size={17} aria-hidden="true" />刷新归档</Button></>}
      />

      <section className="chat-records-filter-bar" aria-label="聊天归档筛选">
        <label><span>运行日期</span><input type="date" value={dateFilter} onChange={(event) => setDateFilter(event.target.value)} /></label>
        <label><span>群名</span><input type="search" value={groupFilter} placeholder="搜索群名" onChange={(event) => setGroupFilter(event.target.value)} /></label>
        <label><span>运行状态</span><select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}><option value="all">全部状态</option>{Object.entries(STATUS_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <span className="chat-records-filter-count">显示 {filteredEntries.length} / {entries.length} 条记录</span>
      </section>

      <div className="chat-records-workspace">
        <aside className="chat-records-run-list" aria-label="聊天归档运行记录">
          <div className="chat-records-section-head"><div><h2>归档选择</h2><p>默认选择包含 messages.json 的记录。</p></div><ChatDots size={22} aria-hidden="true" /></div>
          {filteredEntries.length === 0 ? <EmptyState title="没有匹配记录" description="请调整日期、群名或状态筛选。" /> : <div className="chat-records-run-items">{filteredEntries.map((entry) => <button type="button" key={runKey(entry.run)} className={`chat-records-run-item ${selectedKey === runKey(entry.run) ? "is-active" : ""}`} onClick={() => setSelectedKey(runKey(entry.run))} aria-pressed={selectedKey === runKey(entry.run)}><div><strong>{entry.run.group_name || "未命名群"}</strong><span>{entry.run.run_date}</span></div><div className="chat-records-run-status"><StatusPill status={entry.run.status} />{entry.files.includes("messages.json") ? <span className="chat-file-ok">有消息文件</span> : <span className="chat-file-missing">无消息文件</span>}</div></button>)}</div>}
        </aside>

        <main className="chat-records-panel" aria-label="归档消息内容">
          <ContentSwap swapKey={!selectedEntry ? "empty" : messagesLoading ? "loading" : selectedKey}>
            {!selectedEntry ? <EmptyState title="暂无可用聊天归档" description="当前运行记录中没有包含 messages.json 的真实归档。" /> : messagesLoading ? <LoadingState label="正在解析真实消息归档…" /> : messageError ? <EmptyState title="无法读取聊天归档" description={messageError} /> : (
            <>
              <div className="chat-records-panel-head"><div><span className="chat-records-eyebrow">只读归档</span><h2>{selectedEntry.run.group_name} · {selectedEntry.run.run_date}</h2><p><StatusPill status={selectedEntry.run.status} /> · 周期 {selectedEntry.run.period_start || "—"} ~ {selectedEntry.run.period_end || "—"}</p></div><UserCircle size={27} aria-hidden="true" /></div>
              <div className="chat-records-message-toolbar">
                <div className="chat-records-search"><MagnifyingGlass size={17} aria-hidden="true" /><label className="sr-only" htmlFor="message-search">搜索消息内容</label><input id="message-search" type="search" value={search} placeholder="搜索发送者、内容或消息 ID" onChange={(event) => setSearch(event.target.value)} /></div>
                <label className="chat-records-type-filter"><Funnel size={16} aria-hidden="true" /><span className="sr-only">消息类型</span><select value={typeFilter} onChange={(event) => setTypeFilter(event.target.value)}><option value="all">全部消息类型</option>{messageTypes.map((type) => <option key={type} value={type}>{type}</option>)}</select></label>
                <span className="chat-records-match-count">匹配 {filteredMessages.length} / {messages.length} 条</span>
              </div>
              {messageWarning && <div className="chat-records-warning">{messageWarning}</div>}
              {messages.length === 0 ? <EmptyState title="该归档没有消息" description="messages.json 存在，但消息数组为空。" /> : filteredMessages.length === 0 ? <EmptyState title="没有匹配消息" description="请调整文本搜索或消息类型筛选。" /> : <div className="chat-message-list">{visibleMessages.map((message, index) => <article className="chat-message-row" key={message.message_id || `${message.timestamp}-${index}`}><div className="chat-message-avatar" aria-hidden="true">{initials(message.sender_name)}</div><div className="chat-message-body"><div className="chat-message-meta"><strong>{message.sender_name || "未提供发送者"}</strong><StatusBadge tone={message.message_type === "text" ? "neutral" : "info"}>{message.message_type}</StatusBadge><time>{formatTimestamp(message.timestamp)}</time></div><p className={message.message_type === "text" ? "" : "chat-message-non-text"}>{message.content || (message.message_type === "text" ? "（空文本）" : "未归档媒体文件")}</p>{message.message_type !== "text" && <span className="chat-message-media-note">未归档媒体文件（无附件路径）</span>}<small>消息 ID：{message.message_id || "未提供"} · 发送者 ID：{message.sender_id || "未提供"} · 群 ID：{message.group_id || "未提供"}</small></div></article>)}</div>}
              {visibleCount < filteredMessages.length && <div className="chat-records-load-more"><Button tone="secondary" onClick={() => setVisibleCount((count) => count + 80)}>加载更多（剩余 {filteredMessages.length - visibleCount} 条）</Button></div>}
            </>
            )}
          </ContentSwap>
        </main>
      </div>
      <Toast message={msg} />
    </div>
  );
}
