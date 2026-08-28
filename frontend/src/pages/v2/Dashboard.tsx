import { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowsClockwise,
  CheckCircle,
  GearSix,
  ImageSquare,
  PaperPlaneTilt,
  WarningCircle,
} from "@phosphor-icons/react";
import {
  DashboardCard,
  getDashboard,
  getSystemHealth,
  pipelineGenerate,
  pipelineSend,
  resolveManualSend,
  resolvePromptUnknown,
} from "../../api";
import {
  Button,
  ConfirmDialog,
  EmptyState,
  ImagePreviewTrigger,
  ImageViewer,
  LoadingState,
  PageHeader,
  StatusBadge,
  Toast,
} from "../../components/common";
import { useFetch, useToast } from "../../components/ui";
import { navigateToHash } from "../../navigation";
import { shanghaiDateInputValue } from "../../date";
import { AnimatePresence, m, MOTION_EASE } from "../../components/motion";
import {
  formatRankingCount,
  INTERACTION_EXPLANATION,
  isTextPrimaryRanking,
} from "./rankingPolicy";

const STATUS_META: Record<string, { label: string; tone: "success" | "warning" | "danger" | "info" | "neutral" }> = {
  PENDING: { label: "待生成", tone: "warning" },
  DATA_READY: { label: "数据就绪", tone: "warning" },
  RANKING_READY: { label: "排行完成", tone: "warning" },
  PROMPT_READY: { label: "Prompt 完成", tone: "info" },
  IMAGE_READY: { label: "图片完成", tone: "success" },
  READY_TO_SEND: { label: "待发送", tone: "success" },
  SENT: { label: "已发送", tone: "neutral" },
  FAILED: { label: "失败", tone: "danger" },
};

function StatusPill({ status }: { status: string }) {
  const meta = STATUS_META[status.toUpperCase()] || { label: status || "未知", tone: "neutral" as const };
  return <StatusBadge tone={meta.tone}>{meta.label}</StatusBadge>;
}

function formatDateTime(value: string): string {
  if (!value) return "—";
  return value.replace("T", " ").slice(0, 16);
}

function formatPeriod(card: DashboardCard): string {
  if (!card.period_start || !card.period_end) return "周期尚未确定";
  return `${card.period_start.slice(0, 10)} ~ ${card.period_end.slice(0, 10)}`;
}

interface ViewerImage {
  src: string;
  alt: string;
  filename: string;
  title: string;
}

function ImagePreview({ card, onOpen }: { card: DashboardCard; onOpen: (image: ViewerImage) => void }) {
  const [imageBroken, setImageBroken] = useState(false);

  if (card.image_url) {
    const alt = `${card.group_name} 日报图片`;
    if (!imageBroken) {
      return (
        <ImagePreviewTrigger
          src={card.image_url}
          alt={alt}
          imageClassName="dashboard-task-image"
          className="dashboard-task-image-trigger"
          onError={() => setImageBroken(true)}
          onOpen={() => onOpen({ src: card.image_url, alt, filename: "daily_image.png", title: alt })}
        />
      );
    }
  }
  return (
    <div className="dashboard-image-empty">
      <ImageSquare size={28} aria-hidden="true" />
      <span>{imageBroken ? "图片读取失败" : "暂未生成真实图片"}</span>
    </div>
  );
}

interface TaskActionsProps {
  card: DashboardCard;
  generating: boolean;
  sending: boolean;
  senderOk: boolean;
  senderDetail: string;
  onGenerate: () => void;
  onResolvePrompt: () => void;
  onSend: () => void;
  onResolve: () => void;
}

