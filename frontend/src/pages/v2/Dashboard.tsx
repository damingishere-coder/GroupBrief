import { DashboardCard, getDashboard, pipelineGenerate, pipelineSend } from "../../api";
import { useFetch, useToast } from "../../components/ui";

const STATUS_META: Record<string, { label: string; cls: string }> = {
  PENDING: { label: "待生成", cls: "badge-warn" },
  DATA_READY: { label: "数据就绪", cls: "badge-warn" },
  RANKING_READY: { label: "排行完成", cls: "badge-warn" },
  PROMPT_READY: { label: "Prompt 完成", cls: "badge-ok" },
  IMAGE_READY: { label: "图片完成", cls: "badge-ok" },
  READY_TO_SEND: { label: "待发送", cls: "badge-ok" },
  SENT: { label: "已发送", cls: "badge" },
  FAILED: { label: "失败", cls: "badge-bad" },
};

function StatusBadge({ status }: { status: string }) {
  const meta = STATUS_META[status] || { label: status, cls: "badge-warn" };
  return <span className={`badge ${meta.cls}`}>{meta.label}</span>;
}

function CardActions({ card, toast }: { card: DashboardCard; toast: (s: string) => void }) {
  const canGenerate = card.status !== "SENT";
  const canSend = ["IMAGE_READY", "READY_TO_SEND"].includes(card.status) && !card.sent_at;

  const handleGenerate = () => {
    pipelineGenerate({ group_id: card.group_id, force: true })
      .then(() => toast("已触发生成，请刷新查看"))
      .catch((e) => toast(String(e)));
  };
  const handleSend = () => {
    if (!window.confirm(`确认立即发送「${card.group_name}」的日报到微信群？`)) return;
    pipelineSend({ group_id: card.group_id })
      .then(() => toast("已触发发送"))
      .catch((e) => toast(String(e)));
  };

  return (
    <div className="card-actions">
      {canGenerate && (
        <button className="btn btn-sm btn-secondary" onClick={handleGenerate}>
          立即生成
        </button>
      )}
      {canSend && (
        <button className="btn btn-sm" onClick={handleSend}>
          立即发送
        </button>
      )}
    </div>
  );
}

export default function Dashboard() {
  const { data, error, reload } = useFetch(getDashboard);
  const { msg, toast } = useToast();

  if (error) return <div className="empty-state">加载失败：{error}</div>;
  if (!data) return <div className="empty-state">加载中…</div>;

  const c = data.counts;

  return (
    <div>
      <div className="page-header">
        <div>
          <div className="page-title">今日概览</div>
          <div className="page-sub">
            {data.today} · 统计周期 {data.period_start} ~ {data.period_end}
            {!data.should_run && " · 今天不生成"}
          </div>
        </div>
        <div className="page-header-right">
          <span className="muted">下次发送：{data.next_send || "—"}</span>
          <button className="btn btn-secondary btn-sm" onClick={reload}>
            刷新
          </button>
        </div>
      </div>

      <div className="stat-grid">
        <div className="stat">
          <div className="stat-value">{data.enabled_groups}</div>
          <div className="stat-label">启用群数</div>
        </div>
        <div className="stat">
          <div className="stat-value">{c.pending}</div>
          <div className="stat-label">待生成</div>
        </div>
        <div className="stat">
          <div className="stat-value">{c.generated}</div>
          <div className="stat-label">已生成</div>
        </div>
        <div className="stat">
          <div className="stat-value">{c.sent}</div>
          <div className="stat-label">已发送</div>
        </div>
        <div className="stat">
          <div className="stat-value" style={c.failed > 0 ? { color: "#e5484d" } : undefined}>
            {c.failed}
          </div>
          <div className="stat-label">失败</div>
        </div>
      </div>

      <div className="group-cards">
        {data.cards.length === 0 && (
          <div className="empty-state">暂无启用群，请到「群管理」添加。</div>
        )}
        {data.cards.map((card) => (
          <div className="card group-card" key={card.group_id}>
            <div className="group-card-head">
              <div className="group-card-title">{card.group_name}</div>
              <StatusBadge status={card.status} />
            </div>
            {card.error && <div className="group-card-error">{card.error}</div>}
            {card.image_url ? (
              <div className="group-card-img">
                <img src={card.image_url} alt="日报图片" />
              </div>
            ) : (
              <div className="group-card-img placeholder">（暂无图片）</div>
            )}
            <div className="group-card-meta">
              <div>
                发送时间 <b>{card.send_time}</b>
              </div>
              <div>
                周期{" "}
                <b>
                  {card.period_start?.slice(5, 16)} ~ {card.period_end?.slice(5, 16)}
                </b>
              </div>
              <div>
                消息 <b>{card.message_count}</b> · 发言 <b>{card.speaker_count}</b>
              </div>
              {card.sent_at && (
                <div>
                  发送于 <b>{card.sent_at.slice(5, 16)}</b>
                </div>
              )}
            </div>
            <CardActions card={card} toast={toast} />
          </div>
        ))}
      </div>
      {msg && <div className="toast">{msg}</div>}
    </div>
  );
}
