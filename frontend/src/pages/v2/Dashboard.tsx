import { useMemo, useState } from "react";
import {
  ArrowRight,
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

// 本地时区的今天（避免 toISOString 的 UTC 偏移问题）
function todayLocal(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(
    d.getDate()
  ).padStart(2, "0")}`;
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
  onSend: () => void;
}

function TaskActions({
  card,
  generating,
  sending,
  senderOk,
  senderDetail,
  onGenerate,
  onSend,
}: TaskActionsProps) {
  const canGenerate = card.status !== "SENT";
  const canSend = ["IMAGE_READY", "READY_TO_SEND"].includes(card.status) && !card.sent_at;

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
    </div>
  );
}

export default function Dashboard() {
  const dashboard = useFetch(getDashboard);
  const health = useFetch(getSystemHealth);
  const { msg, toast } = useToast();
  const [runDate, setRunDate] = useState(todayLocal());
  const [generatingId, setGeneratingId] = useState<number | null>(null);
  const [sendingId, setSendingId] = useState<number | null>(null);
  const [sendCard, setSendCard] = useState<DashboardCard | null>(null);
  const [viewerImage, setViewerImage] = useState<ViewerImage | null>(null);

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
  const recentCards = data.cards.slice(0, 5);

  return (
    <div className="dashboard-page">
      <PageHeader
        title="运行总览"
        description={`${data.today} · 统计周期 ${data.period_start} ~ ${data.period_end}${!data.should_run ? " · 今天不生成" : ""}`}
        actions={
          <>
            <StatusBadge tone={health.loading ? "neutral" : systemHealthy ? "success" : "warning"}>
              {health.loading ? "健康检查中" : systemHealthy ? "系统健康" : "需要关注"}
            </StatusBadge>
            <label className="dashboard-date-field">
              <span>生成日期</span>
              <input
                type="date"
                value={runDate}
                onChange={(event) => setRunDate(event.target.value || todayLocal())}
                aria-label="生成日期"
              />
            </label>
            <Button tone="ghost" onClick={refresh} busy={dashboard.loading}>
              <ArrowsClockwise size={17} aria-hidden="true" />
              刷新
            </Button>
          </>
        }
      />

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

      <div className={`dashboard-failure-note ${counts.failed > 0 ? "has-failures" : ""}`}>
        {counts.failed > 0 ? <WarningCircle size={20} aria-hidden="true" /> : <CheckCircle size={20} aria-hidden="true" />}
        <span>{counts.failed > 0 ? `有 ${counts.failed} 个群任务失败，请查看下方状态与错误信息。` : "当前没有失败任务。"}</span>
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

        <section className="dashboard-panel dashboard-status-panel">
          <div className="dashboard-panel-heading">
            <div>
              <h2>今日任务状态</h2>
              <p>每个群独立记录，失败不会隐藏或阻塞其他群。</p>
            </div>
          </div>
          {data.cards.length === 0 ? (
            <EmptyState title="暂无今日任务" description="启用群后，这里会显示任务状态。" />
          ) : (
            <div className="dashboard-status-list">
              {data.cards.map((card) => (
                <div className="dashboard-status-row" key={card.group_id}>
                  <div className="dashboard-status-main">
                    <strong>{card.group_name}</strong>
                    <span>更新时间 {formatDateTime(card.updated_at)} · 发送 {card.send_time || "—"}</span>
                  </div>
                  <StatusPill status={card.status} />
                </div>
              ))}
            </div>
          )}
        </section>
      </div>

      <section className="dashboard-panel dashboard-recent-panel">
        <div className="dashboard-panel-heading dashboard-panel-heading-with-action">
          <div>
            <h2>最近群任务</h2>
            <p>显示当前接口返回的群任务，最多 5 项。</p>
          </div>
          <Button tone="ghost" className="ui-button-compact" onClick={() => navigateToHash("/groups")}>
            管理群聊
            <ArrowRight size={16} aria-hidden="true" />
          </Button>
        </div>
        {recentCards.length === 0 ? (
          <EmptyState title="暂无群任务" description="启用群后，任务卡片会出现在这里。" />
        ) : (
          <div className="dashboard-task-grid">
            {recentCards.map((card) => (
              <article className="dashboard-task-card" key={card.group_id}>
                <div className="dashboard-task-card-head">
                  <div>
                    <h3>{card.group_name}</h3>
                    <span>{formatPeriod(card)}</span>
                  </div>
                  <StatusPill status={card.status} />
                </div>
                <ImagePreview key={card.image_url || "empty"} card={card} onOpen={setViewerImage} />
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
                  onSend={() => handleSend(card)}
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
      <Toast message={msg} />
    </div>
  );
}
