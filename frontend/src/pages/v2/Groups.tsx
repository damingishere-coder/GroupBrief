import { useEffect, useState } from "react";
import {
  DiscoveredGroup,
  GroupMatch,
  GroupV2,
  bindGroupFromName,
  del,
  discoverGroups,
  get,
  post,
  put,
  resolveGroups,
  testReadGroup,
} from "../../api";
import { useToast } from "../../components/ui";

const EMPTY_GROUP: Partial<GroupV2> = {
  display_name: "",
  wechat_group_id: "",
  enabled: true,
  send_time: "08:30",
  schedule_rule: "weekday_default",
  summary_model: "deepseek-v4-flash",
  prompt_model: "deepseek-v4-flash",
  image_enabled: true,
  send_target: "",
  ranking_template: "default",
  image_prompt_template: "default",
};

function GroupForm({
  initial,
  onSave,
  onCancel,
}: {
  initial: Partial<GroupV2>;
  onSave: (g: Partial<GroupV2>) => void;
  onCancel: () => void;
}) {
  const { msg, toast } = useToast();
  const [form, setForm] = useState<Partial<GroupV2>>({ ...EMPTY_GROUP, ...initial });
  const [discovered, setDiscovered] = useState<DiscoveredGroup[]>([]);
  const [showDiscover, setShowDiscover] = useState(false);
  const set = (k: keyof GroupV2, v: unknown) => setForm((f) => ({ ...f, [k]: v }));

  const loadDiscover = () => {
    discoverGroups()
      .then((list) => {
        setDiscovered(list);
        setShowDiscover(true);
        toast(`从微信发现 ${list.length} 个群`);
      })
      .catch((e) => toast(String(e)));
  };

  const pick = (item: DiscoveredGroup) => {
    setForm((f) => ({
      ...f,
      display_name: item.group_name,
      wechat_group_id: item.group_id,
      wechat_group_name: item.group_name,
      send_target: item.group_name,
    }));
    toast(`已选择「${item.group_name}」`);
  };

  return (
    <div className="card form-card">
      <div className="card-title">{initial.id ? "编辑群" : "新增群"}</div>

      {!initial.id && (
        <div className="discover-bar">
          <button type="button" className="btn btn-sm btn-secondary" onClick={loadDiscover}>
            从微信发现群（避免手输群 ID）
          </button>
          {showDiscover && (
            <div className="discover-list">
              {discovered.length === 0 && <span className="muted">未发现群（请确认 WeChatDataAnalysis 已运行）</span>}
              {discovered.map((item) => (
                <button
                  key={item.group_id}
                  type="button"
                  className="discover-item"
                  onClick={() => pick(item)}
                  title={item.group_id}
                >
                  {item.group_name}
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      <div className="form-grid">
        <label className="field">
          群名称
          <input value={form.display_name || ""} onChange={(e) => set("display_name", e.target.value)} />
        </label>
        <label className="field">
          微信群 ID
          <input value={form.wechat_group_id || ""} onChange={(e) => set("wechat_group_id", e.target.value)} />
        </label>
        <label className="field">
          发送时间
          <input value={form.send_time || "08:30"} onChange={(e) => set("send_time", e.target.value)} />
        </label>
        <label className="field">
          发送目标（微信群名）
          <input value={form.send_target || ""} onChange={(e) => set("send_target", e.target.value)} />
        </label>
        <label className="field">
          统计周期规则
          <select value={form.schedule_rule || "weekday_default"} onChange={(e) => set("schedule_rule", e.target.value)}>
            <option value="weekday_default">工作日默认（周一=周五~周日，周六日不生成）</option>
          </select>
        </label>
        <label className="field">
          排行榜模板
          <input value={form.ranking_template || "default"} onChange={(e) => set("ranking_template", e.target.value)} />
        </label>
        <label className="field">
          生图 Prompt 模板
          <input
            value={form.image_prompt_template || "default"}
            onChange={(e) => set("image_prompt_template", e.target.value)}
          />
        </label>
        <label className="field">
          Prompt 模型
          <input value={form.prompt_model || "deepseek-v4-flash"} onChange={(e) => set("prompt_model", e.target.value)} />
        </label>
        <label className="field switch-field">
          <input type="checkbox" checked={form.enabled !== false} onChange={(e) => set("enabled", e.target.checked)} />
          启用该群
        </label>
        <label className="field switch-field">
          <input type="checkbox" checked={form.image_enabled !== false} onChange={(e) => set("image_enabled", e.target.checked)} />
          启用生图
        </label>
      </div>
      <div className="form-actions">
        <button className="btn" onClick={() => onSave(form)}>
          保存
        </button>
        <button className="btn btn-ghost" onClick={onCancel}>
          取消
        </button>
      </div>
      {msg && <div className="toast">{msg}</div>}
    </div>
  );
}

export default function Groups() {
  const { msg, toast } = useToast();
  const [groups, setGroups] = useState<GroupV2[]>([]);
  const [editing, setEditing] = useState<Partial<GroupV2> | null>(null);
  const [loading, setLoading] = useState(true);
  // 按群名搜索绑定
  const [searchName, setSearchName] = useState("");
  const [searching, setSearching] = useState(false);
  const [searchResult, setSearchResult] = useState<GroupMatch[]>([]);
  // 测试读取结果
  const [testReadingId, setTestReadingId] = useState<number | null>(null);

  const load = () => {
    setLoading(true);
    get<GroupV2[]>("/groups")
      .then(setGroups)
      .catch((e) => toast(String(e)))
      .finally(() => setLoading(false));
  };
  useEffect(load, []);

  const save = (g: Partial<GroupV2>) => {
    const p = g.id ? put<{ id: number }>(`/groups/${g.id}`, g) : post<{ id: number }>("/groups", g);
    p.then(() => {
      toast("已保存");
      setEditing(null);
      load();
    }).catch((e) => toast(String(e)));
  };

  const remove = (g: GroupV2) => {
    if (!window.confirm(`确认删除群「${g.display_name}」？此操作不可恢复。`)) return;
    del<{ ok: boolean }>(`/groups/${g.id}`)
      .then(() => {
        toast("已删除");
        load();
      })
      .catch((e) => toast(String(e)));
  };

  // 按群名搜索真实群
  const doSearch = () => {
    if (!searchName.trim()) {
      toast("请输入群名关键词");
      return;
    }
    setSearching(true);
    resolveGroups(searchName.trim())
      .then((list) => {
        setSearchResult(list);
        if (list.length === 0) toast("未找到匹配的群");
        setSearching(false);
      })
      .catch((e) => {
        toast(String(e));
        setSearching(false);
      });
  };

  const doBind = (m: GroupMatch) => {
    bindGroupFromName({ name: searchName.trim(), group_id: m.id })
      .then((res) => {
        toast(res.already_existed ? `已绑定到已有群（id=${res.id}）` : `绑定成功（id=${res.id}）`);
        setSearchResult([]);
        setSearchName("");
        load();
      })
      .catch((e) => {
        const text = String(e);
        toast(text.length > 120 ? text.slice(0, 120) + "…" : text);
      });
  };

  // 测试读取（显示该群最近周期的消息数）
  const testRead = (g: GroupV2) => {
    setTestReadingId(g.id);
    testReadGroup(g.id)
      .then((res) => {
        toast(
          `群「${g.display_name}」\n${res.provider} · ${res.status}\n消息数：${res.message_count}\n${res.detail}`
        );
      })
      .catch((e) => toast(String(e)))
      .finally(() => setTestReadingId(null));
  };

  return (
    <div>
      <div className="page-header">
        <div>
          <div className="page-title">群管理</div>
          <div className="page-sub">管理启用的微信群与日报配置</div>
        </div>
        <button className="btn" onClick={() => setEditing({})}>
          ＋ 新增群
        </button>
      </div>

      {/* 按群名搜索绑定（复用 V1 能力） */}
      <div className="card search-bind">
        <div className="search-bind-row">
          <input
            placeholder="按真实微信群名称搜索并绑定（如：茶馆）"
            value={searchName}
            onChange={(e) => setSearchName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && doSearch()}
          />
          <button className="btn btn-secondary btn-sm" onClick={doSearch} disabled={searching}>
            {searching ? "搜索中…" : "搜索群"}
          </button>
        </div>
        {searchResult.length > 0 && (
          <div className="search-result">
            {searchResult.map((m) => (
              <div key={m.id} className="search-result-row">
                <span>
                  {m.name}
                  <span className="muted">（{m.match_type === "exact" ? "精确匹配" : "模糊匹配"} · {m.provider}）</span>
                </span>
                <button className="btn btn-sm" onClick={() => doBind(m)}>
                  绑定
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      {editing && <GroupForm initial={editing} onSave={save} onCancel={() => setEditing(null)} />}

      <div className="group-list">
        {loading && <div className="empty-state">加载中…</div>}
        {!loading && groups.length === 0 && <div className="empty-state">暂无群，可点击「新增群」或在上方「搜索群」绑定。</div>}
        {groups.map((g) => (
          <div className="card group-row" key={g.id}>
            <div className="group-row-main">
              <div className="group-row-title">
                {g.display_name}
                {!g.enabled && <span className="badge badge-warn">已停用</span>}
              </div>
              <div className="muted">
                {g.wechat_group_name || g.wechat_group_id || "未绑定"} · 发送 {g.send_time} ·{" "}
                {g.image_enabled ? "生图开" : "生图关"} · 排行模板 {g.ranking_template} · Prompt 模板{" "}
                {g.image_prompt_template}
              </div>
            </div>
            <div className="row-actions">
              <button className="btn btn-sm btn-secondary" onClick={() => testRead(g)} disabled={testReadingId === g.id}>
                {testReadingId === g.id ? "读取中…" : "测试读取"}
              </button>
              <button className="btn btn-sm btn-secondary" onClick={() => setEditing({ ...g })}>
                编辑
              </button>
              <button className="btn btn-sm btn-danger" onClick={() => remove(g)}>
                删除
              </button>
            </div>
          </div>
        ))}
      </div>
      {msg && <div className="toast">{msg}</div>}
    </div>
  );
}
