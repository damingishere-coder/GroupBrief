import { useEffect, useMemo, useState } from "react";
import {
  CheckCircle,
  Clock,
  Database,
  GearSix,
  Heartbeat,
  Key,
  PlugsConnected,
  WarningCircle,
} from "@phosphor-icons/react";
import {
  RecoveryInfo,
  SettingsValues,
  StartupCheck,
  SystemHealth,
  getRecoveryInfo,
  getSettings,
  getStartupChecks,
  getSystemHealth,
  saveSettings,
} from "../../api";
import { Button, EmptyState, LoadingState, PageHeader, StatusBadge, Toast } from "../../components/common";
import { useToast } from "../../components/ui";

type SettingsTab = "settings" | "health" | "startup" | "recovery";

const SENSITIVE_KEYS = new Set(["ai_api_key", "email_smtp_password", "email_smtp_user", "email_from", "wechat_mcp_token"]);
const BOOLEAN_KEYS = new Set(["email_enabled", "email_use_ssl", "email_send_partial_report"]);
const NUMBER_KEYS = new Set(["wechat_mcp_timeout_seconds", "wechat_mcp_range_timeout_seconds", "codex_summary_timeout_seconds", "codex_summary_max_retries", "codex_summary_request_concurrency", "ai_timeout_seconds", "ai_max_retries", "max_context_chars", "generation_group_concurrency", "wechat_fetch_concurrency", "ai_request_concurrency", "email_smtp_port"]);

const LABELS: Record<string, string> = {
  wechat_data_dir: "微信数据目录",
  wechat_export_dir: "微信导出目录",
  wechat_cli_path: "wechat-cli 路径",
  wechat_mcp_url: "WeChatDataAnalysis MCP URL",
  wechat_mcp_token: "MCP Token",
  wechat_mcp_account: "MCP 账号标识",
  wechat_mcp_timeout_seconds: "MCP 超时（秒）",
  wechat_mcp_range_timeout_seconds: "全天范围读取超时（秒）",
  wechat_contact_db_path: "联系人数据库路径",
  summary_provider_primary: "总结主 Provider",
  summary_provider_fallback: "总结备用 Provider",
  codex_summary_model: "Codex GPT 主模型",
  codex_summary_timeout_seconds: "Codex 总结超时（秒）",
  codex_summary_max_retries: "Codex 最大重试次数",
  codex_summary_request_concurrency: "Codex 总结并发数",
  ai_base_url: "DeepSeek Base URL（备用）",
  ai_api_key: "DeepSeek API Key（备用）",
  ai_model: "DeepSeek 模型 ID（备用）",
  ai_timeout_seconds: "DeepSeek 超时（秒）",
  ai_max_retries: "DeepSeek 最大重试次数",
  max_context_chars: "整群直接提交上限（字符）",
  generation_group_concurrency: "群任务并发数",
  wechat_fetch_concurrency: "微信取数并发数",
  ai_request_concurrency: "DeepSeek 备用请求并发数",
  email_enabled: "启用邮件（V1 兼容）",
  email_recipient: "邮件收件人（V1 兼容）",
  email_from: "发件地址（V1 兼容）",
  email_smtp_host: "SMTP 主机（V1 兼容）",
  email_smtp_port: "SMTP 端口（V1 兼容）",
  email_smtp_user: "SMTP 用户（V1 兼容）",
  email_smtp_password: "SMTP 密码（V1 兼容）",
  email_use_ssl: "SMTP 使用 SSL（V1 兼容）",
  email_send_partial_report: "允许部分群邮件（V1 兼容）",
  schedule_generate_time: "每日生成时间",
  schedule_email_time: "邮件时间（V1 兼容）",
};

const SETTING_GROUPS = [
  {
    id: "data",
    title: "数据与微信读取",
    description: "只编辑当前后端设置 API 返回的 Provider、MCP 与本地读取字段。",
    icon: Database,
    keys: [
      "wechat_data_dir",
      "wechat_export_dir",
      "wechat_cli_path",
      "wechat_mcp_url",
      "wechat_mcp_token",
      "wechat_mcp_account",
      "wechat_mcp_timeout_seconds",
      "wechat_mcp_range_timeout_seconds",
      "wechat_contact_db_path",
    ],
  },
  {
    id: "model",
    title: "模型与 Prompt",
    description: "Codex GPT 主用；单次失败时自动切换到已配置的 DeepSeek 备用。",
    icon: PlugsConnected,
    keys: ["summary_provider_primary", "summary_provider_fallback", "codex_summary_model", "codex_summary_timeout_seconds", "codex_summary_max_retries", "ai_base_url", "ai_model", "ai_api_key", "ai_timeout_seconds", "ai_max_retries", "max_context_chars"],
  },
  {
    id: "advanced",
    title: "高级并发设置",
    description: "控制群任务、微信取数、Codex 总结和 DeepSeek 备用请求的并发上限。",
    icon: GearSix,
    keys: ["generation_group_concurrency", "wechat_fetch_concurrency", "codex_summary_request_concurrency", "ai_request_concurrency"],
  },
  {
    id: "legacy",
    title: "邮件与每日调度",
    description: "每日生成时间用于 V2 前一日群报；各群发送时间仍在群聊配置中管理。邮件字段保留兼容。",
    icon: Clock,
    keys: ["email_enabled", "email_recipient", "email_from", "email_smtp_host", "email_smtp_port", "email_smtp_user", "email_smtp_password", "email_use_ssl", "email_send_partial_report", "schedule_generate_time", "schedule_email_time"],
  },
] as const;

