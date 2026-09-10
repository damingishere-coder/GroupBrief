import { useReportScroll } from "../../components/useReportScroll";
import { useEffect, useMemo, useState } from "react";
import {
  ArrowsClockwise,
  CheckCircle,
  ImageSquare,
  WarningCircle,
} from "@phosphor-icons/react";
import { DashboardCard, getDashboard, getSystemHealth } from "../../api";
import {
  Button,
  EmptyState,
  ImagePreviewTrigger,
  LoadingState,
  PageHeader,
  StatusBadge,
} from "../../components/common";
import { useFetch } from "../../components/ui";
import {
  navigateToHash,
  updateWorkspaceQuery,
  useWorkspaceQuery,
} from "../../navigation";
import ReportWorkspace from "./ReportWorkspace";
import {
  ArrowUpRight,
  MagnifyingGlass,
  Sparkle,
  Stack,
  UsersThree,
} from "@phosphor-icons/react";
import { shanghaiDateInputValue } from "../../date";
import { runtimeRefreshDelay } from "./dashboardRuntime";

const STATUS_META: Record<
  string,
  { label: string; tone: "success" | "warning" | "danger" | "info" | "neutral" }
> = {
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
  const meta = STATUS_META[status.toUpperCase()] || {
    label: status || "未知",
    tone: "neutral" as const,
  };
  return <StatusBadge tone={meta.tone}>{meta.label}</StatusBadge>;
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

function ImagePreview({
  card,
  onOpen,
}: {
  card: DashboardCard;
  onOpen: (image: ViewerImage) => void;
}) {
  const [imageBroken, setImageBroken] = useState(false);
  const diagnostic = card.image_delivery_eligible === false;

  if (card.image_url) {
    const alt = diagnostic
      ? `${card.group_name} 不可发送诊断图`
      : `${card.group_name} 日报图片`;
    if (!imageBroken) {
      return (
        <div
          className={`dashboard-image-frame${diagnostic ? " is-diagnostic" : ""}`}
        >
          {diagnostic && (
            <StatusBadge tone="danger">诊断图不可发送</StatusBadge>
          )}
          <ImagePreviewTrigger
            src={card.image_url}
            alt={alt}
            imageClassName="dashboard-task-image"
            className="dashboard-task-image-trigger"
            onError={() => setImageBroken(true)}
            onOpen={() =>
              onOpen({
                src: card.image_url,
                alt,
                filename: "daily_image.png",
                title: alt,
              })
            }
          />
        </div>
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

export default function Dashboard() {
  const queryParams = useWorkspaceQuery();
  const rememberScroll = useReportScroll(queryParams.get("group"));
  const runDate = queryParams.get("date") || shanghaiDateInputValue();
  const setRunDate = (date: string) =>
    updateWorkspaceQuery({ date, group: null, panel: null });
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("all");
  const dashboard = useFetch(() => getDashboard(runDate), [runDate]);
  const health = useFetch(getSystemHealth);
  const [healthOpen, setHealthOpen] = useState(false);
  const [pageVisible, setPageVisible] = useState(
    () => document.visibilityState !== "hidden",
  );
  useEffect(() => {
    const onVisibilityChange = () => {
      const visible = document.visibilityState !== "hidden";
      setPageVisible(visible);
      if (visible && runDate === shanghaiDateInputValue()) {
        dashboard.reload();
      }
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () =>
      document.removeEventListener("visibilitychange", onVisibilityChange);
  }, [dashboard.reload, runDate]);

  const runtimeStatus =
    dashboard.data?.runtime?.overall_status || "not_started";
  const runtimeScheduledAt = dashboard.data?.runtime?.scheduler?.scheduled_at;
  useEffect(() => {
    const delay = runtimeRefreshDelay(runtimeStatus, {
      isToday: runDate === shanghaiDateInputValue(),
      visible: pageVisible,
      scheduledAt: runtimeScheduledAt,
    });
    if (delay === null) return;
    const timer = window.setTimeout(() => {
      dashboard.reload();
    }, delay);
    return () => window.clearTimeout(timer);
  }, [
    dashboard.reload,
    pageVisible,
    runDate,
    runtimeScheduledAt,
    runtimeStatus,
  ]);

  const refresh = () => {
    dashboard.reload();
    health.reload();
  };

  const totalMessages = useMemo(
    () =>
      dashboard.data?.cards.reduce(
        (total, card) => total + Number(card.message_count || 0),
        0,
      ) || 0,
    [dashboard.data],
  );
  const totalSpeakers = useMemo(
    () =>
      dashboard.data?.cards.reduce(
        (total, card) => total + Number(card.speaker_count || 0),
        0,
      ) || 0,
    [dashboard.data],
  );
  const healthChecks = health.data ? Object.values(health.data.checks) : [];
  const healthProblems = health.data
    ? Object.entries(health.data.checks).filter(([, check]) => !check.ok)
    : [];
  const systemHealthy =
    healthChecks.length > 0 && healthChecks.every((check) => check.ok);

  if (
    dashboard.loading &&
    (!dashboard.data || dashboard.data.today !== runDate)
  )
    return <LoadingState label="正在加载今日运行总览…" />;
  if (dashboard.error && !dashboard.data) {
    return (
      <EmptyState
        title="总览加载失败"
        description={dashboard.error}
        action={
          <Button tone="secondary" onClick={refresh}>
            重新加载
          </Button>
        }
      />
    );
  }
  if (!dashboard.data)
    return (
      <EmptyState
        title="暂无总览数据"
        description="接口尚未返回可展示的数据。"
      />
    );

  const data = dashboard.data;
  const counts = data.counts;
  const dailyStatusMeta = {
    not_started: { label: "今日未开始", tone: "neutral" as const },
    running: { label: "今日运行中", tone: "info" as const },
    retry_pending: { label: "等待自动重试", tone: "warning" as const },
    complete: { label: "今日已完成", tone: "success" as const },
    partial: { label: "今日部分完成", tone: "warning" as const },
    blocked: { label: "今日已阻断", tone: "danger" as const },
    failed: { label: "今日运行失败", tone: "danger" as const },
    needs_attention: { label: "今日待核对", tone: "danger" as const },
  }[data.daily_status?.overall_status || "not_started"];

  const needsAttention = (card: DashboardCard) =>
    card.status === "FAILED" ||
    card.send_hold ||
    card.prompt_hold ||
    card.image_delivery_eligible === false;
  const matchesFilter = (card: DashboardCard) =>
    filter === "all" ||
    (filter === "attention"
      ? needsAttention(card)
      : filter === "sent"
        ? card.status === "SENT"
        : filter === "ready"
          ? ["IMAGE_READY", "READY_TO_SEND"].includes(card.status) &&
            !needsAttention(card)
          : !["SENT", "IMAGE_READY", "READY_TO_SEND", "FAILED"].includes(
              card.status,
            ));
  const cards = data.cards
    .filter(
      (card) =>
        card.group_name
          .toLocaleLowerCase()
          .includes(search.trim().toLocaleLowerCase()) && matchesFilter(card),
    )
    .sort((a, b) => Number(needsAttention(b)) - Number(needsAttention(a)));
  const selected = data.cards.find(
    (card) => card.group_name === queryParams.get("group"),
  );
  const openCard = (card: DashboardCard) => {
    rememberScroll();
    updateWorkspaceQuery({
      group: card.group_name,
      date: runDate,
      panel: "preview",
    });
  };

  return (
    <div className="dashboard-page studio-desk">
      <PageHeader
        title="今天的精彩，从这里开始"
        description="让群聊里的灵感与热闹，成为值得收藏的日报。"
        actions={
          <>
            <label className="studio-date">
              <span>运行日期</span>
              <input
                type="date"
                aria-label="运行日期"
                value={runDate}
                onChange={(event) =>
                  setRunDate(event.target.value || shanghaiDateInputValue())
                }
              />
            </label>
            <Button tone="secondary" onClick={refresh} busy={dashboard.loading}>
              <ArrowsClockwise size={17} />
              刷新
            </Button>
          </>
        }
      />
      {!selected && (
        <section className="studio-hero">
          <div className="studio-hero-copy">
            <span className="studio-eyebrow">
              <Sparkle size={15} weight="fill" /> DAILY CREATIVE SPACE
            </span>
            <h2>
              每一个群，
              <br />
              都有自己的<span>高光时刻。</span>
            </h2>
            <p>
              消息统计周期 {data.period_start?.slice(0, 10)} —{" "}
              {data.period_end?.slice(0, 10)}
              {!data.should_run ? " · 当日无计划生成" : ""}
            </p>
            <button
              onClick={() => {
                setFilter("attention");
                document
                  .getElementById("daily-reports")
                  ?.scrollIntoView({ block: "start", behavior: "smooth" });
              }}
            >
              查看待处理日报 <ArrowUpRight size={18} />
            </button>
          </div>
          <div className="studio-hero-art" aria-hidden="true">
            <div className="hero-orbit" />
            <span className="hero-spark">✦</span>
            <div className="hero-paper paper-back">
              <span>GROUP / BRIEF</span>
              <div />
              <div />
              <div />
            </div>
            <div className="hero-paper paper-front">
              <span>今日灵感</span>
              <Sparkle size={42} weight="fill" />
              <strong>
                让讨论
                <br />
                留下回响
              </strong>
              <small>DISCUSS. CREATE. SHARE.</small>
            </div>
            <span className="hero-sticker">灵感，每天发生 ↗</span>
          </div>
        </section>
      )}
      <section className="studio-stats" aria-label="运行统计">
        <div>
          <span className="stat-icon violet">
            <UsersThree size={21} />
          </span>
          <p>
            启用群数
            <strong>
              {data.enabled_groups}
              <small>个群</small>
            </strong>
          </p>
        </div>
        <div>
          <span className="stat-icon peach">
            <Stack size={21} />
          </span>
          <p>
            周期消息总数
            <strong>
              {totalMessages.toLocaleString()}
              <small>{totalSpeakers} 人参与</small>
            </strong>
          </p>
        </div>
        <div>
          <span className="stat-icon mint">
            <CheckCircle size={21} />
          </span>
          <p>
            已生成 / 已发送
            <strong>
              {counts.generated} <i>/</i> {counts.sent}
              <small>份日报</small>
            </strong>
          </p>
        </div>
        <button onClick={() => setFilter("attention")}>
          <span className="stat-icon amber">
            <WarningCircle size={21} />
          </span>
          <p>
            需要关注
            <strong>
              {data.cards.filter(needsAttention).length}
              <small>查看待办 →</small>
            </strong>
          </p>
        </button>
      </section>
      <div className="studio-status-line">
        <span>
          <span className={`status-dot ${systemHealthy ? "ok" : "warn"}`} />
          {dailyStatusMeta.label} ·{" "}
          {counts.failed || counts.held
            ? `失败 ${counts.failed} 个，暂停待核对 ${counts.held} 个`
            : "当前没有失败或暂停任务"}
        </span>
        <button
          onClick={() => setHealthOpen((value) => !value)}
          aria-expanded={healthOpen}
        >
          {health.loading
            ? "健康检查中"
            : systemHealthy
              ? "系统健康"
              : "系统需要关注"}{" "}
          ↗
        </button>
      </div>
      {healthOpen && (
        <div className="studio-alert" role="status">
          {health.error ||
            (systemHealthy ? "所有系统检查均正常" : "以下检查需要关注")}
          {healthProblems.map(([name, check]) => (
            <p key={name}>
              {name}：{check.detail || check.status}
            </p>
          ))}
        </div>
      )}
      <div id="daily-reports" className="studio-section-head">
        <div>
          <span className="studio-eyebrow">YOUR DAILY REPORTS</span>
          <h2>
            日报工作台 <span>{data.cards.length}</span>
          </h2>
        </div>
        <div className="studio-search">
          <MagnifyingGlass size={18} />
          <input
            aria-label="搜索日报群名"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="找一个群的日报…"
          />
        </div>
      </div>
      <div className="studio-filter-row" aria-label="日报状态筛选">
        {[
          ["all", "全部日报"],
          ["attention", "需要关注"],
          ["pending", "生成中 / 待生成"],
          ["ready", "待发送"],
          ["sent", "已发送"],
        ].map(([key, label]) => (
          <button
            key={key}
            type="button"
            aria-pressed={filter === key}
            className={filter === key ? "active" : ""}
            onClick={() => setFilter(key)}
          >
            {label}
          </button>
        ))}
        <button
          className="studio-filter-end"
          onClick={() => navigateToHash("images")}
        >
          全部作品 <ArrowUpRight size={16} />
        </button>
      </div>
      <div
        className={`studio-reports-layout ${selected ? "with-workspace" : ""}`}
      >
        <div className={selected ? "studio-report-list" : "studio-report-grid"}>
          {cards.length === 0 && (
            <EmptyState
              title={
                data.cards.length
                  ? "没有匹配的日报"
                  : "还没有群聊，添加你的第一个群"
              }
              description="调整筛选，或前往群聊管理添加群。"
              action={
                <Button
                  tone="secondary"
                  onClick={() => navigateToHash("groups")}
                >
                  管理群聊
                </Button>
              }
            />
          )}
          {cards.map((card, index) => (
            <article
              className={`studio-report-card color-${index % 4} ${selected?.group_id === card.group_id ? "selected" : ""}`}
              key={card.group_id}
            >
              {!selected && (
                <div className="studio-card-cover">
                  <ImagePreview
                    key={card.image_url}
                    card={card}
                    onOpen={() => openCard(card)}
                  />
                  <span className="studio-cover-date">
                    {runDate.slice(5).replace("-", " / ")}
                  </span>
                </div>
              )}
              <button
                type="button"
                className="studio-card-main"
                onClick={() => openCard(card)}
                aria-label={`打开日报 ${card.group_name}`}
                aria-pressed={selected?.group_id === card.group_id}
              >
                <div className="studio-card-heading">
                  <span className="group-monogram">
                    {Array.from(card.group_name)[0]}
                  </span>
                  <h3>{card.group_name}</h3>
                  <ArrowUpRight size={18} />
                </div>
                <div className="studio-card-meta">
                  <span>
                    {Number(card.message_count || 0).toLocaleString()} 条消息 ·{" "}
                    {Number(card.speaker_count || 0)} 人
                  </span>
                  <StatusPill status={card.status} />
                  {needsAttention(card) && (
                    <StatusBadge tone="warning">
                      {card.image_delivery_eligible === false
                        ? card.status === "SENT"
                          ? "图片生成失败（已发送）"
                          : "图片不可发送"
                        : "需要关注"}
                    </StatusBadge>
                  )}
                </div>
                {card.error && (
                  <p className="studio-card-error">{card.error}</p>
                )}
              </button>
              {!selected && (
                <div className="studio-card-footer">
                  <span>{formatPeriod(card)}</span>
                  <button onClick={() => openCard(card)}>
                    查看与处理 <ArrowUpRight size={15} />
                  </button>
                </div>
              )}
            </article>
          ))}
        </div>
        {selected && (
          <ReportWorkspace
            key={`${runDate}:${selected.group_id}`}
            target={{ groupName: selected.group_name, runDate }}
            onUpdated={dashboard.reload}
          />
        )}
      </div>
    </div>
  );
}
