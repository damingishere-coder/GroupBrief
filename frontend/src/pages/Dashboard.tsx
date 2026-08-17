import { get, SystemStatus, type Group } from "../api";
import { useFetch } from "../components/ui";

export default function Dashboard({ onNav }: { onNav: (p: string) => void }) {
  const { data: status } = useFetch(() => get<SystemStatus>("/system/status"));
  const { data: groups } = useFetch(() => get<Group[]>("/groups"));
  const { data: providers } = useFetch(() =>
    get<Record<string, { status: string; detail: string; ok: boolean }>>(
      "/system/providers"
    )
  );

  const providerLabel: Record<string, string> = {
    wechat_data_analysis: "WeChatDataAnalysis",
    wechat_cli: "wechat-cli",
    mock: "Mock（测试数据）",
  };

  return (
    <div>
      <div className="page-header row">
        <div>
          <div className="page-title">仪表盘</div>
          <div className="page-sub">
            {status ? `统计日期 ${status.report_date}` : "正在加载…"}
          </div>
        </div>
        <div className="spacer" />
        <button
          className="btn"
          onClick={() => onNav("groups")}
        >
          立即生成群报
        </button>
      </div>

      <div className="stat-grid">
        <div className="stat">
          <div className="stat-label">当前统计日期</div>
          <div className="stat-value">{status?.report_date ?? "—"}</div>
          <div className="muted" style={{ fontSize: 12 }}>
            {status?.is_weekend_summary ? "周末两天汇总" : "单日统计"}
          </div>
        </div>
        <div className="stat">
          <div className="stat-label">已配置群数</div>
          <div className="stat-value">{status?.total_groups ?? 0}</div>
          <div className="muted" style={{ fontSize: 12 }}>
            {status?.enabled_groups ?? 0} 个已启用
          </div>
        </div>
        <div className="stat">
          <div className="stat-label">下次自动生成</div>
          <div className="stat-value" style={{ fontSize: 18 }}>
            {status?.next_generate_at
              ? new Date(status.next_generate_at).toLocaleString("zh-CN", {
                  hour12: false,
                })
              : "周日不执行"}
          </div>
        </div>
        <div className="stat">
          <div className="stat-label">服务状态</div>
          <div className="stat-value" style={{ fontSize: 18, color: "#34c759" }}>
            ● 运行中
          </div>
          <div className="muted" style={{ fontSize: 12 }}>
            v{status?.version ?? "—"} · {status?.timezone ?? ""}
          </div>
        </div>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <div className="card-title">系统状态</div>
        {!providers ? (
          <div className="muted">加载中…</div>
        ) : (
          Object.entries(providers).map(([key, p]) => (
            <div className="status-row" key={key}>
              <div>
                <span
                  className={`dot ${p.ok ? "dot-ok" : p.status === "UNSUPPORTED_WECHAT_VERSION" || p.status === "EMPTY_RESULT" ? "dot-warn" : "dot-bad"}`}
                />
                <strong>{providerLabel[key] ?? key}</strong>
                <div className="muted" style={{ fontSize: 12, marginLeft: 16, display: "inline" }}>
                  {p.detail}
                </div>
              </div>
              <span
                className={`badge ${p.ok ? "badge-ok" : "badge-bad"}`}
              >
                {p.ok ? "可用" : p.status}
              </span>
            </div>
          ))
        )}
      </div>

      <div className="card">
        <div className="card-title">已配置群聊</div>
        {!groups || groups.length === 0 ? (
          <div className="empty-state">
            <div className="big">还没有配置群聊</div>
            <div>点击下方按钮，从微信读取到群列表中选择要统计的群</div>
            <div style={{ marginTop: 16 }}>
              <button className="btn" onClick={() => onNav("groups")}>
                去添加群聊
              </button>
            </div>
          </div>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>显示名称</th>
                <th>微信群</th>
                <th>状态</th>
              </tr>
            </thead>
            <tbody>
              {groups.map((g) => (
                <tr key={g.id}>
                  <td>{g.display_name || g.wechat_group_name}</td>
                  <td className="muted">{g.wechat_group_name}</td>
                  <td>
                    {g.enabled ? (
                      <span className="badge badge-ok">启用</span>
                    ) : (
                      <span className="badge badge-warn">停用</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
