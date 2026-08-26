import { useEffect, useMemo, useState } from "react";
import {
  ArrowsClockwise,
  ChartBar,
  WarningCircle,
} from "@phosphor-icons/react";
import {
  getRunDetail,
  getRuns,
  readV2JsonFile,
  readV2TextFile,
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
import { TemplateEditor } from "./Templates";
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

interface RankingSpeaker {
  rank: number;
  name: string;
  count: number;
}

interface RankingSummary {
  groupName: string;
  periodStart: string;
  periodEnd: string;
  messageCount: number | null;
  speakerCount: number | null;
  topSpeakers: RankingSpeaker[];
}

interface RankingDetail {
  run: V2Run;
  files: string[];
  summary: RankingSummary | null;
  rankingText: string;
  jsonError: string;
  textError: string;
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

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function asText(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function asCount(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value) && value >= 0) return Math.round(value);
  return null;
}

function parseRanking(value: unknown): { summary: RankingSummary | null; error: string } {
  const record = asRecord(value);
  if (!record) return { summary: null, error: "ranking.json 不是对象格式，无法解析排行榜。" };
  const rawSpeakers = record.top_speakers;
  if (!Array.isArray(rawSpeakers)) {
    return { summary: null, error: "ranking.json 缺少有效的 top_speakers 数组。" };
  }
  const topSpeakers: RankingSpeaker[] = [];
  let malformed = false;
  rawSpeakers.forEach((item, index) => {
    const speaker = asRecord(item);
    const name = speaker ? asText(speaker.name).trim() : "";
    const count = speaker ? asCount(speaker.count) : null;
    if (!name || count === null) {
      malformed = true;
      return;
    }
    const rank = asCount(speaker?.rank) || index + 1;
    topSpeakers.push({ rank, name, count });
  });
  return {
    summary: {
      groupName: asText(record.group_name),
      periodStart: asText(record.period_start),
      periodEnd: asText(record.period_end),
      messageCount: asCount(record.message_count),
      speakerCount: asCount(record.speaker_count),
      topSpeakers,
    },
    error: malformed ? "ranking.json 中有部分排行项格式异常，已跳过异常项。" : "",
  };
}

function runKey(run: V2Run): string {
  return `${run.group_name}\u0000${run.run_date}`;
}

function formatDateTime(value: unknown): string {
  if (!value) return "—";
  return String(value).replace("T", " ").slice(0, 16);
}

export default function Ranking() {
  const { msg, toast } = useToast();
  const [runs, setRuns] = useState<V2Run[]>([]);
  const [dateFilter, setDateFilter] = useState(shanghaiDateInputValue);
  const [groupFilter, setGroupFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [selectedKey, setSelectedKey] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [detail, setDetail] = useState<RankingDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const loadRuns = () => {
    setLoading(true);
    setError("");
    getRuns(dateFilter || undefined)
      .then((data) => {
        setRuns(data.runs);
        setSelectedKey((current) => data.runs.some((run) => runKey(run) === current) ? current : data.runs[0] ? runKey(data.runs[0]) : "");
      })
      .catch((reason: unknown) => {
        setError(`运行记录加载失败：${String(reason)}`);
        toast(`运行记录加载失败：${String(reason)}`);
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    loadRuns();
    // 日期筛选变化时重新读取后端运行记录。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dateFilter]);

  const filteredRuns = useMemo(() => {
    const query = groupFilter.trim().toLocaleLowerCase();
    return runs.filter((run) => {
      const matchesGroup = !query || run.group_name.toLocaleLowerCase().includes(query);
      const matchesStatus = statusFilter === "all" || run.status.toUpperCase() === statusFilter;
      return matchesGroup && matchesStatus;
    });
  }, [groupFilter, runs, statusFilter]);

  useEffect(() => {
    if (filteredRuns.length === 0) {
      setSelectedKey("");
      return;
    }
    if (!filteredRuns.some((run) => runKey(run) === selectedKey)) {
      setSelectedKey(runKey(filteredRuns[0]));
    }
  }, [filteredRuns, selectedKey]);

  useEffect(() => {
    if (!selectedKey) {
      setDetail(null);
      return;
    }
    const selected = runs.find((run) => runKey(run) === selectedKey);
    if (!selected) return;
    setDetailLoading(true);
    setDetail(null);
    getRunDetail(selected.group_name, selected.run_date)
      .then((result) => {
        const jsonRequest = result.files.includes("ranking.json")
          ? readV2JsonFile<unknown>(selected.group_name, selected.run_date, "ranking.json")
          : Promise.resolve(null);
        const textRequest = result.files.includes("ranking.txt")
          ? readV2TextFile(selected.group_name, selected.run_date, "ranking.txt")
          : Promise.resolve("");
        return Promise.allSettled([jsonRequest, textRequest]).then(([jsonResult, textResult]) => {
          let summary: RankingSummary | null = null;
          let jsonError = result.files.includes("ranking.json") ? "" : "未找到 ranking.json，无法显示结构化排行。";
          let rankingText = "";
          let textError = result.files.includes("ranking.txt") ? "" : "未找到 ranking.txt，无法显示排行榜文案。";
          if (jsonResult.status === "fulfilled" && jsonResult.value !== null) {
            const parsed = parseRanking(jsonResult.value);
            summary = parsed.summary;
            jsonError = parsed.error;
          } else if (jsonResult.status === "rejected") {
            jsonError = String(jsonResult.reason);
          }
          if (textResult.status === "fulfilled") {
            rankingText = textResult.value;
            if (!rankingText.trim() && result.files.includes("ranking.txt")) textError = "ranking.txt 文件为空。";
          } else {
            textError = String(textResult.reason);
          }
          setDetail({ run: result.run, files: result.files, summary, rankingText, jsonError, textError });
        });
      })
      .catch((reason: unknown) => toast(`运行详情加载失败：${String(reason)}`))
      .finally(() => setDetailLoading(false));
  }, [runs, selectedKey, toast]);

  if (loading && runs.length === 0) return <LoadingState label="正在加载真实运行记录…" />;
  if (error && runs.length === 0) {
    return <EmptyState title="排行榜加载失败" description={error} action={<Button tone="secondary" onClick={loadRuns}>重新加载</Button>} />;
  }

  return (
    <div className="ranking-page">
      <PageHeader
        title="排行榜"
        description="查看真实运行记录中的排行榜 JSON 与文案，并管理排行榜模板。"
        actions={<Button tone="ghost" onClick={loadRuns} busy={loading}><ArrowsClockwise size={17} aria-hidden="true" />刷新记录</Button>}
      />

      <section className="ranking-filter-bar" aria-label="排行榜运行记录筛选">
        <label><span>运行日期</span><input type="date" value={dateFilter} onChange={(event) => setDateFilter(event.target.value)} /></label>
        <label><span>群名</span><input type="search" value={groupFilter} placeholder="搜索群名" onChange={(event) => setGroupFilter(event.target.value)} /></label>
        <label><span>状态</span><select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}><option value="all">全部状态</option>{Object.entries(STATUS_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <span className="ranking-filter-count">显示 {filteredRuns.length} / {runs.length} 条</span>
      </section>

      <div className="ranking-workspace">
        <section className="ranking-run-list" aria-label="运行记录列表">
          <div className="ranking-section-head"><div><h2>运行记录</h2><p>选择一条真实记录查看产物。</p></div><ChartBar size={22} aria-hidden="true" /></div>
          {filteredRuns.length === 0 ? <EmptyState title="没有匹配的运行记录" description="请调整日期、群名或状态筛选。" /> : (
            <div className="ranking-run-items">
              {filteredRuns.map((run) => (
                <button type="button" key={runKey(run)} className={`ranking-run-item ${selectedKey === runKey(run) ? "is-active" : ""}`} onClick={() => setSelectedKey(runKey(run))} aria-pressed={selectedKey === runKey(run)}>
                  <div className="ranking-run-item-head"><strong>{run.group_name || "未命名群"}</strong><StatusPill status={run.status} /></div>
                  <div className="ranking-run-item-meta"><span>{run.run_date}</span><span>更新 {formatDateTime(run.updated_at)}</span></div>
                </button>
              ))}
            </div>
          )}
        </section>

        <section className="ranking-detail-panel" aria-label="排行榜运行详情">
          <ContentSwap swapKey={detailLoading ? "loading" : detail ? selectedKey : "empty"}>
            {detailLoading ? <LoadingState label="正在读取排行榜产物…" /> : !detail ? <EmptyState title="请选择运行记录" description="从左侧选择一条运行记录，读取真实 ranking.json 与 ranking.txt。" /> : (
            <>
              <div className="ranking-detail-head">
                <div><span className="ranking-eyebrow">真实运行产物</span><h2>{detail.run.group_name} · {detail.run.run_date}</h2><p>状态 <StatusPill status={detail.run.status} /> · 更新时间 {formatDateTime(detail.run.updated_at)}</p></div>
              </div>
              <div className="ranking-summary-grid">
                <div><span>统计周期</span><strong>{detail.summary?.periodStart || detail.run.period_start || "—"} ~ {detail.summary?.periodEnd || detail.run.period_end || "—"}</strong></div>
                <div><span>消息数</span><strong>{detail.summary?.messageCount ?? (typeof detail.run.message_count === "number" ? detail.run.message_count : "—")}</strong></div>
                <div><span>发言人数</span><strong>{detail.summary?.speakerCount ?? (typeof detail.run.speaker_count === "number" ? detail.run.speaker_count : "—")}</strong></div>
              </div>
              {(detail.jsonError || detail.textError) && <div className="ranking-file-warning"><WarningCircle size={18} aria-hidden="true" /><div>{detail.jsonError && <p>{detail.jsonError}</p>}{detail.textError && <p>{detail.textError}</p>}</div></div>}
              <div className="ranking-detail-columns">
                <div className="ranking-top-list"><div className="ranking-content-heading"><h3>Top 排名</h3><span>来自 ranking.json</span></div>{detail.summary?.topSpeakers.length ? <ol>{detail.summary.topSpeakers.map((speaker) => <li key={`${speaker.rank}-${speaker.name}`}><span className="ranking-rank">{speaker.rank}</span><strong>{speaker.name}</strong><span>{speaker.count} 条</span></li>)}</ol> : <EmptyState title="暂无结构化排行" description="ranking.json 缺失、为空或无法解析。" />}</div>
                <div className="ranking-text-block"><div className="ranking-content-heading"><h3>排行榜文案</h3><span>来自 ranking.txt</span></div>{detail.rankingText.trim() ? <pre>{detail.rankingText}</pre> : <EmptyState title="暂无排行榜文案" description="真实 ranking.txt 缺失或为空。" />}</div>
              </div>
              {detail.run.error && <div className="ranking-run-error">任务错误：{String(detail.run.error)}</div>}
            </>
            )}
          </ContentSwap>
        </section>
      </div>

      <section className="ranking-template-section"><TemplateEditor kind="ranking" /></section>
      <Toast message={msg} />
    </div>
  );
}