function TaskActions({
  card,
  generating,
  sending,
  senderOk,
  senderDetail,
  onGenerate,
  onResolvePrompt,
  onSend,
  onResolve,
}: TaskActionsProps) {
  const canGenerate = card.status !== "SENT" && !card.prompt_hold;
  const canResolvePrompt = card.prompt_hold && card.prompt_hold_reason === "PROMPT_RESULT_UNKNOWN";
  const canSend = ["IMAGE_READY", "READY_TO_SEND"].includes(card.status) && !card.sent_at && !card.send_hold;

  return (
    <div className="dashboard-task-actions">
      <Button tone="ghost" className="ui-button-compact" onClick={() => navigateToHash(`/groups/${card.group_id}`)}>
        <GearSix size={16} aria-hidden="true" />
        查看配置
      </Button>
      {canGenerate && (
        <Button
          tone="secondary"
          className="ui-button-compact"
          onClick={onGenerate}
          busy={generating}
          title={generating ? "正在生成中，请耐心等待" : "立即生成当前选择日期的日报"}
        >
          {generating ? "生成中…" : "立即生成"}
        </Button>
      )}
      {canResolvePrompt && (
        <Button tone="secondary" className="ui-button-compact" onClick={onResolvePrompt}>
          <WarningCircle size={16} aria-hidden="true" />
          核对后重试
        </Button>
      )}
      {card.prompt_hold && !canResolvePrompt && (
        <Button tone="ghost" className="ui-button-compact" disabled title="当前 Prompt 暂停原因需要人工检查运行记录">
          <WarningCircle size={16} aria-hidden="true" />
          Prompt 待复核
        </Button>
      )}
      {canSend && senderOk && (
        <Button tone="primary" className="ui-button-compact" onClick={onSend} busy={sending}>
          <PaperPlaneTilt size={16} aria-hidden="true" />
          立即发送
        </Button>
      )}
      {canSend && !senderOk && (
        <Button
          tone="ghost"
          className="ui-button-compact"
          disabled
          title={senderDetail || "微信自动发送服务不可用"}
        >
          微信发送未启用
        </Button>
      )}
      {card.send_hold && (
        <Button tone="secondary" className="ui-button-compact" onClick={onResolve}>
          <WarningCircle size={16} aria-hidden="true" />
          人工核对
        </Button>
      )}
    </div>
  );
}

