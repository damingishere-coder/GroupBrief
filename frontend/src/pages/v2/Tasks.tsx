import { useEffect, useMemo, useState } from "react";
import {
  ArrowsClockwise,
  CheckCircle,
  Clock,
  ListChecks,
  WarningCircle,
} from "@phosphor-icons/react";
import {
  getRunDetail,
  getRecoveryInfo,
  getRuns,
  getSystemHealth,
  retryFailed,
  RecoveryInfo,
  V2Run,
} from "../../api";
import {
  Button,
  ConfirmDialog,
  EmptyState,
  LoadingState,
  PageHeader,
  StatusBadge,
  Toast,
} from "../../components/common";
import { useFetch, useToast } from "../../components/ui";

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

interface TaskEntry {
  run: V2Run;
  files: string[];
  detailError?: string;
  integrity?: RecoveryInfo["integrity"][number];
}

function runKey(run: V2Run): string {
  return `${run.group_name}\u0000${run.run_date}`;
}

function statusTone(status: string): "success" | "warning" | "danger" | "info" | "neutral" {
  if (status === "SENT") return "success";
  if (status === "FAILED") return "danger";
  if (["IMAGE_READY", "READY_TO_SEND"].includes(status)) return "success";
  if (["PROMPT_READY", "RANKING_READY"].includes(status)) return "info";
  if (["PENDING", "DATA_READY"].includes(status)) return "warning";
  return "neutral";
}

function StatusPill({ status }: { status: string }) {
  const normalized = status.toUpperCase();
  return <StatusBadge tone={statusTone(normalized)}>{STATUS_LABELS[normalized] || status || "未知"} · {normalized || "UNKNOWN"}</StatusBadge>;
}

function formatDateTime(value: unknown): string {
  if (!value) return "—";
  return String(value).replace("T", " ").slice(0, 16);
}

function stringField(run: V2Run, field: string): string {
  const value = run[field];
  return typeof value === "string" ? value : value === null || value === undefined ? "" : String(value);
}

function runGroupId(run: V2Run): number | undefined {
  const value = run.group_id;
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : undefined;
}

