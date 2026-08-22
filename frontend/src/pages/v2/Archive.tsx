import { useEffect, useMemo, useState } from "react";
import {
  Archive as ArchiveIcon,
  ArrowClockwise,
  CalendarBlank,
  CaretLeft,
  CaretRight,
  FileText,
  ImageSquare,
  Trash,
  WarningCircle,
} from "@phosphor-icons/react";
import {
  ArchiveGroup,
  V2Run,
  V2RunDetail,
  getArchiveGroups,
  getRunDetail,
  getV2File,
  readV2TextFile,
  restoreGroup,
} from "../../api";
import { Button, EmptyState, LoadingState, PageHeader, StatusBadge, Toast } from "../../components/common";
import { useToast } from "../../components/ui";

type ArchiveView = "active" | "trash";
type PreviewState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ready"; content: string }
  | { kind: "missing" }
  | { kind: "empty" }
  | { kind: "error"; message: string };

type CalendarDay = { date: string; day: number; outside: boolean };

const STATUS_LABEL: Record<string, string> = {
  PENDING: "待处理",
  DATA_READY: "数据完成",
  RANKING_READY: "排行完成",
  PROMPT_READY: "Prompt 完成",
  IMAGE_READY: "图片完成",
  READY_TO_SEND: "待发送",
  SENT: "已发送",
  FAILED: "失败",
};

const WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"];

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

function localDate(year: number, month: number, day: number): string {
  return `${year}-${pad(month + 1)}-${pad(day)}`;
}

