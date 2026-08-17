import { useState } from "react";
import { get, put, type SystemStatus } from "../api";
import { useFetch, useToast } from "../components/ui";

export default function SettingsPage() {
  const { toast } = useToast();
  const { data: status } = useFetch(() => get<SystemStatus>("/system/status"));
  const [settings, setSettings] = useState<Record<string, string>>({});

  const load = async () => {
    const s = await get<Record<string, string>>("/settings");
    setSettings(s);
  };

  useFetch(async () => {
    await load();
    return null;
  });

  const set = (k: string, v: string) => setSettings((s) => ({ ...s, [k]: v }));

  const save = async () => {
    try {
      await put("/settings", { values: settings });
      toast("设置已保存");
    } catch (e) {
      toast(String(e));
    }
  };

  const field = (
    key: string,
    label: string,
    placeholder = "",
    type: "text" | "password" | "number" = "text"
  ) => (
    <div className="field">
      <label>{label}</label>
      <input
        type={type}
        value={settings[key] ?? ""}
        placeholder={placeholder}
        onChange={(e) => set(key, e.target.value)}
      />
    </div>
  );

  return (
    <div>
      <div className="page-header">
        <div className="page-title">配置设置</div>
        <div className="page-sub">API Key 与密码只显示掩码，不会完整回显</div>
      </div>

      <div className="card">
        <div className="card-title">自动任务</div>
        <div className="row">
          <div className="field" style={{ marginRight: 16 }}>
            <label>生成时间</label>
            <input
              type="text"
              value={settings.schedule_generate_time ?? "08:45"}
              onChange={(e) => set("schedule_generate_time", e.target.value)}
            />
          </div>
          <div className="field">
            <label>邮件时间</label>
            <input
              type="text"
              value={settings.schedule_email_time ?? "09:00"}
              onChange={(e) => set("schedule_email_time", e.target.value)}
            />
          </div>
        </div>
        <div className="muted" style={{ fontSize: 13 }}>
          时区 {status?.timezone ?? "Asia/Shanghai"} · 周日自动跳过
        </div>
      </div>

      <div className="card">
        <div className="card-title">微信数据 Provider</div>
        <div className="row">
          <div className="field" style={{ marginRight: 16 }}>
            <label>主 Provider</label>
            <select
              value={settings.history_provider_primary ?? "wechat_data_analysis"}
              onChange={(e) => set("history_provider_primary", e.target.value)}
            >
              <option value="wechat_data_analysis">WeChatDataAnalysis</option>
              <option value="wechat_cli">wechat-cli</option>
              <option value="mock">Mock（测试数据）</option>
            </select>
          </div>
          <div className="field">
            <label>备用 Provider</label>
            <select
              value={settings.history_provider_fallback ?? "wechat_cli"}
              onChange={(e) => set("history_provider_fallback", e.target.value)}
            >
              <option value="wechat_data_analysis">WeChatDataAnalysis</option>
              <option value="wechat_cli">wechat-cli</option>
              <option value="mock">Mock（测试数据）</option>
            </select>
          </div>
        </div>
        {field("wechat_data_dir", "微信数据目录（WeChatDataAnalysis）", "留空自动探测")}
        {field("wechat_export_dir", "导出 JSON 目录（WECHAT_EXPORT_DIR）", "留空用 data/wechat_export")}
        {field("wechat_cli_path", "wechat-cli 路径", "留空则使用 PATH")}
        <div className="card-title" style={{ marginTop: 10 }}>
          WeChatDataAnalysis 本地 MCP（可选）
        </div>
        {field("wechat_mcp_url", "MCP 地址（仅本机回环）", "http://127.0.0.1:10392/mcp")}
        {field("wechat_mcp_token", "MCP 令牌", "", "password")}
        {field("wechat_mcp_account", "微信账号标识（多账号可选）")}
        {field("wechat_mcp_timeout_seconds", "MCP 超时（秒）", "10")}
      </div>

      <div className="card">
        <div className="card-title">DeepSeek V4 Flash</div>
        {field("ai_base_url", "API Base URL")}
        {field("ai_api_key", "API Key", "sk-…", "password")}
        {field("ai_model", "Model")}
        <div className="row">
          <div className="field" style={{ marginRight: 16 }}>
            <label>Timeout（秒）</label>
            <input
              type="number"
              value={settings.ai_timeout_seconds ?? "60"}
              onChange={(e) => set("ai_timeout_seconds", e.target.value)}
            />
          </div>
          <div className="field">
            <label>Retry</label>
            <input
              type="number"
              value={settings.ai_max_retries ?? "3"}
              onChange={(e) => set("ai_max_retries", e.target.value)}
            />
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-title">邮件</div>
        {field("email_enabled", "启用邮件（true/false）", "false")}
        {field("email_recipient", "收件地址")}
        {field("email_from", "发件人")}
        {field("email_smtp_host", "SMTP 主机")}
        <div className="row">
          <div className="field" style={{ marginRight: 16 }}>
            <label>SMTP 端口</label>
            <input
              type="number"
              value={settings.email_smtp_port ?? "465"}
              onChange={(e) => set("email_smtp_port", e.target.value)}
            />
          </div>
          <div className="field">
            <label>SSL（true/false）</label>
            <input
              type="text"
              value={settings.email_use_ssl ?? "true"}
              onChange={(e) => set("email_use_ssl", e.target.value)}
            />
          </div>
        </div>
        {field("email_smtp_user", "SMTP 用户名")}
        {field("email_smtp_password", "SMTP 密码", "", "password")}
        {field(
          "email_send_partial_report",
          "部分成功也发送（true/false）",
          "true"
        )}
      </div>

      <button className="btn" onClick={save}>
        保存设置
      </button>
    </div>
  );
}