export default function Tasks() {
  const { msg, toast } = useToast();
  const health = useFetch(getSystemHealth);
  const [entries, setEntries] = useState<TaskEntry[]>([]);
  const [recovery, setRecovery] = useState<RecoveryInfo | null>(null);
  const [recoveryError, setRecoveryError] = useState("");
  const [dateFilter, setDateFilter] = useState("");
  const [groupFilter, setGroupFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [selectedKey, setSelectedKey] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [retryTarget, setRetryTarget] = useState<TaskEntry | null>(null);
  const [retrying, setRetrying] = useState(false);

  const loadTasks = () => {
    setLoading(true);
    setLoadError("");
    Promise.all([
      getRuns(dateFilter || undefined),
      getRecoveryInfo().catch((reason: unknown) => {
        setRecoveryError(`恢复完整性读取失败：${String(reason)}`);
        return null;
      }),
    ])
      .then(async ([runData, recoveryData]) => {
        if (recoveryData) {
          setRecovery(recoveryData);
          setRecoveryError("");
        }
        const integrityMap = new Map<string, RecoveryInfo["integrity"][number]>();
        recoveryData?.integrity.forEach((item) => integrityMap.set(`${item.group_name}\u0000${item.run_date}`, item));
        const detailed = await Promise.all(runData.runs.map(async (run): Promise<TaskEntry> => {
          try {
            const detail = await getRunDetail(run.group_name, run.run_date);
            return { run: detail.run, files: detail.files, integrity: integrityMap.get(runKey(detail.run)) || integrityMap.get(runKey(run)) };
          } catch (reason) {
            return { run, files: [], integrity: integrityMap.get(runKey(run)), detailError: `运行详情读取失败：${String(reason)}` };
          }
        }));
        setEntries(detailed);
        setSelectedKey((current) => detailed.some((entry) => runKey(entry.run) === current) ? current : detailed[0] ? runKey(detailed[0].run) : "");
      })
      .catch((reason: unknown) => {
        setLoadError(`任务记录加载失败：${String(reason)}`);
        toast(`任务记录加载失败：${String(reason)}`);
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    loadTasks();
    // 日期筛选变化时重新读取真实任务记录。
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
      setSelectedKey(filteredEntries[0] ? runKey(filteredEntries[0].run) : "");
    }
  }, [filteredEntries, selectedKey]);

  const selectedEntry = entries.find((entry) => runKey(entry.run) === selectedKey) || null;
  const totalCount = entries.length;
  const sentCount = entries.filter((entry) => String(entry.run.status).toUpperCase() === "SENT").length;
  const failedCount = entries.filter((entry) => String(entry.run.status).toUpperCase() === "FAILED").length;
  const waitingCount = entries.filter((entry) => !["SENT", "FAILED"].includes(String(entry.run.status).toUpperCase())).length;
  const incompleteCount = entries.filter((entry) => entry.integrity && !entry.integrity.ok).length;
  const healthChecks = health.data ? Object.values(health.data.checks) : [];
  const healthy = healthChecks.length > 0 && healthChecks.every((check) => check.ok);

  const confirmRetry = () => {
    if (!retryTarget || retrying || String(retryTarget.run.status).toUpperCase() !== "FAILED") return;
    setRetrying(true);
    const groupId = runGroupId(retryTarget.run);
    retryFailed(groupId ? { group_id: groupId, run_date: retryTarget.run.run_date } : { run_date: retryTarget.run.run_date })
      .then((response) => {
        const result = groupId ? response.results?.[0] : response.results?.find((item) => item.group_name === retryTarget.run.group_name) || response.results?.[0];
        toast(result ? `重跑请求返回：${result.status}${result.detail ? ` · ${result.detail}` : ""}` : "后端未返回重跑结果");
        setRetryTarget(null);
        loadTasks();
      })
      .catch((reason: unknown) => toast(`重跑失败：${String(reason)}`))
      .finally(() => setRetrying(false));
  };

  if (loading && entries.length === 0) return <LoadingState label="正在加载任务中心…" />;
  if (loadError && entries.length === 0) {
    return <EmptyState title="任务中心加载失败" description={loadError} action={<Button tone="secondary" onClick={loadTasks}>重新加载</Button>} />;
  }

  return (
    <div className="tasks-page">
      <PageHeader
        title="任务中心"
        description="查看真实运行状态、输出完整性与错误；后端未记录阶段事件时不会虚构进度。"
        actions={<><StatusBadge tone={health.loading ? "neutral" : healthy ? "success" : "warning"}>{health.loading ? "健康检查中" : healthy ? "系统健康" : "需要关注"}</StatusBadge><Button tone="ghost" onClick={loadTasks} busy={loading}><ArrowsClockwise size={17} aria-hidden="true" />刷新任务</Button></>}
      />

      <section className="tasks-kpi-grid" aria-label="真实任务统计">
        <div className="tasks-kpi"><ListChecks size={19} aria-hidden="true" /><span>当前任务</span><strong>{totalCount}</strong><small>来自当前筛选日期的 runs</small></div>
        <div className="tasks-kpi"><CheckCircle size={19} aria-hidden="true" /><span>已发送</span><strong>{sentCount}</strong><small>原始状态为 SENT</small></div>
        <div className="tasks-kpi"><WarningCircle size={19} aria-hidden="true" /><span>失败</span><strong>{failedCount}</strong><small>仅失败任务提供重跑</small></div>
        <div className="tasks-kpi"><Clock size={19} aria-hidden="true" /><span>未完成</span><strong>{waitingCount}</strong><small>不等同于实时进度</small></div>
      </section>

      <section className="tasks-filter-bar" aria-label="任务筛选">
        <label><span>运行日期</span><input type="date" value={dateFilter} onChange={(event) => setDateFilter(event.target.value)} /></label>
        <label><span>群名</span><input type="search" value={groupFilter} placeholder="搜索群名" onChange={(event) => setGroupFilter(event.target.value)} /></label>
        <label><span>状态</span><select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}><option value="all">全部状态</option>{Object.entries(STATUS_LABELS).map(([value, label]) => <option key={value} value={value}>{label} · {value}</option>)}</select></label>
        <span className="tasks-filter-count">显示 {filteredEntries.length} / {entries.length} 条 · 完整性异常 {incompleteCount}</span>
      </section>

      {recoveryError && <div className="tasks-warning"><WarningCircle size={18} aria-hidden="true" /><span>{recoveryError}</span></div>}

      <div className="tasks-workspace">
        <section className="tasks-list-panel" aria-label="任务列表">
          <div className="tasks-section-head"><div><h2>运行任务</h2><p>状态标签同时保留后端原始状态码。</p></div><ListChecks size={22} aria-hidden="true" /></div>
          {filteredEntries.length === 0 ? <EmptyState title="没有匹配任务" description="请调整日期、群名或状态筛选。" /> : <div className="tasks-list-items">{filteredEntries.map((entry) => <button type="button" key={runKey(entry.run)} className={`tasks-list-item ${selectedKey === runKey(entry.run) ? "is-active" : ""}`} onClick={() => setSelectedKey(runKey(entry.run))} aria-pressed={selectedKey === runKey(entry.run)}><div><strong>{entry.run.group_name || "未命名群"}</strong><span>{entry.run.run_date} · 更新 {formatDateTime(entry.run.updated_at)}</span></div><div className="tasks-list-item-status"><StatusPill status={String(entry.run.status || "")} />{entry.integrity && !entry.integrity.ok && <WarningCircle size={17} aria-label="输出完整性异常" />}</div></button>)}</div>}
        </section>

        <section className="tasks-detail-panel" aria-label="任务详情">
          {!selectedEntry ? <EmptyState title="暂无任务详情" description="当前没有可展示的真实运行任务。" /> : (
            <>
              <div className="tasks-detail-head"><div><span className="tasks-eyebrow">真实运行状态</span><h2>{selectedEntry.run.group_name} · {selectedEntry.run.run_date}</h2><p><StatusPill status={String(selectedEntry.run.status || "")} /> · 更新时间 {formatDateTime(selectedEntry.run.updated_at)}</p></div></div>
              <div className="tasks-summary-grid"><div><span>统计周期</span><strong>{selectedEntry.run.period_start || "—"} ~ {selectedEntry.run.period_end || "—"}</strong></div><div><span>消息数</span><strong>{typeof selectedEntry.run.message_count === "number" ? selectedEntry.run.message_count : "—"}</strong></div><div><span>发言人数</span><strong>{typeof selectedEntry.run.speaker_count === "number" ? selectedEntry.run.speaker_count : "—"}</strong></div></div>
              <div className="tasks-detail-note"><Clock size={18} aria-hidden="true" /><span>当前后端未记录阶段事件；页面不显示虚构进度、耗时或事件时间线。</span></div>
              {String(selectedEntry.run.status).toUpperCase() === "PENDING" && selectedEntry.files.length === 0 && !selectedEntry.run.started_at && !selectedEntry.run.finished_at && !selectedEntry.run.updated_at && <div className="tasks-warning"><WarningCircle size={18} aria-hidden="true" /><span>该记录仅有 PENDING 状态，未发现文件或时间证据，不能确认任务已真实创建。</span></div>}
              {(stringField(selectedEntry.run, "error") || stringField(selectedEntry.run, "image_error") || stringField(selectedEntry.run, "error_type")) && <div className="tasks-error-box">错误：{stringField(selectedEntry.run, "error") || stringField(selectedEntry.run, "image_error") || stringField(selectedEntry.run, "error_type")}</div>}
              <div className="tasks-file-section"><div className="tasks-content-heading"><h3>输出文件</h3><span>来自运行详情与恢复完整性检查</span></div><div className="tasks-file-columns"><div><strong>已发现</strong>{selectedEntry.files.length ? <ul>{selectedEntry.files.map((file) => <li key={file}><CheckCircle size={15} aria-hidden="true" />{file}</li>)}</ul> : <p>运行详情没有文件记录。</p>}</div><div><strong>缺失</strong>{selectedEntry.integrity?.missing.length ? <ul className="tasks-missing-files">{selectedEntry.integrity.missing.map((file) => <li key={file}><WarningCircle size={15} aria-hidden="true" />{file}</li>)}</ul> : <p>{selectedEntry.integrity ? "完整性检查未发现缺失文件。" : "暂无恢复完整性数据。"}</p>}</div></div></div>
              {selectedEntry.detailError && <div className="tasks-warning"><WarningCircle size={18} aria-hidden="true" /><span>{selectedEntry.detailError}</span></div>}
              {String(selectedEntry.run.status).toUpperCase() === "FAILED" && <div className="tasks-retry-area"><div><strong>失败任务</strong><span>仅调用后端 retryFailed，不提供取消或虚构重试进度。</span></div><Button tone="danger" onClick={() => setRetryTarget(selectedEntry)}><ArrowsClockwise size={16} aria-hidden="true" />重跑失败任务</Button></div>}
            </>
          )}
        </section>
      </div>

      {recovery && <div className="tasks-recovery-summary">恢复扫描：{recovery.incomplete.length} 条未完成任务 · 完整性记录 {recovery.integrity.length} 条。这里只展示后端返回的恢复信息。</div>}
      <ConfirmDialog
        open={Boolean(retryTarget)}
        title="重跑失败任务？"
        description={retryTarget ? `将调用后端 retryFailed 重跑「${retryTarget.run.group_name}」${retryTarget.run.run_date} 的失败任务。不会取消或伪造任务进度，结果以接口返回为准。` : ""}
        confirmLabel="确认重跑"
        busy={retrying}
        onConfirm={confirmRetry}
        onCancel={() => !retrying && setRetryTarget(null)}
      />
      <Toast message={msg} />
    </div>
  );
}
