import { useEffect, useState } from "react";
import { Button, StatusBadge } from "./common";
import { getWechatAccount, scanWechatAccounts, bindWechatAccount, verifyWechatAccount, WechatAccount, WechatAccountCandidate } from "../api";

export function WechatAccountCard() {
  const [account, setAccount] = useState<WechatAccount | null>(null);
  const [candidates, setCandidates] = useState<WechatAccountCandidate[]>([]);
  const [selected, setSelected] = useState("");
  const [name, setName] = useState("大明同学");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<{ ok: boolean; detail: string } | null>(null);

  useEffect(() => { getWechatAccount().then((value) => { setAccount(value); setResult(value.verification || null); if (value.account_name) setName(value.account_name); }).catch((err) => setError(String(err))); }, []);

  const run = async (action: () => Promise<void>) => {
    setBusy(true); setError("");
    try { await action(); } catch (err) { setError(String(err)); } finally { setBusy(false); }
  };

  return <article className="settings-group card" aria-label="微信发送账号">
    <div className="settings-group-head"><div><h2>微信发送账号</h2><p>发送前核验左上角固定头像，再核验目标群。只有一个微信窗口匹配时才发送。</p></div></div>
    {account && <div className="wechat-account-summary">
      {account.avatar && <img src={account.avatar} alt="已绑定的标准头像" width="64" height="64" />}
      <div><strong>{account.account_name || "尚未绑定账号"}</strong><p>{account.detail}</p><StatusBadge tone={account.bound ? "success" : "warning"}>{account.bound ? "标准头像已保存" : "自动发送已阻止"}</StatusBadge></div>
    </div>}
    <div className="settings-header-actions">
      <Button tone="secondary" disabled={busy} onClick={() => void run(async () => { setCandidates([]); setSelected(""); setResult(null); const value = await scanWechatAccounts(); setCandidates(value.candidates); if (!value.candidates.length) setError("未找到已登录的微信主窗口"); })}>扫描微信头像</Button>
      <Button tone="secondary" disabled={busy || !account?.bound} onClick={() => void run(async () => setResult(await verifyWechatAccount()))}>仅核验账号</Button>
    </div>
    {candidates.length > 0 && <fieldset className="wechat-account-candidates"><legend>选择发送账号的头像</legend>
      {candidates.map((candidate, index) => <label key={candidate.candidate_id || index}>
        <input type="radio" name="wechat-account-avatar" value={candidate.candidate_id || ""} disabled={!candidate.ok || busy} checked={Boolean(candidate.candidate_id) && selected === candidate.candidate_id} onChange={() => setSelected(candidate.candidate_id!)} />
        {candidate.avatar && <img src={candidate.avatar} alt={`${candidate.label}头像`} width="64" height="64" />}
        <span>{candidate.label}{candidate.detail && <small>{candidate.detail}</small>}</span>
      </label>)}
      <label>账号名称<input aria-label="微信发送账号名称" value={name} maxLength={80} disabled={busy} onChange={(event) => setName(event.target.value)} /></label>
      {selected && <p>确认所选头像属于“{name}”。保存后，所有微信发送都必须匹配这个头像。</p>}
      <Button disabled={busy || !selected || !name.trim()} onClick={() => void run(async () => { const value = await bindWechatAccount(selected, name); setAccount(value); setCandidates([]); setSelected(""); setResult(value.verification || null); })}>确认绑定所选头像</Button>
    </fieldset>}
    {result && <p role="status"><StatusBadge tone={result.ok ? "success" : "warning"}>{result.ok ? "账号核验通过" : "账号核验未通过"}</StatusBadge> {result.detail}</p>}
    {error && <p className="settings-error" role="alert">{error}</p>}
    <p>头像无法读取、未匹配或多个窗口匹配时会停止发送。扫描和核验不会发送消息。</p>
  </article>;
}
