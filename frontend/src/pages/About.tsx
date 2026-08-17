export default function About() {
  return (
    <div>
      <div className="page-header">
        <div className="page-title">关于</div>
        <div className="page-sub">群报 GroupBrief · 微信群聊日报小工具</div>
      </div>
      <div className="card">
        <div className="row">
          <div
            className="brand-logo"
            style={{ width: 56, height: 56, fontSize: 24, borderRadius: 14 }}
          >
            报
          </div>
          <div>
            <div style={{ fontSize: 20, fontWeight: 700 }}>群报 GroupBrief</div>
            <div className="muted">版本 1.0.0 · V1 本地版</div>
          </div>
        </div>
        <div style={{ marginTop: 16, color: "var(--text-secondary)", lineHeight: 1.8 }}>
          <p>V1 核心链路：本地读取 → 精确排行 → DeepSeek 生图 Prompt → 邮件。</p>
          <p>V2 规划：Codex Automation 自动生图 → 微信自动发送。</p>
          <p style={{ marginTop: 8 }}>
            隐私：聊天记录仅在本机读取，只有生成 Prompt 所需文本会提交给 DeepSeek API。
          </p>
        </div>
      </div>
    </div>
  );
}