function currentMonth(): string {
  const now = new Date();
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}`;
}

function shiftMonth(month: string, delta: number): string {
  const [year, value] = month.split("-").map(Number);
  const next = new Date(year, value - 1 + delta, 1);
  return `${next.getFullYear()}-${pad(next.getMonth() + 1)}`;
}

function monthLabel(month: string): string {
  const [year, value] = month.split("-");
  return `${year} 年 ${Number(value)} 月`;
}

function calendarDays(month: string): CalendarDay[] {
  const [year, value] = month.split("-").map(Number);
  const monthIndex = value - 1;
  const firstWeekday = (new Date(year, monthIndex, 1).getDay() + 6) % 7;
  const firstCell = new Date(year, monthIndex, 1 - firstWeekday);
  return Array.from({ length: 42 }, (_, index) => {
    const date = new Date(firstCell.getFullYear(), firstCell.getMonth(), firstCell.getDate() + index);
    return {
      date: localDate(date.getFullYear(), date.getMonth(), date.getDate()),
      day: date.getDate(),
      outside: date.getMonth() !== monthIndex,
    };
  });
}

function runKey(run: V2Run): string {
  return `${run.group_name}::${run.run_date}::${String(run.updated_at || "")}`;
}

function displayText(value: unknown, fallback = "—"): string {
  if (typeof value === "string" && value.trim()) return value;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return fallback;
}

function displayCount(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value) ? String(value) : "—";
}

function statusTone(status: string): "success" | "warning" | "danger" | "info" | "neutral" {
  if (status === "SENT" || status === "IMAGE_READY" || status === "READY_TO_SEND") return "success";
  if (status === "FAILED") return "danger";
  if (status === "PENDING" || status === "DATA_READY" || status === "RANKING_READY") return "warning";
  if (status === "PROMPT_READY") return "info";
  return "neutral";
}

function statusLabel(status: string): string {
  return STATUS_LABEL[status] || status || "未知状态";
}

function safeError(error: unknown, fallback: string): string {
  const message = String(error || "")
    .replace(/^Error:\s*/i, "")
    .replace(/[A-Za-z]:\\[^,，。；;\n]+/g, "本机路径")
    .replace(/(^|\s)\/[^\s，。；;）)]+/g, "$1本机路径")
    .trim();
  if (!message) return fallback;
  return message.length > 180 ? `${message.slice(0, 180)}…` : message;
}

function isMissingError(error: unknown): boolean {
  return /404|不存在|not found|not_found/i.test(String(error || ""));
}

function fileLabel(file: string): string {
  if (file === "ranking.txt") return "排行榜文字";
  if (file === "image_prompt.txt") return "生图 Prompt";
  if (file === "image_prompt.original.txt") return "原始生图 Prompt";
  if (file === "daily_image.png") return "日报图片";
  if (file === "daily_image.previous.png") return "上一版日报图片";
  if (file === "ranking.json") return "排行榜数据";
  if (file === "messages.json") return "标准化消息";
  if (file === "run.json") return "运行状态";
  return file;
}

function formatRemovedAt(value: string | null): string {
  if (!value) return "历史遗留";
  return value.replace("T", " ").slice(0, 16);
}

function preferredGroup(groups: ArchiveGroup[]): ArchiveGroup | undefined {
  return [...groups].sort((a, b) => {
    const dateCompare = (b.run_dates[0] || "").localeCompare(a.run_dates[0] || "");
    return dateCompare || (a.group_id || Number.MAX_SAFE_INTEGER) - (b.group_id || Number.MAX_SAFE_INTEGER);
  })[0];
}

export default function Archive() {
  const { msg, toast } = useToast();
  const [groups, setGroups] = useState<ArchiveGroup[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [view, setView] = useState<ArchiveView>("active");
  const [selectedGroupKey, setSelectedGroupKey] = useState("");
  const [calendarMonth, setCalendarMonth] = useState(currentMonth());
  const [selectedDate, setSelectedDate] = useState("");
  const [selectedRunKey, setSelectedRunKey] = useState("");
  const [details, setDetails] = useState<Record<string, V2RunDetail>>({});
  const [detailErrors, setDetailErrors] = useState<Record<string, string>>({});
  const [activeFile, setActiveFile] = useState("");
  const [preview, setPreview] = useState<PreviewState>({ kind: "idle" });
  const [imageBroken, setImageBroken] = useState(false);
  const [restoringId, setRestoringId] = useState<number | null>(null);

  const loadArchive = async (): Promise<ArchiveGroup[]> => {
    setLoading(true);
    setLoadError("");
    try {
      const response = await getArchiveGroups();
      const next = Array.isArray(response.groups) ? response.groups : [];
      setGroups(next);
      setDetails({});
      setDetailErrors({});
      return next;
    } catch (error) {
      const message = safeError(error, "本地服务暂不可用");
      setLoadError(message);
      toast(`归档读取失败：${message}`);
      setGroups([]);
      return [];
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadArchive();
  }, []);

  const activeGroups = useMemo(() => groups.filter((group) => group.state === "active"), [groups]);
  const trashGroups = useMemo(() => groups.filter((group) => group.state !== "active"), [groups]);
  const visibleGroups = view === "active" ? activeGroups : trashGroups;
  const selectedGroup = groups.find((group) => group.archive_key === selectedGroupKey) || null;

  useEffect(() => {
    if (visibleGroups.some((group) => group.archive_key === selectedGroupKey)) return;
    const next = view === "active" ? preferredGroup(visibleGroups) : visibleGroups[0];
    setSelectedGroupKey(next?.archive_key || "");
  }, [selectedGroupKey, view, visibleGroups]);

  useEffect(() => {
    if (!selectedGroup) {
      setSelectedDate("");
      setSelectedRunKey("");
      return;
    }
    const latest = selectedGroup.run_dates[0] || "";
    setSelectedDate(latest);
    setCalendarMonth(latest ? latest.slice(0, 7) : currentMonth());
    setSelectedRunKey("");
    setActiveFile("");
  }, [selectedGroup?.archive_key]);

  const dateCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const run of selectedGroup?.runs || []) counts[run.run_date] = (counts[run.run_date] || 0) + 1;
    return counts;
  }, [selectedGroup]);

  const visibleRuns = useMemo(
    () => (selectedGroup?.runs || []).filter((run) => !selectedDate || run.run_date === selectedDate),
    [selectedDate, selectedGroup],
  );

  useEffect(() => {
    if (!visibleRuns.length) {
      setSelectedRunKey("");
      return;
    }
    if (!visibleRuns.some((run) => runKey(run) === selectedRunKey)) setSelectedRunKey(runKey(visibleRuns[0]));
  }, [selectedRunKey, visibleRuns]);

  const selectedRun = visibleRuns.find((run) => runKey(run) === selectedRunKey) || visibleRuns[0] || null;
  const selectedDetail = selectedRun ? details[runKey(selectedRun)] : undefined;
  const selectedFiles = selectedDetail?.files || [];

  useEffect(() => {
    if (!selectedRun) return;
    const key = runKey(selectedRun);
    if (details[key] || detailErrors[key]) return;
    let active = true;
    getRunDetail(selectedRun.group_name, selectedRun.run_date)
      .then((detail) => {
        if (active) setDetails((current) => ({ ...current, [key]: detail }));
      })
      .catch((error) => {
        if (active) setDetailErrors((current) => ({ ...current, [key]: safeError(error, "运行详情读取失败") }));
      });
    return () => {
      active = false;
    };
  }, [detailErrors, details, selectedRun]);

  useEffect(() => {
    if (!selectedFiles.length) {
      setActiveFile("");
      return;
    }
    if (!activeFile || !selectedFiles.includes(activeFile)) setActiveFile(selectedFiles[0]);
  }, [activeFile, selectedFiles]);

  useEffect(() => {
    if (!selectedRun || !activeFile) {
      setPreview({ kind: "idle" });
      return;
    }
    setImageBroken(false);
    if (activeFile === "daily_image.png" || activeFile === "daily_image.previous.png") {
      let active = true;
      setPreview({ kind: "loading" });
      fetch(getV2File(selectedRun.group_name, selectedRun.run_date, activeFile))
        .then(async (response) => {
          if (!response.ok) {
            const detail = await response.text();
            throw new Error(`读取 ${activeFile} 失败：${detail || `HTTP ${response.status}`}`);
          }
          return response.blob();
        })
        .then((blob) => {
          if (!active) return;
          if (blob.size === 0) setPreview({ kind: "empty" });
          else if (blob.type && !blob.type.startsWith("image/")) setPreview({ kind: "error", message: "图片文件类型无法识别" });
          else setPreview({ kind: "ready", content: "" });
        })
        .catch((error) => {
          if (active) setPreview(isMissingError(error) ? { kind: "missing" } : { kind: "error", message: safeError(error, "图片读取失败") });
        });
      return () => {
        active = false;
      };
    }

    let active = true;
    setPreview({ kind: "loading" });
    readV2TextFile(selectedRun.group_name, selectedRun.run_date, activeFile)
      .then((content) => {
        if (!active) return;
        if (!content.trim()) {
          setPreview({ kind: "empty" });
          return;
        }
        if (activeFile.endsWith(".json")) {
          try {
            setPreview({ kind: "ready", content: JSON.stringify(JSON.parse(content), null, 2) });
          } catch {
            setPreview({ kind: "error", message: `${activeFile} 不是有效的 JSON 文件` });
          }
          return;
        }
        setPreview({ kind: "ready", content });
      })
      .catch((error) => {
        if (active) setPreview(isMissingError(error) ? { kind: "missing" } : { kind: "error", message: safeError(error, "文件读取失败") });
      });
    return () => {
      active = false;
    };
  }, [activeFile, selectedRun]);

  const switchView = (next: ArchiveView) => {
    setView(next);
    const candidates = next === "active" ? activeGroups : trashGroups;
    const preferred = next === "active" ? preferredGroup(candidates) : candidates[0];
    setSelectedGroupKey(preferred?.archive_key || "");
  };

  const restoreSelectedGroup = async () => {
    if (!selectedGroup?.group_id || selectedGroup.state !== "deleted" || restoringId !== null) return;
    setRestoringId(selectedGroup.group_id);
    try {
      await restoreGroup(selectedGroup.group_id);
      const restoredKey = selectedGroup.archive_key;
      await loadArchive();
      setView("active");
      setSelectedGroupKey(restoredKey);
      toast(`已恢复「${selectedGroup.display_name}」，当前保持停用`);
    } catch (error) {
      toast(`恢复失败：${safeError(error, "请稍后重试")}`);
    } finally {
      setRestoringId(null);
    }
  };

  const monthDays = useMemo(() => calendarDays(calendarMonth), [calendarMonth]);
  const selectedDetailError = selectedRun ? detailErrors[runKey(selectedRun)] : "";

  return (
    <div className="archive-page">
      <PageHeader
        title="归档中心"
        description="按群聊和日期查看历史归档；移除的群会保留在回收站。"
        actions={<Button tone="secondary" onClick={() => void loadArchive()} busy={loading}><ArrowClockwise size={16} />刷新归档</Button>}
      />

      <section className="archive-group-catalog card" aria-label="归档群聊">
        <div className="archive-catalog-head">
          <div><h2>群聊归档</h2><p>选择群聊后，可通过月历查看有归档的日期。</p></div>
          <div className="archive-view-tabs" role="tablist" aria-label="群聊归档分类">
            <button type="button" role="tab" aria-selected={view === "active"} className={view === "active" ? "is-active" : ""} onClick={() => switchView("active")}>
              <ArchiveIcon size={17} />当前群聊 <span>{activeGroups.length}</span>
            </button>
            <button type="button" role="tab" aria-selected={view === "trash"} className={view === "trash" ? "is-active" : ""} onClick={() => switchView("trash")}>
              <Trash size={17} />回收站 <span>{trashGroups.length}</span>
            </button>
          </div>
        </div>

        {loading && groups.length === 0 && <LoadingState label="正在读取群聊归档…" />}
        {!loading && loadError && groups.length === 0 && <div className="archive-error" role="alert"><WarningCircle size={18} />{loadError}</div>}
        {!loading && !loadError && visibleGroups.length === 0 && (
          <EmptyState
            title={view === "active" ? "暂无群聊任务" : "回收站为空"}
            description={view === "active" ? "在群聊任务中添加群后，这里会立即出现对应群聊。" : "删除群聊任务后，群配置和历史归档会保留在这里。"}
          />
        )}
        {visibleGroups.length > 0 && (
          <div className="archive-group-buttons">
            {visibleGroups.map((group) => (
              <button
                type="button"
                className={`archive-group-button ${selectedGroupKey === group.archive_key ? "is-selected" : ""}`}
                key={group.archive_key}
                aria-pressed={selectedGroupKey === group.archive_key}
                onClick={() => setSelectedGroupKey(group.archive_key)}
              >
                <span className="archive-group-button-main"><strong>{group.display_name}</strong><small>{group.wechat_group_id || "未记录微信群 ID"}</small></span>
                <span className="archive-group-button-meta">
                  <b>{group.run_count} 条归档</b>
                  {group.state === "active" && !group.enabled && <em>已停用</em>}
                  {group.state === "deleted" && <em>已移除</em>}
                  {group.state === "orphaned" && <em>历史遗留</em>}
                </span>
              </button>
            ))}
          </div>
        )}
      </section>

      {selectedGroup && (
        <>
          <section className="archive-selected-group card">
            <div>
              <p className="eyebrow">{selectedGroup.state === "active" ? "当前群聊" : selectedGroup.state === "deleted" ? "回收站群聊" : "历史遗留归档"}</p>
              <h2>{selectedGroup.display_name}</h2>
              <p>{selectedGroup.state === "active" ? `${selectedGroup.run_count} 条历史归档${selectedGroup.enabled ? "" : " · 当前任务已停用"}` : `${selectedGroup.run_count} 条历史归档 · ${formatRemovedAt(selectedGroup.deleted_at)}`}</p>
            </div>
            {selectedGroup.state === "deleted" && selectedGroup.group_id && (
              <Button tone="secondary" onClick={() => void restoreSelectedGroup()} busy={restoringId === selectedGroup.group_id}>恢复群聊</Button>
            )}
          </section>

          <div className="archive-browser-layout">
            <section className="archive-calendar card" aria-label={`${selectedGroup.display_name} 归档日历`}>
              <div className="archive-calendar-head">
                <button type="button" aria-label="上个月" onClick={() => setCalendarMonth(shiftMonth(calendarMonth, -1))}><CaretLeft size={18} /></button>
                <div><CalendarBlank size={18} /><strong>{monthLabel(calendarMonth)}</strong></div>
                <button type="button" aria-label="下个月" onClick={() => setCalendarMonth(shiftMonth(calendarMonth, 1))}><CaretRight size={18} /></button>
              </div>
              <div className="archive-calendar-actions">
                <button type="button" className={!selectedDate ? "is-active" : ""} onClick={() => setSelectedDate("")}>查看全部</button>
                <span>{selectedDate ? `已筛选 ${selectedDate}` : `${selectedGroup.run_count} 条归档`}</span>
              </div>
              <div className="archive-calendar-weekdays" aria-hidden="true">{WEEKDAYS.map((day) => <span key={day}>{day}</span>)}</div>
              <div className="archive-calendar-grid">
                {monthDays.map((item) => {
                  const count = dateCounts[item.date] || 0;
                  return (
                    <button
                      type="button"
                      key={item.date}
                      className={`${item.outside ? "is-outside" : ""} ${count ? "has-archive" : ""} ${selectedDate === item.date ? "is-selected" : ""}`}
                      disabled={item.outside || count === 0}
                      aria-label={count ? `${item.date}，${count} 条归档` : `${item.date}，无归档`}
                      onClick={() => setSelectedDate(item.date)}
                    >
                      <span>{item.day}</span>
                      {count > 0 && <small>{count}</small>}
                    </button>
                  );
                })}
              </div>
            </section>

            <section className="archive-detail card" aria-label="归档详情">
              {selectedGroup.run_count === 0 && <EmptyState title="该群尚无归档" description="完成第一次群聊任务后，归档日期和文件会显示在这里。" />}
              {selectedGroup.run_count > 0 && visibleRuns.length === 0 && <EmptyState title="该日期没有归档" description="请选择日历中带标记的日期，或点击“查看全部”。" />}
              {visibleRuns.length > 0 && (
                <>
                  <div className="archive-run-picker" aria-label="运行归档列表">
                    {visibleRuns.map((run, index) => (
                      <button type="button" className={runKey(run) === selectedRunKey ? "is-selected" : ""} key={`${runKey(run)}:${index}`} onClick={() => { setSelectedRunKey(runKey(run)); setActiveFile(""); }}>
                        <span><strong>{displayText(run.run_date)}</strong><small>{displayText(run.period_start, "未记录统计开始时间")}</small></span>
                        <StatusBadge tone={statusTone(displayText(run.status, ""))}>{statusLabel(displayText(run.status, "未知状态"))}</StatusBadge>
                      </button>
                    ))}
                  </div>

                  {selectedRun && (
                    <>
                      <div className="archive-detail-head">
                        <div><p className="eyebrow">运行归档</p><h2>{selectedGroup.display_name} · {displayText(selectedRun.run_date)}</h2></div>
                        <StatusBadge tone={statusTone(displayText(selectedRun.status, ""))}>{statusLabel(displayText(selectedRun.status, "未知状态"))}</StatusBadge>
                      </div>
                      <div className="archive-summary-grid">
                        <div><span>统计周期</span><strong>{displayText(selectedRun.period_start)} ~ {displayText(selectedRun.period_end)}</strong></div>
                        <div><span>消息数</span><strong>{displayCount(selectedRun.message_count)}</strong></div>
                        <div><span>发言人数</span><strong>{displayCount(selectedRun.speaker_count)}</strong></div>
                        <div><span>发送时间</span><strong>{displayText(selectedRun.sent_at)}</strong></div>
                      </div>
                      {(selectedRun.error || selectedRun.image_error || selectedDetailError) && <div className="archive-error" role="alert"><WarningCircle size={18} />{safeError(selectedRun.error || selectedRun.image_error || selectedDetailError, "运行详情读取失败")}</div>}
                      {!selectedDetail && !selectedDetailError && <LoadingState label="正在读取归档文件…" />}
                      {selectedDetail && (
                        <>
                          <div className="archive-files-heading"><div><h3>归档文件</h3><p>只展示安全文件名，不展示本机绝对路径。</p></div></div>
                          {selectedFiles.length === 0 && <EmptyState title="暂无文件记录" description="该运行尚未产生可预览产物。" />}
                          {selectedFiles.length > 0 && (
                            <div className="archive-file-layout">
                              <div className="archive-file-list" aria-label="归档文件列表">
                                {selectedFiles.map((file) => (
                                  <button type="button" key={file} className={`archive-file-button ${activeFile === file ? "is-selected" : ""}`} onClick={() => setActiveFile(file)} aria-pressed={activeFile === file}>
                                    {file.endsWith(".png") ? <ImageSquare size={17} aria-hidden="true" /> : <FileText size={17} aria-hidden="true" />}
                                    <span>{fileLabel(file)}</span><small>{file}</small>
                                  </button>
                                ))}
                              </div>
                              <div className="archive-preview" aria-live="polite">
                                <div className="archive-preview-title">{activeFile ? fileLabel(activeFile) : "预览"}<span>{activeFile}</span></div>
                                {preview.kind === "loading" && <LoadingState label="正在读取文件…" />}
                                {preview.kind === "missing" && <EmptyState title="文件不存在" description="归档记录存在，但该文件当前不可用。" />}
                                {preview.kind === "empty" && <EmptyState title="文件为空" description="该文件存在，但没有可展示内容。" />}
                                {preview.kind === "error" && <div className="archive-error" role="alert"><WarningCircle size={18} />{preview.message}</div>}
                                {preview.kind === "ready" && activeFile.endsWith(".png") && !imageBroken && <img className="archive-preview-image" src={getV2File(selectedRun.group_name, selectedRun.run_date, activeFile)} alt={`${selectedGroup.display_name} ${selectedRun.run_date} ${fileLabel(activeFile)}`} onError={() => setImageBroken(true)} />}
                                {preview.kind === "ready" && activeFile.endsWith(".png") && imageBroken && <EmptyState title="图片读取失败" description="图片文件不存在、为空或当前无法识别。" />}
                                {preview.kind === "ready" && !activeFile.endsWith(".png") && <pre className="archive-preview-text">{preview.content}</pre>}
                              </div>
                            </div>
                          )}
                        </>
                      )}
                    </>
                  )}
                </>
              )}
            </section>
          </div>
        </>
      )}
      <Toast message={msg} />
    </div>
  );
}