const CHECK_LABEL: Record<string, string> = {
  wechat_data_analysis: "WeChatDataAnalysis 数据源",
  codex_summary: "Codex GPT 群聊总结",
  deepseek_fallback: "DeepSeek 总结备用",
  codex_imagegen: "Codex 图片生成",
  wechat_sender: "微信自动发送",
  output: "输出目录",
  templates: "模板资产",
  recent_task: "最近任务",
};

function friendlyError(error: unknown, fallback: string): string {
  const message = String(error || "");
  if (!message || /key|token|password|secret|credential/i.test(message)) return fallback;
  return message.replace(/^Error:\s*/i, "").slice(0, 160) || fallback;
}

function safeDetail(detail: unknown): string {
  return String(detail || "—")
    .replace(/[A-Za-z]:\\[^,，。；;\n]+/g, "本机路径")
    .replace(/(^|\s)\/[^\s，。；;）)]+/g, "$1本机路径");
}

function detailTone(ok: boolean): "success" | "danger" {
  return ok ? "success" : "danger";
}

function SettingField({
  name,
  value,
  onChange,
}: {
  name: string;
  value: string;
  onChange: (value: string) => void;
}) {
  const sensitive = SENSITIVE_KEYS.has(name);
  const boolean = BOOLEAN_KEYS.has(name) || value === "true" || value === "false";
  const inputType = sensitive ? "password" : NUMBER_KEYS.has(name) ? "number" : "text";
  return (
    <div className={`settings-field ${boolean ? "is-switch" : ""}`}>
      {boolean ? (
        <label className="settings-switch-field">
          <input type="checkbox" checked={value.toLowerCase() === "true"} onChange={(event) => onChange(event.target.checked ? "true" : "false")} />
          <span className="settings-switch-visual" aria-hidden="true" />
          <span><strong>{LABELS[name] || name}</strong><small>{name}</small></span>
        </label>
      ) : (
        <label>
          <span className="settings-label"><strong>{LABELS[name] || name}</strong><small>{name}</small></span>
          <input type={inputType} value={value} autoComplete={sensitive ? "new-password" : undefined} onChange={(event) => onChange(event.target.value)} aria-describedby={sensitive ? "settings-secret-note" : undefined} />
        </label>
      )}
      {sensitive && <span className="settings-sensitive-note">敏感值已掩码</span>}
    </div>
  );
}

function HealthPanel({ health, loading, error }: { health: SystemHealth | null; loading: boolean; error: string }) {
  if (loading) return <LoadingState label="正在检查系统健康…" />;
  if (error) return <div className="settings-error" role="alert"><WarningCircle size={18} />{error}</div>;
  if (!health) return <EmptyState title="暂无健康检查结果" />;
  return (
    <div className="settings-diagnostics">
      {health.warnings && health.warnings.length > 0 && (
        <div className="settings-warning" role="note"><WarningCircle size={19} /><div><strong>无人值守提示</strong>{health.warnings.map((warning) => <p key={warning}>{safeDetail(warning)}</p>)}</div></div>
      )}
      <div className="settings-health-grid">
        {Object.entries(health.checks).map(([key, check]) => (
          <article className="settings-health-item" key={key}>
            <div className="settings-health-head"><span className={`settings-health-dot ${check.ok ? "is-ok" : "is-bad"}`} /><strong>{CHECK_LABEL[key] || key}</strong><StatusBadge tone={detailTone(check.ok)}>{check.status || (check.ok ? "OK" : "UNAVAILABLE")}</StatusBadge></div>
            <p>{safeDetail(check.detail)}</p>
          </article>
        ))}
      </div>
    </div>
  );
}

