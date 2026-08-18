import { getSystemHealth } from "../../api";
import { useFetch } from "../../components/ui";

const CHECK_LABEL: Record<string, string> = {
  wechat_data_analysis: "WeChatDataAnalysis 数据源",
  deepseek: "DeepSeek V4 Flash",
  codex_imagegen: "Codex 图片生成",
  wechat_sender: "微信自动发送",
  output: "输出目录",
  templates: "模板资产",
};

export default function System() {
  const { data, error, reload } = useFetch(getSystemHealth);

  return (
    <div>
      <div className="page-header">
        <div>
          <div className="page-title">系统状态</div>
          <div className="page-sub">外部依赖与运行环境健康检查</div>
        </div>
        <button className="btn btn-secondary btn-sm" onClick={reload}>
          重新检测
        </button>
      </div>

      {error && <div className="empty-state">检测失败：{error}</div>}
      {!data && !error && <div className="empty-state">检测中…</div>}

      {data && (
        <div className="sys-grid">
          {Object.entries(data.checks).map(([key, check]) => (
            <div className="card sys-item" key={key}>
              <div className="sys-head">
                <span className={`dot ${check.ok ? "dot-ok" : "dot-bad"}`} />
                <b>{CHECK_LABEL[key] || key}</b>
                <span className={`badge ${check.ok ? "badge" : "badge-bad"}`}>{check.status}</span>
              </div>
              <div className="muted">{check.detail}</div>
            </div>
          ))}
        </div>
      )}

      <div className="card sys-tips">
        <div className="card-title">运行环境提示</div>
        <ul>
          <li>电脑需长期开机、不锁屏、不休眠（微信发送与生图依赖桌面会话）。</li>
          <li>微信 PC 客户端需保持登录。</li>
          <li>WeChatDataAnalysis 本地服务需运行（数据读取）。</li>
          <li>DeepSeek API Key 需在设置中配置（生图 Prompt 生成）。</li>
          <li>Codex CLI 需安装（$imagegen 生图）。</li>
        </ul>
      </div>
    </div>
  );
}