export default function Dashboard() {
  const [runDate, setRunDate] = useState(shanghaiDateInputValue);
  const dashboard = useFetch(() => getDashboard(runDate), [runDate]);
  const health = useFetch(getSystemHealth);
  const { msg, toast } = useToast();
  const [generatingId, setGeneratingId] = useState<number | null>(null);
  const [sendingId, setSendingId] = useState<number | null>(null);
  const [sendCard, setSendCard] = useState<DashboardCard | null>(null);
  const [promptRetryCard, setPromptRetryCard] = useState<DashboardCard | null>(null);
  const [viewerImage, setViewerImage] = useState<ViewerImage | null>(null);
  const [manualCard, setManualCard] = useState<DashboardCard | null>(null);
  const [manualResolution, setManualResolution] = useState<"all_sent" | "text_sent" | "not_sent">("all_sent");
  const [manualBusy, setManualBusy] = useState(false);
  const [healthOpen, setHealthOpen] = useState(false);
  const manualDialogRef = useRef<HTMLElement>(null);
  const manualReturnFocusRef = useRef<HTMLElement | null>(null);
  const manualBusyRef = useRef(manualBusy);
  manualBusyRef.current = manualBusy;

  useEffect(() => {
    if (!manualCard) return;
    manualReturnFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const frame = window.requestAnimationFrame(() => manualDialogRef.current?.focus());
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !manualBusyRef.current) {
        event.preventDefault();
        setManualCard(null);
        return;
      }
      if (event.key !== "Tab" || !manualDialogRef.current) return;
      const focusable = Array.from(
        manualDialogRef.current.querySelectorAll<HTMLElement>(
          "button:not(:disabled), input:not(:disabled), textarea:not(:disabled), select:not(:disabled), [tabindex]:not([tabindex='-1'])",
        ),
      );
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      window.cancelAnimationFrame(frame);
      document.removeEventListener("keydown", onKeyDown);
      window.requestAnimationFrame(() => manualReturnFocusRef.current?.focus());
    };
  }, [manualCard]);

  const refresh = () => {
    dashboard.reload();
    health.reload();
  };

  const handleGenerate = (card: DashboardCard) => {
    if (generatingId !== null) return;
    setGeneratingId(card.group_id);
    toast(`正在生成 ${runDate.slice(5)} 的「${card.group_name}」日报，请稍候…`);
    pipelineGenerate({ group_id: card.group_id, force: true, run_date: runDate })
      .then((response) => {
        const result = response.results?.[0];
        if (!result) {
          toast("生成接口未返回结果");
        } else if (result.status === "skipped") {
          toast(`未生成：${result.detail || "该日期不生成日报"}`);
        } else if (["failed", "error"].includes(result.status)) {
          toast(`生成失败：${result.detail || result.error_type || result.status}`);
        } else {
          toast(`生成完成：${result.detail || result.status}`);
        }
        dashboard.reload();
      })
      .catch((error: unknown) => toast(`生成出错：${String(error)}`))
      .finally(() => setGeneratingId(null));
  };

  const handleSend = (card: DashboardCard) => {
    if (sendingId !== null) return;
    setSendCard(card);
  };

  const confirmSend = () => {
    if (!sendCard || sendingId !== null) return;
    setSendingId(sendCard.group_id);
    pipelineSend({ group_id: sendCard.group_id, run_date: runDate })
      .then((response) => {
        const result = response.result;
        if (!result || !["sent", "SENT"].includes(result.status)) {
          toast(`发送未完成：${result?.detail || result?.error || result?.error_type || result?.status || "未知状态"}`);
        } else {
          toast(`「${sendCard.group_name}」已发送`);
        }
        dashboard.reload();
      })
      .catch((error: unknown) => toast(`发送出错：${String(error)}`))
      .finally(() => {
        setSendingId(null);
        setSendCard(null);
      });
  };

  const confirmPromptRetry = () => {
    if (!promptRetryCard || generatingId !== null) return;
    const card = promptRetryCard;
    setGeneratingId(card.group_id);
    toast(`正在解除「${card.group_name}」的 Prompt 暂停并重新生成…`);
    resolvePromptUnknown({
      group_id: card.group_id,
      run_date: runDate,
      expected_operation_id: card.prompt_operation_id,
    })
      .then(() => pipelineGenerate({ group_id: card.group_id, force: true, run_date: runDate }))
      .then((response) => {
        const result = response.results?.[0];
        if (!result || ["failed", "error", "blocked"].includes(result.status)) {
          toast(`生成未完成：${result?.detail || result?.error_type || result?.status || "未知状态"}`);
        } else {
          toast(`生成完成：${result.detail || result.status}`);
        }
        dashboard.reload();
      })
      .catch((error: unknown) => toast(`Prompt 核对或重试失败：${String(error)}`))
      .finally(() => {
        setGeneratingId(null);
        setPromptRetryCard(null);
      });
  };

  const confirmManualResolution = () => {
    if (!manualCard || manualBusy) return;
    setManualBusy(true);
    resolveManualSend({
      group_id: manualCard.group_id,
      run_date: runDate,
      resolution: manualResolution,
      expected_updated_at: manualCard.updated_at,
    })
      .then((response) => {
        toast(response.result.detail);
        setManualCard(null);
        dashboard.reload();
      })
      .catch((error: unknown) => toast(`人工核对失败：${String(error)}`))
      .finally(() => setManualBusy(false));
  };

  const totalMessages = useMemo(
    () => dashboard.data?.cards.reduce((total, card) => total + Number(card.message_count || 0), 0) || 0,
    [dashboard.data]
  );
  const totalSpeakers = useMemo(
    () => dashboard.data?.cards.reduce((total, card) => total + Number(card.speaker_count || 0), 0) || 0,
    [dashboard.data]
  );
  const maxMessages = useMemo(
    () => Math.max(1, ...(dashboard.data?.cards.map((card) => Number(card.message_count || 0)) || [])),
    [dashboard.data]
  );
  const healthChecks = health.data ? Object.values(health.data.checks) : [];
  const healthProblems = health.data
    ? Object.entries(health.data.checks).filter(([, check]) => !check.ok)
    : [];
  const systemHealthy = healthChecks.length > 0 && healthChecks.every((check) => check.ok);
  const senderDetail = health.data?.checks?.wechat_sender?.detail || health.error || "微信自动发送服务不可用";

  if (dashboard.loading && !dashboard.data) return <LoadingState label="正在加载今日运行总览…" />;
  if (dashboard.error && !dashboard.data) {
    return (
      <EmptyState
        title="总览加载失败"
        description={dashboard.error}
        action={<Button tone="secondary" onClick={refresh}>重新加载</Button>}
      />
    );
  }
  if (!dashboard.data) return <EmptyState title="暂无总览数据" description="接口尚未返回可展示的数据。" />;

  const data = dashboard.data;
  const counts = data.counts;
  const dailyStatusMeta = {
    not_started: { label: "今日未开始", tone: "neutral" as const },
    running: { label: "今日运行中", tone: "info" as const },
    complete: { label: "今日已完成", tone: "success" as const },
    partial: { label: "今日部分完成", tone: "warning" as const },
    blocked: { label: "今日已阻断", tone: "danger" as const },
    needs_attention: { label: "今日待核对", tone: "danger" as const },
  }[data.daily_status?.overall_status || "not_started"];

  return (
    <div className="dashboard-page">
      <PageHeader
        title="运行总览"
        description={`${data.today} · 统计周期 ${data.period_start} ~ ${data.period_end}${!data.should_run ? " · 今天不生成" : ""}`}
        actions={
          <>
            <StatusBadge tone={dailyStatusMeta.tone}>{dailyStatusMeta.label}</StatusBadge>
            <button
              type="button"
              className="dashboard-health-trigger"
              aria-expanded={healthOpen}
              aria-controls="dashboard-health-details"
              onClick={() => setHealthOpen((value) => !value)}
            >
              <StatusBadge tone={health.loading ? "neutral" : systemHealthy ? "success" : "warning"}>
                {health.loading ? "健康检查中" : systemHealthy ? "系统健康" : "需要关注"}
              </StatusBadge>
            </button>
            <label className="dashboard-date-field">
              <span>运行日期</span>
              <input
                type="date"
                value={runDate}
                onChange={(event) => setRunDate(event.target.value || shanghaiDateInputValue())}
                aria-label="运行日期"
              />
            </label>
            <Button tone="ghost" onClick={refresh} busy={dashboard.loading}>
              <ArrowsClockwise size={17} aria-hidden="true" />
              刷新
            </Button>
          </>
        }
      />

      {healthOpen && (
        <section id="dashboard-health-details" className="dashboard-health-details" aria-live="polite">
          <strong>{systemHealthy ? "所有系统检查均正常" : `有 ${healthProblems.length || 1} 项检查需要关注`}</strong>
          {health.error && <p>{health.error}</p>}
          {healthProblems.map(([name, check]) => (
            <p key={name}><b>{name}</b><span>{check.detail || check.status}</span></p>
          ))}
          {health.data?.warnings?.map((warning) => <p key={warning}><b>运行提醒</b><span>{warning}</span></p>)}
        </section>
      )}

      <section className="dashboard-kpis" aria-label="运行统计">
        <div className="dashboard-kpi-card">
          <span className="dashboard-kpi-label">启用群数</span>
          <strong>{data.enabled_groups}</strong>
          <span className="dashboard-kpi-note">来自当前启用配置</span>
        </div>
        <div className="dashboard-kpi-card">
          <span className="dashboard-kpi-label">周期消息总数</span>
          <strong>{totalMessages}</strong>
          <span className="dashboard-kpi-note">按各群真实 message_count 合计</span>
        </div>
        <div className="dashboard-kpi-card">
          <span className="dashboard-kpi-label">发言人数合计</span>
          <strong>{totalSpeakers}</strong>
          <span className="dashboard-kpi-note">按各群真实 speaker_count 合计</span>
        </div>
        <div className="dashboard-kpi-card">
          <span className="dashboard-kpi-label">已生成 / 已发送</span>
          <strong>{counts.generated} / {counts.sent}</strong>
          <span className="dashboard-kpi-note">当前运行日期的任务状态</span>
        </div>
      </section>

      <div className={`dashboard-failure-note ${counts.failed > 0 || counts.held > 0 ? "has-failures" : ""}`}>
        {counts.failed > 0 || counts.held > 0 ? <WarningCircle size={20} aria-hidden="true" /> : <CheckCircle size={20} aria-hidden="true" />}
        <span>{counts.failed > 0 || counts.held > 0 ? `失败 ${counts.failed} 个，暂停待核对 ${counts.held} 个；请查看下方状态与错误信息。` : "当前没有失败或暂停任务。"}</span>
      </div>

      <div className="dashboard-main-grid">
        <section className="dashboard-panel dashboard-message-panel">
          <div className="dashboard-panel-heading">
            <div>
              <h2>群消息概览</h2>
              <p>按当前统计周期的真实消息数展示，不构造时间序列。</p>
            </div>
          </div>
          {data.cards.length === 0 ? (
            <EmptyState title="暂无启用群" description="请先在群聊配置中添加并启用微信群。" action={<Button tone="secondary" onClick={() => navigateToHash("/groups/new")}>新增群</Button>} />
          ) : (
            <div className="dashboard-message-list">
              {data.cards.map((card) => (
                <div className="dashboard-message-row" key={card.group_id}>
                  <div className="dashboard-message-label">
                    <strong>{card.group_name}</strong>
                    <span>{Number(card.message_count || 0)} 条消息</span>
                  </div>
                  <progress className="dashboard-message-bar" value={Number(card.message_count || 0)} max={maxMessages} aria-label={`${card.group_name} 消息数`} />
                </div>
              ))}
            </div>
          )}
        </section>

      </div>

      <section className="dashboard-panel dashboard-recent-panel">
        <div className="dashboard-panel-heading dashboard-panel-heading-with-action">
          <div>
            <h2>当前群任务</h2>
            <p>每群一张完整任务卡：左侧核对排行榜，右侧核对 AI 图片和发送状态。</p>
          </div>
          <Button tone="ghost" className="ui-button-compact" onClick={() => navigateToHash("/groups")}>
            管理群聊
          </Button>
        </div>
        {data.cards.length === 0 ? (
          <EmptyState title="暂无群任务" description="启用群后，任务卡片会出现在这里。" />
        ) : (
          <div className="dashboard-task-grid">
            {data.cards.map((card) => (
              <article className="dashboard-task-card" key={`${card.group_id}-${card.updated_at}`}>
                <div className="dashboard-task-card-head">
                  <div>
                    <h3>{card.group_name}</h3>
                    <span>{formatPeriod(card)} · 更新 {formatDateTime(card.updated_at)}</span>
                  </div>
                  {card.prompt_hold ? <StatusBadge tone="warning">暂停待核对</StatusBadge> : <StatusPill status={card.status} />}
                </div>
                <div className="dashboard-task-content">
                  <section className="dashboard-ranking-preview" aria-label={`${card.group_name} Top 5 排行榜`}>
                    <div className="dashboard-task-content-head"><strong>Top 5 排行榜</strong><span>{card.ranking_preview?.length ? "ranking.json" : "暂无排行"}</span></div>
                    {card.ranking_preview?.length ? (
                      <>
                        <ol>
                          {(card.ranking_preview || []).map((speaker) => (
                            <li key={`${speaker.rank}-${speaker.name}`}>
                              <span>{speaker.rank}</span><strong title={speaker.name}>{speaker.name}</strong><em>{formatRankingCount(card.ranking_count_policy, speaker)}</em>
                            </li>
                          ))}
                        </ol>
                        {isTextPrimaryRanking(card.ranking_count_policy) && <p className="dashboard-ranking-interaction-note">{INTERACTION_EXPLANATION}</p>}
                      </>
                    ) : <div className="dashboard-ranking-empty">{card.ranking_error || "尚未生成结构化排行榜"}</div>}
                  </section>
                  <section className="dashboard-image-preview" aria-label={`${card.group_name} AI 图片`}>
                    <div className="dashboard-task-content-head"><strong>AI 图片</strong><span>{card.image_url ? "daily_image.png" : "暂无图片"}</span></div>
                    <ImagePreview key={card.image_url || "empty"} card={card} onOpen={setViewerImage} />
                  </section>
                </div>
                {card.error && <p className="dashboard-task-error">{card.error}</p>}
                <div className="dashboard-task-meta">
                  <span>消息 {Number(card.message_count || 0)}</span>
                  <span>发言 {Number(card.speaker_count || 0)}</span>
                  <span>发送 {card.send_time || "—"}</span>
                </div>
                <TaskActions
                  card={card}
                  generating={generatingId === card.group_id}
                  sending={sendingId === card.group_id}
                  senderOk={health.data?.checks?.wechat_sender?.ok ?? false}
                  senderDetail={senderDetail}
                  onGenerate={() => handleGenerate(card)}
                  onResolvePrompt={() => setPromptRetryCard(card)}
                  onSend={() => handleSend(card)}
                  onResolve={() => {
                    setManualResolution("all_sent");
                    setManualCard(card);
                  }}
                />
              </article>
            ))}
          </div>
        )}
      </section>

      <ImageViewer
        open={Boolean(viewerImage)}
        src={viewerImage?.src || ""}
        alt={viewerImage?.alt || "日报图片"}
        filename={viewerImage?.filename || "daily_image.png"}
        title={viewerImage?.title || "日报图片"}
        onClose={() => setViewerImage(null)}
        onDownloadError={toast}
      />

      <ConfirmDialog
        open={Boolean(sendCard)}
        title="确认立即发送"
        description={sendCard ? `将把「${sendCard.group_name}」在 ${runDate} 的排行榜文字和图片发送到配置的微信群。请确认微信桌面会话处于可发送状态。` : ""}
        confirmLabel="确认发送"
        busy={sendingId !== null}
        onConfirm={confirmSend}
        onCancel={() => sendingId === null && setSendCard(null)}
      />
      <ConfirmDialog
        open={Boolean(promptRetryCard)}
        title="确认丢弃未知 Prompt 结果并重试"
        description={promptRetryCard ? `「${promptRetryCard.group_name}」上一次 Codex GPT 调用已经提交，但超时后没有可恢复的最终 Prompt。确认后会解除暂停并发起一次新的文本生成，可能增加一次模型用量；不会发送微信或邮件。` : ""}
        confirmLabel="确认并重新生成"
        busy={generatingId !== null}
        onConfirm={confirmPromptRetry}
        onCancel={() => generatingId === null && setPromptRetryCard(null)}
      />
      <AnimatePresence>
        {manualCard && (
        <m.div className="ui-dialog-backdrop" role="presentation" onMouseDown={() => !manualBusy && setManualCard(null)} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.18 }}>
          <m.section ref={manualDialogRef} tabIndex={-1} className="ui-dialog dashboard-manual-dialog" role="dialog" aria-modal="true" aria-labelledby="manual-send-title" onMouseDown={(event) => event.stopPropagation()} initial={{ opacity: 0, scale: 0.985, y: 5 }} animate={{ opacity: 1, scale: 1, y: 0 }} exit={{ opacity: 0, scale: 0.985, y: 4 }} transition={{ duration: 0.2, ease: MOTION_EASE }}>
            <h2 id="manual-send-title">核对「{manualCard.group_name}」的发送结果</h2>
            <p>请选择你已经在微信中实际完成的情况。这里仅更新任务状态和审计记录，不会再次发送任何内容。</p>
            <fieldset>
              <label><input type="radio" name="manual-resolution" checked={manualResolution === "all_sent"} onChange={() => setManualResolution("all_sent")} /><span><b>排行榜和图片均已发送</b><small>整单标记为已发送，不再自动重发。</small></span></label>
              <label><input type="radio" name="manual-resolution" checked={manualResolution === "text_sent"} onChange={() => setManualResolution("text_sent")} /><span><b>只发送了排行榜文字</b><small>保留图片待发送，后续只会继续图片阶段。</small></span></label>
              <label><input type="radio" name="manual-resolution" checked={manualResolution === "not_sent"} onChange={() => setManualResolution("not_sent")} /><span><b>两项都没有发送</b><small>清除未知提交检查点，恢复为可发送状态。</small></span></label>
            </fieldset>
            <div className="ui-dialog-actions">
              <Button tone="secondary" onClick={() => setManualCard(null)} disabled={manualBusy}>取消</Button>
              <Button tone="primary" onClick={confirmManualResolution} busy={manualBusy}>确认核对结果</Button>
            </div>
          </m.section>
        </m.div>
        )}
      </AnimatePresence>
      <Toast message={msg} />
    </div>
  );
}