function StartupPanel({ checks, loading, error }: { checks: StartupCheck["checks"] | null; loading: boolean; error: string }) {
  if (loading) return <LoadingState label="正在读取启动检查…" />;
  if (error) return <div className="settings-error" role="alert"><WarningCircle size={18} />{error}</div>;
  if (!checks) return <EmptyState title="暂无启动检查结果" />;
  return (
    <div className="settings-check-list">
      {checks.map((check) => (
        <div className="settings-check-row" key={check.name}>
          {check.ok ? <CheckCircle size={19} className="settings-check-ok" aria-label="检查通过" /> : <WarningCircle size={19} className="settings-check-bad" aria-label="检查未通过" />}
          <strong>{check.name}</strong><StatusBadge tone={detailTone(check.ok)}>{check.status || (check.ok ? "OK" : "UNAVAILABLE")}</StatusBadge><span>{safeDetail(check.detail)}</span>
        </div>
      ))}
    </div>
  );
}

function RecoveryPanel({ recovery, loading, error }: { recovery: RecoveryInfo | null; loading: boolean; error: string }) {
  if (loading) return <LoadingState label="正在读取恢复信息…" />;
  if (error) return <div className="settings-error" role="alert"><WarningCircle size={18} />{error}</div>;
  if (!recovery) return <EmptyState title="暂无恢复信息" />;
  const incomplete = Array.isArray(recovery.incomplete) ? recovery.incomplete : [];
  const broken = (recovery.integrity || []).filter((entry) => !entry.ok);
  return (
    <div className="settings-recovery">
      <div className="settings-recovery-summary"><strong>未完成任务 {incomplete.length} 个</strong><span>{incomplete.length ? "仅展示后端返回的未完成记录；本页不执行重跑。" : "当前没有未完成任务。"}</span></div>
      {incomplete.length > 0 && <div className="settings-recovery-list">{incomplete.map((run) => <div className="settings-recovery-row" key={`${run.group_name}-${run.run_date}`}><span>{run.group_name}</span><span>{run.run_date}</span><StatusBadge tone="warning">{run.status || "PENDING"}</StatusBadge></div>)}</div>}
      {broken.length > 0 && <div className="settings-warning" role="alert"><WarningCircle size={19} /><div><strong>完整性检查发现缺失文件</strong>{broken.map((entry) => <p key={`${entry.group_name}-${entry.run_date}`}>{entry.group_name} · {entry.run_date}：{entry.missing.join("、") || "未提供文件名"}</p>)}</div></div>}
      {incomplete.length === 0 && broken.length === 0 && <EmptyState title="恢复状态正常" description="没有待恢复任务或缺失产物。" />}
    </div>
  );
}

