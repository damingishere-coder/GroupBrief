import { useEffect, useState } from "react";
import {
  RecoveryInfo,
  StartupCheck,
  getRecoveryInfo,
  getStartupChecks,
  getSystemHealth,
  retryFailed,
} from "../../api";
import { useFetch, useToast } from "../../components/ui";

const CHECK_LABEL: Record<string, string> = {
  wechat_data_analysis: "WeChatDataAnalysis 数据源",
  deepseek: "DeepSeek V4 Flash",
  codex_imagegen: "Codex 图片生成",
  wechat_sender: "微信自动发送",
  output: "输出目录",
  templates: "模板资产",
  recent_task: "最近任务",
};

export default function System() {
  const { data, error, reload } = useFetch(getSystemHealth);
  const { msg, toast } = useToast();
  const [startup, setStartup] = useState<StartupCheck["checks"] | null>(null);
  const [recovery, setRecovery] = useState<RecoveryInfo | null>(null);

  useEffect(() => {
    getStartupChecks()
      .then((d) => setStartup(d.checks))
      .catch(() => {});
    getRecoveryInfo()
      .then(setRecovery)
      .catch(() => {});
  }, []);

  const doRetry = () => {
    if (!window.confirm("确认重跑所有未完成任务？将重新取数、排行、生成 Prompt（生图/发送视配置）")) return;
    retryFailed({})
      .then((r) => {
        toast(`已重跑 ${r.results.length} 个任务`);
        setTimeout(() => {
          getRecoveryInfo().then(setRecovery).catch(() => {});
        }, 3000);
      })
      .catch((e) => toast(String(e)));
  };

  return (
    <div>
      <div className="page-header">
        <div>
          <div className="page-title">系统状态</div>
          <div className="page-sub">外部依赖与运行环境健康检查</div>
        </div>
        <div className="row-actions">
          <button className="btn btn-secondary btn-sm" onClick={doRetry}>
            重跑未完成任务
          </button>
          <button className="btn btn-sm" onClick={reload}>
            重新检测
          </button>
        </div>
      </div>

      {error && <div className="empty-state">检测失败：{error}</div>}
      {!data && !error && <div className="empty-state">检测中…</div>}

      {data?.warnings && data.warnings.length > 0 && (
        <div className="card sys-warning">
          <div className="card-title">⚠ 无人值守提示</div>
          {data.warnings.map((w, i) => (
            <div key={i} className="muted">
              {w}
            </div>
          ))}
        </div>
      )}

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

      {startup && (
        <div className="card sys-tips">
          <div className="card-title">启动检查（服务启动时）</div>
          {startup.map((c) => (
            <div key={c.name} className="sys-startup-item">
              <span className={`dot ${c.ok ? "dot-ok" : "dot-bad"}`} />
              <b>{c.name}</b>
              <span className="muted">{c.detail}</span>
            </div>
          ))}
        </div>
      )}

      {recovery && (
        <div className="card sys-tips">
          <div className="card-title">
            恢复信息 · 未完成任务 {recovery.incomplete.length} 个
          </div>
          {recovery.incomplete.length === 0 && <div className="muted">无未完成任务，状态一致。</div>}
          {recovery.incomplete.map((r) => (
            <div key={`${r.group_name}-${r.run_date}`} className="sys-startup-item">
              <span className="muted">
                {r.group_name} · {r.run_date} · {r.status}
              </span>
            </div>
          ))}
          {recovery.integrity.filter((x) => !x.ok).length > 0 && (
            <div className="muted" style={{ marginTop: 8 }}>
              文件不完整：{recovery.integrity.filter((x) => !x.ok).map((x) => `${x.group_name}(${x.missing.join(",")})`).join("、")}
            </div>
          )}
        </div>
      )}

      <div className="card sys-tips">
        <div className="card-title">运行环境要求</div>
        <ul>
          <li>电脑需长期开机、不锁屏、不休眠（微信发送与生图依赖桌面会话）。</li>
          <li>微信 PC 客户端需保持登录。</li>
          <li>WeChatDataAnalysis 本地服务需运行（数据读取）。</li>
          <li>DeepSeek API Key 需在设置中配置（生图 Prompt 生成）。</li>
          <li>Codex CLI 需安装（$imagegen 生图）。</li>
        </ul>
      </div>
      {msg && <div className="toast">{msg}</div>}
    </div>
  );
}
