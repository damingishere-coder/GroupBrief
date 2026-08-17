import { useMemo, useState } from "react";
import {
  del,
  get,
  post,
  put,
  type Group,
  type LatestReport,
} from "../api";
import { copyText, downloadText, useToast } from "../components/ui";

export default function Groups() {
  const { msg, toast } = useToast();
  const [groups, setGroups] = useState<Group[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [reports, setReports] = useState<LatestReport[]>([]);
  const [adding, setAdding] = useState(false);
  const [newName, setNewName] = useState("");
  const [newWxName, setNewWxName] = useState("");
  const [discovered, setDiscovered] = useState<
    { group_id: string; group_name: string; member_count: number }[]
  >([]);
  const [busy, setBusy] = useState(false);
  const [editingPrompt, setEditingPrompt] = useState(false);
  const [promptDraft, setPromptDraft] = useState("");
  const [newWxId, setNewWxId] = useState("");
  const [reportDate, setReportDate] = useState(() => {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(
      d.getDate()
    ).padStart(2, "0")}`;
  });
  const [testResult, setTestResult] = useState<string>("");

  const refresh = async () => {
    const [g, r] = await Promise.all([
      get<Group[]>("/groups"),
      get<LatestReport[]>("/reports/latest"),
    ]);
    setGroups(g);
    setReports(r);
    if (g.length > 0) {
      setActiveId((prev) =>
        prev !== null && g.some((x) => x.id === prev) ? prev : g[0].id
      );
    } else {
      setActiveId(null);
    }
  };

  useMemo(() => {
    refresh().catch((e) => toast(String(e)));
  }, []);

  const active = groups.find((g) => g.id === activeId) ?? null;
  const report = active
    ? reports.find((r) => r.group_run_id === activeId) ?? null
    : null;

  const generate = async (force: boolean, groupId?: number) => {
    setBusy(true);
    try {
      const res = await post<{ run_id: number }>("/reports/generate", {
        report_date: reportDate,
        group_id: groupId ?? undefined,
        force,
      });
      toast(res.run_id ? "生成完成" : "生成任务已提交");
      await refresh();
    } catch (e) {
      toast(`生成失败：${e}`);
    } finally {
      setBusy(false);
    }
  };

  const generateAll = async () => {
    setBusy(true);
    try {
      const res = await post<{ run_id: number }>("/reports/generate", {
        report_date: reportDate,
        force: false,
      });
      toast(`全部生成完成（run ${res.run_id}）`);
      await refresh();
    } catch (e) {
      toast(`生成失败：${e}`);
    } finally {
      setBusy(false);
    }
  };

  const testRead = async () => {
    if (!active) return;
    setTestResult("测试中…");
    try {
      const res = await post<{
        provider: string;
        status: string;
        detail: string;
        message_count: number;
      }>(`/groups/${active.id}/test-read`);
      setTestResult(
        `Provider: ${res.provider} · 状态: ${res.status} · 消息数: ${res.message_count}\n${res.detail}`
      );
    } catch (e) {
      setTestResult(`测试失败：${e}`);
    }
  };

  const resolveAndBind = async () => {
    const name = prompt("输入真实微信群名称（将从本地微信数据解析并绑定）：");
    if (!name || !name.trim()) return;
    try {
      const res = await post<{ id: number; bound: boolean; already_existed: boolean }>(
        "/groups/from-name",
        { name: name.trim() }
      );
      toast(
        res.bound
          ? res.already_existed
            ? "已绑定到已有群"
            : "群名解析并绑定成功"
          : "解析失败"
      );
      await refresh();
    } catch (e) {
      toast(`绑定失败：${e}`);
    }
  };

  const sendEmail = async () => {
    try {
      const res = await post<{ ok: boolean; detail: string }>("/email/send");
      toast(res.ok ? "邮件已发送" : `发送失败：${res.detail}`);
    } catch (e) {
      toast(String(e));
    }
  };

  const savePrompt = async () => {
    if (!active) return;
    try {
      await put(`/reports/${report?.id ?? 0}/prompt`, { text: promptDraft });
      toast("Prompt 已保存");
      setEditingPrompt(false);
      await refresh();
    } catch (e) {
      toast(`保存失败：${e}`);
    }
  };

  const toggleEnabled = async (g: Group) => {
    try {
      await put(`/groups/${g.id}`, { enabled: !g.enabled });
      await refresh();
    } catch (e) {
      toast(String(e));
    }
  };

  const removeGroup = async (g: Group) => {
    if (!window.confirm(`确认删除群「${g.display_name || g.wechat_group_name}」？`))
      return;
    try {
      await del(`/groups/${g.id}`);
      toast("已删除");
      await refresh();
    } catch (e) {
      toast(String(e));
    }
  };

  const addGroup = async () => {
    if (!newName.trim()) {
      toast("请填写显示名称");
      return;
    }
    try {
      await post("/groups", {
        display_name: newName.trim(),
        wechat_group_id: newWxId,
        wechat_group_name: newWxName.trim() || newWxId,
      });
      setAdding(false);
      setNewName("");
      setNewWxName("");
      setNewWxId("");
      toast("群已添加");
      await refresh();
    } catch (e) {
      toast(String(e));
    }
  };

  const openAdd = async () => {
    setAdding(true);
    try {
      const list = await get<
        { group_id: string; group_name: string; member_count: number }[]
      >("/groups/discover");
      setDiscovered(list);
    } catch {
      setDiscovered([]);
    }
  };

  const pickDiscovered = (item: { group_id: string; group_name: string }) => {
    setNewWxId(item.group_id);
    setNewWxName(item.group_name);
    setNewName(item.group_name);
  };

  return (
    <div>
      <div className="page-header">
        <div className="page-title">群聊管理</div>
        <div className="page-sub">添加、删除、启停群聊，生成并预览每日群报</div>
      </div>

      <div className="tabs" style={{ marginBottom: 20 }}>
        {groups.map((g) => (
          <button
            key={g.id}
            className={`tab ${g.id === activeId ? "active" : ""}`}
            onClick={() => setActiveId(g.id)}
          >
            {g.display_name || g.wechat_group_name || `群 ${g.id}`}
            {!g.enabled ? "（停用）" : ""}
          </button>
        ))}
        <button
          className="tab-add"
          onClick={openAdd}
        >
          + 添加群聊
        </button>
      </div>

      {adding && (
        <div className="card">
          <div className="card-title">添加群聊</div>
          {discovered.length > 0 && (
            <div style={{ marginBottom: 16 }}>
              <div className="muted" style={{ fontSize: 13, marginBottom: 8 }}>
                从 Provider 发现的群聊（点击选择，避免输入错误）：
              </div>
              <div className="row" style={{ gap: 8 }}>
                {discovered.map((item) => (
                  <button
                    key={item.group_id}
                    className="btn btn-sm btn-ghost"
                    onClick={() => pickDiscovered(item)}
                  >
                    {item.group_name || item.group_id}
                    {item.member_count ? `（${item.member_count}人）` : ""}
                  </button>
                ))}
              </div>
            </div>
          )}
          <div className="row" style={{ gap: 10 }}>
            <div className="field" style={{ margin: 0 }}>
              <label>显示名称</label>
              <input
                type="text"
                placeholder="例如：Eason张UED-4群"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
              />
            </div>
            <div className="field" style={{ margin: 0 }}>
              <label>微信真实群 ID</label>
              <input
                type="text"
                placeholder="Provider 返回的稳定 ID"
                value={newWxId}
                onChange={(e) => setNewWxId(e.target.value)}
              />
            </div>
            <div className="field" style={{ margin: 0 }}>
              <label>微信群名称（可选）</label>
              <input
                type="text"
                placeholder="绑定微信真实群名"
                value={newWxName}
                onChange={(e) => setNewWxName(e.target.value)}
              />
            </div>
            <div className="row" style={{ alignSelf: "flex-end" }}>
              <button className="btn btn-sm" onClick={addGroup}>
                保存
              </button>
              <button
                className="btn btn-sm btn-ghost"
                onClick={() => setAdding(false)}
              >
                取消
              </button>
            </div>
          </div>
        </div>
      )}

      {!active ? (
        <div className="empty-state">
          <div className="big">还没有群聊</div>
          <div>点击「+ 添加群聊」创建第一个群</div>
        </div>
      ) : (
        <>
          <div className="card">
            <div className="row">
              <div>
                <div className="card-title" style={{ marginBottom: 4 }}>
                  {active.display_name || active.wechat_group_name}
                </div>
                <div className="muted" style={{ fontSize: 13 }}>
                  微信群：{active.wechat_group_name || "未绑定"} · ID：
                  {active.wechat_group_id || "—"}
                </div>
              </div>
              <div className="spacer" />
              <button
                className="btn btn-sm"
                onClick={() => toggleEnabled(active)}
              >
                {active.enabled ? "停用" : "启用"}
              </button>
              <button
                className="btn btn-sm btn-danger"
                onClick={() => removeGroup(active)}
              >
                删除
              </button>
            </div>
            <div className="toolbar" style={{ marginTop: 16 }}>
              <input
                type="date"
                value={reportDate}
                onChange={(e) => setReportDate(e.target.value)}
                style={{ width: 170 }}
              />
              <button className="btn btn-sm" disabled={busy} onClick={() => generate(false)}>
                生成群报
              </button>
              <button
                className="btn btn-sm btn-secondary"
                disabled={busy}
                onClick={() => generate(true)}
              >
                重新生成
              </button>
              <button
                className="btn btn-sm"
                disabled={busy}
                onClick={generateAll}
              >
                全部生成
              </button>
              <button
                className="btn btn-sm btn-secondary"
                onClick={sendEmail}
              >
                手动发邮件
              </button>
              <button className="btn btn-sm btn-ghost" onClick={testRead}>
                测试读取
              </button>
              <button className="btn btn-sm btn-ghost" onClick={resolveAndBind}>
                按群名绑定
              </button>
              <button
                className="btn btn-sm btn-ghost"
                disabled={!report}
                onClick={() => report && copyText(report.ranking_text, toast)}
              >
                复制排行榜
              </button>
              <button
                className="btn btn-sm btn-ghost"
                disabled={!report}
                onClick={() =>
                  report &&
                  downloadText(report.ranking_text, `${active.id}_ranking.txt`)
                }
              >
                导出排行榜
              </button>
              <button
                className="btn btn-sm btn-ghost"
                disabled={!report}
                onClick={() => report && copyText(report.prompt_text, toast)}
              >
                复制 Prompt
              </button>
              <button
                className="btn btn-sm btn-ghost"
                disabled={!report}
                onClick={() =>
                  report &&
                  downloadText(report.prompt_text, `${active.id}_image_prompt.txt`)
                }
              >
                导出 Prompt
              </button>
              <button
                className="btn btn-sm btn-ghost"
                disabled={!report}
                onClick={async () => {
                  try {
                    const p = await get<{ subject: string; body: string }>(
                      "/email/preview"
                    );
                    window.alert(`邮件预览\n\n主题：${p.subject}\n\n${p.body}`);
                  } catch (e) {
                    toast(String(e));
                  }
                }}
              >
                预览邮件
              </button>
            </div>
            {testResult && (
              <pre className="panel" style={{ marginTop: 12 }}>
                {testResult}
              </pre>
            )}
          </div>

          <div className="card">
            <div className="card-title">发言排行榜</div>
            {report?.ranking_text ? (
              <pre className="panel">{report.ranking_text}</pre>
            ) : (
              <div className="empty-state">
                <div className="big">暂无排行数据</div>
                <div>点击「生成群报」开始统计</div>
              </div>
            )}
          </div>

          <div className="card">
            <div className="row" style={{ marginBottom: 12 }}>
              <div className="card-title" style={{ marginBottom: 0 }}>
                GPT 生图 Prompt
              </div>
              <div className="spacer" />
              {!editingPrompt ? (
                <button
                  className="btn btn-sm btn-secondary"
                  disabled={!report}
                  onClick={() => {
                    setPromptDraft(report?.prompt_text ?? "");
                    setEditingPrompt(true);
                  }}
                >
                  编辑
                </button>
              ) : (
                <>
                  <button className="btn btn-sm" onClick={savePrompt}>
                    保存
                  </button>
                  <button
                    className="btn btn-sm btn-ghost"
                    onClick={() => setEditingPrompt(false)}
                  >
                    取消
                  </button>
                </>
              )}
            </div>
            {editingPrompt ? (
              <textarea
                className="panel"
                value={promptDraft}
                onChange={(e) => setPromptDraft(e.target.value)}
              />
            ) : report?.prompt_text ? (
              <pre className="panel">{report.prompt_text}</pre>
            ) : (
              <div className="empty-state">
                <div className="big">暂无 Prompt</div>
                <div>生成群报后，DeepSeek 会基于当天聊天事件自动生成</div>
              </div>
            )}
          </div>

          <div className="card">
            <div className="card-title">海报预览（V2）</div>
            <div className="empty-state">
              <div className="big">海报预览</div>
              <div>V2 即将支持</div>
              {report?.poster_file && (
                <div className="muted" style={{ marginTop: 8 }}>
                  {report.poster_file}
                </div>
              )}
            </div>
          </div>
        </>
      )}

      {msg && <div className="toast">{msg}</div>}
    </div>
  );
}