export default function Settings() {
  const { msg, toast } = useToast();
  const [tab, setTab] = useState<SettingsTab>("settings");
  const [values, setValues] = useState<SettingsValues>({});
  const [original, setOriginal] = useState<SettingsValues>({});
  const [settingsLoading, setSettingsLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [settingsError, setSettingsError] = useState("");
  const [health, setHealth] = useState<SystemHealth | null>(null);
  const [startup, setStartup] = useState<StartupCheck["checks"] | null>(null);
  const [recovery, setRecovery] = useState<RecoveryInfo | null>(null);
  const [healthError, setHealthError] = useState("");
  const [startupError, setStartupError] = useState("");
  const [recoveryError, setRecoveryError] = useState("");
  const [diagnosticsLoading, setDiagnosticsLoading] = useState(true);

  const loadSettings = async () => {
    setSettingsLoading(true);
    setSettingsError("");
    try {
      const next = await getSettings();
      setValues(next);
      setOriginal(next);
    } catch (error) {
      setSettingsError(`设置读取失败：${friendlyError(error, "本地服务暂不可用")}`);
    } finally {
      setSettingsLoading(false);
    }
  };

  const loadDiagnostics = async () => {
    setDiagnosticsLoading(true);
    setHealthError("");
    setStartupError("");
    setRecoveryError("");
    const [healthResult, startupResult, recoveryResult] = await Promise.allSettled([getSystemHealth(), getStartupChecks(), getRecoveryInfo()]);
    if (healthResult.status === "fulfilled") setHealth(healthResult.value);
    else setHealthError(`健康检查失败：${friendlyError(healthResult.reason, "本地服务暂不可用")}`);
    if (startupResult.status === "fulfilled") setStartup(startupResult.value.checks);
    else setStartupError(`启动检查失败：${friendlyError(startupResult.reason, "本地服务暂不可用")}`);
    if (recoveryResult.status === "fulfilled") setRecovery(recoveryResult.value);
    else setRecoveryError(`恢复信息读取失败：${friendlyError(recoveryResult.reason, "本地服务暂不可用")}`);
    setDiagnosticsLoading(false);
  };

  useEffect(() => {
    void loadSettings();
    void loadDiagnostics();
  }, []);

  const visibleGroups = useMemo(
    () => SETTING_GROUPS.map((group) => ({ ...group, keys: group.keys.filter((key) => Object.prototype.hasOwnProperty.call(values, key)) })),
    [values],
  );

  const save = async () => {
    const payload: SettingsValues = {};
    Object.entries(values).forEach(([key, value]) => {
      if (!Object.prototype.hasOwnProperty.call(original, key)) return;
      if (SENSITIVE_KEYS.has(key) && (!value || value === "******")) return;
      if (value !== original[key]) payload[key] = value;
    });
    if (!Object.keys(payload).length) {
      toast("没有需要保存的设置");
      return;
    }
    setSaving(true);
    try {
      await saveSettings(payload);
      await loadSettings();
      toast("设置已保存，并已重新读取运行时状态");
    } catch (error) {
      toast(`设置保存失败：${friendlyError(error, "请检查本地服务")}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="settings-page">
      <PageHeader
        title="设置中心"
        description="编辑本机真实运行配置，并查看健康、启动与恢复状态。"
        actions={<div className="settings-header-actions"><Button tone="secondary" onClick={() => void loadSettings()} busy={settingsLoading}><GearSix size={16} />重新读取</Button><Button onClick={() => void save()} busy={saving} disabled={settingsLoading}><CheckCircle size={16} />保存设置</Button></div>}
      />

      <div className="settings-secret-banner" id="settings-secret-note"><Key size={19} /><span>API Key、Token、密码等敏感值只显示掩码；留空或保持 <code>******</code> 不会修改现有密钥。</span></div>

      <div className="settings-tabs" role="tablist" aria-label="设置中心区域">
        <button type="button" role="tab" aria-selected={tab === "settings"} className={`settings-tab ${tab === "settings" ? "is-active" : ""}`} onClick={() => setTab("settings")}><GearSix size={17} />运行设置</button>
        <button type="button" role="tab" aria-selected={tab === "health"} className={`settings-tab ${tab === "health" ? "is-active" : ""}`} onClick={() => setTab("health")}><Heartbeat size={17} />系统健康</button>
        <button type="button" role="tab" aria-selected={tab === "startup"} className={`settings-tab ${tab === "startup" ? "is-active" : ""}`} onClick={() => setTab("startup")}><PlugsConnected size={17} />启动检查</button>
        <button type="button" role="tab" aria-selected={tab === "recovery"} className={`settings-tab ${tab === "recovery" ? "is-active" : ""}`} onClick={() => setTab("recovery")}><WarningCircle size={17} />恢复信息</button>
      </div>

      {tab === "settings" && (
        <section className="settings-form-area" aria-label="运行设置">
          {settingsError && <div className="settings-error" role="alert"><WarningCircle size={18} />{settingsError}</div>}
          {settingsLoading && <LoadingState label="正在读取真实设置…" />}
          {!settingsLoading && !settingsError && visibleGroups.every((group) => group.keys.length === 0) && <EmptyState title="后端没有返回可编辑设置" description="请确认本地服务已启动。" />}
          {!settingsLoading && !settingsError && visibleGroups.map((group) => {
            const GroupIcon = group.icon;
            return (
              <article className="settings-group card" key={group.id}>
                <div className="settings-group-head"><div className="settings-group-icon"><GroupIcon size={19} /></div><div><h2>{group.title}</h2><p>{group.description}</p></div></div>
                <div className="settings-fields">{group.keys.map((key) => <SettingField key={key} name={key} value={values[key] ?? ""} onChange={(value) => setValues((current) => ({ ...current, [key]: value }))} />)}</div>
              </article>
            );
          })}
        </section>
      )}

      {tab === "health" && <section className="settings-diagnostic-panel card" aria-label="系统健康"><div className="settings-panel-head"><div><h2>系统健康</h2><p>状态来自本地 `/api/v2/system/health`，不可用依赖保持真实阻塞状态。</p></div><Button tone="secondary" onClick={() => void loadDiagnostics()} busy={diagnosticsLoading}>重新检测</Button></div><HealthPanel health={health} loading={diagnosticsLoading} error={healthError} /></section>}
      {tab === "startup" && <section className="settings-diagnostic-panel card" aria-label="启动检查"><div className="settings-panel-head"><div><h2>启动检查</h2><p>展示服务启动时的真实检查结果，不在浏览器端安装或修改自启动。</p></div><Button tone="secondary" onClick={() => void loadDiagnostics()} busy={diagnosticsLoading}>重新读取</Button></div><StartupPanel checks={startup} loading={diagnosticsLoading} error={startupError} /></section>}
      {tab === "recovery" && <section className="settings-diagnostic-panel card" aria-label="恢复信息"><div className="settings-panel-head"><div><h2>恢复信息</h2><p>只读展示后端扫描到的未完成任务与输出完整性。</p></div><Button tone="secondary" onClick={() => void loadDiagnostics()} busy={diagnosticsLoading}>重新读取</Button></div><RecoveryPanel recovery={recovery} loading={diagnosticsLoading} error={recoveryError} /></section>}

      <Toast message={msg} />
    </div>
  );
}
