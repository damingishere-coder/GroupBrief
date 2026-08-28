import { Fragment, useEffect, useMemo, useState } from "react";
import {
  ArrowsClockwise,
  Flask,
  LinkSimple,
  MagnifyingGlass,
  PencilSimple,
  Plus,
  Trash,
  ToggleLeft,
  ToggleRight,
} from "@phosphor-icons/react";
import {
  GroupMatch,
  GroupV2,
  TestReadResult,
  bindGroupFromName,
  deleteGroup,
  listGroups,
  resolveGroups,
  syncWechatGroupNames,
  testReadGroup,
  updateGroup,
} from "../../api";
import {
  Button,
  ConfirmDialog,
  EmptyState,
  LoadingState,
  PageHeader,
  StatusBadge,
  Toast,
} from "../../components/common";
import { useToast } from "../../components/ui";
import { navigateToHash } from "../../navigation";

type GroupFilter = "all" | "enabled" | "disabled";
type ToggleField = "enabled" | "image_enabled";

function formatDateTime(value: string): string {
  if (!value) return "—";
  return value.replace("T", " ").slice(0, 16);
}

function ToggleSwitch({
  checked,
  label,
  busy,
  onChange,
}: {
  checked: boolean;
  label: string;
  busy: boolean;
  onChange: () => void;
}) {
  const Icon = checked ? ToggleRight : ToggleLeft;
  return (
    <button
      type="button"
      className={`groups-toggle ${checked ? "is-on" : ""}`}
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={onChange}
      disabled={busy}
      title={busy ? "保存中…" : label}
    >
      <Icon size={28} weight="fill" aria-hidden="true" />
      <span>{checked ? "已启用" : "已停用"}</span>
    </button>
  );
}

function TestResult({ result }: { result: TestReadResult }) {
  const tone = result.status === "OK" ? "success" : "warning";
  return (
    <div className="groups-test-result">
      <StatusBadge tone={tone}>{result.status}</StatusBadge>
      <span>Provider：{result.provider || "—"}</span>
      <span>读取总数：{result.raw_message_count} / 计入统计：{result.message_count}</span>
      <span>{result.detail || "无详细信息"}</span>
    </div>
  );
}

export default function Groups() {
  const { msg, toast } = useToast();
  const [groups, setGroups] = useState<GroupV2[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<GroupFilter>("all");
  const [searchName, setSearchName] = useState("");
  const [searching, setSearching] = useState(false);
  const [searchResult, setSearchResult] = useState<GroupMatch[]>([]);
  const [toggleBusy, setToggleBusy] = useState("");
  const [testBusy, setTestBusy] = useState<number | null>(null);
  const [testResults, setTestResults] = useState<Record<number, TestReadResult>>({});
  const [deleteTarget, setDeleteTarget] = useState<GroupV2 | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [syncingNames, setSyncingNames] = useState(false);

  const load = () => {
    setLoading(true);
    setLoadError("");
    listGroups()
      .then(setGroups)
      .catch((error: unknown) => {
        const message = String(error);
        setLoadError(message);
        toast(message);
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
  }, []);

  const filteredGroups = useMemo(() => {
    const normalizedQuery = query.trim().toLocaleLowerCase();
    return groups.filter((group) => {
      const matchesFilter = filter === "all" || (filter === "enabled" ? group.enabled : !group.enabled);
      if (!matchesFilter) return false;
      if (!normalizedQuery) return true;
      return [group.display_name, group.wechat_group_name, group.wechat_group_id, group.send_target]
        .filter(Boolean)
        .some((value) => value.toLocaleLowerCase().includes(normalizedQuery));
    });
  }, [filter, groups, query]);

  const toggle = (group: GroupV2, field: ToggleField) => {
    const next = !group[field];
    const key = `${group.id}:${field}`;
    if (toggleBusy) return;
    setToggleBusy(key);
    setGroups((current) => current.map((item) => item.id === group.id ? { ...item, [field]: next } : item));
    const body = field === "enabled" ? { enabled: next } : { image_enabled: next };
    updateGroup(group.id, body)
      .then(() => toast(`${field === "enabled" ? "启用状态" : "AI 图片开关"}已更新`))
      .catch((error: unknown) => {
        setGroups((current) => current.map((item) => item.id === group.id ? { ...item, [field]: !next } : item));
        toast(`保存失败：${String(error)}`);
      })
      .finally(() => setToggleBusy(""));
  };

  const testRead = (group: GroupV2) => {
    if (testBusy !== null) return;
    setTestBusy(group.id);
    testReadGroup(group.id)
      .then((result) => {
        setTestResults((current) => ({ ...current, [group.id]: result }));
        toast(`「${group.display_name}」测试读取完成`);
      })
      .catch((error: unknown) => toast(`读取失败：${String(error)}`))
      .finally(() => setTestBusy(null));
  };

  const confirmDelete = () => {
    if (!deleteTarget || deleteBusy) return;
    setDeleteBusy(true);
    deleteGroup(deleteTarget.id)
      .then(() => {
        toast(`已将「${deleteTarget.display_name}」移入归档回收站`);
        setDeleteTarget(null);
        load();
      })
      .catch((error: unknown) => toast(`删除失败：${String(error)}`))
      .finally(() => setDeleteBusy(false));
  };

  const doSearch = () => {
    const name = searchName.trim();
    if (!name || searching) {
      if (!name) toast("请输入真实微信群名称关键词");
      return;
    }
    setSearching(true);
    resolveGroups(name)
      .then((matches) => {
        setSearchResult(matches);
        if (matches.length === 0) toast("未找到匹配的真实微信群");
      })
      .catch((error: unknown) => toast(`搜索失败：${String(error)}`))
      .finally(() => setSearching(false));
  };

  const syncNames = () => {
    if (syncingNames) return;
    setSyncingNames(true);
    syncWechatGroupNames()
      .then((result) => {
        if (result.status === "unavailable") {
          toast(`微信群名同步不可用，已保留缓存名称：${result.detail || "数据源未返回群列表"}`);
        } else {
          toast(`微信群名同步完成：更新 ${result.updated.length} 个，未变化 ${result.unchanged} 个${result.skipped.length ? `，跳过 ${result.skipped.length} 个` : ""}`);
        }
        load();
      })
      .catch((error: unknown) => toast(`微信群名同步失败：${String(error)}`))
      .finally(() => setSyncingNames(false));
  };

  const bindMatch = (match: GroupMatch) => {
    bindGroupFromName({ name: searchName.trim(), group_id: match.id })
      .then((result) => {
        toast(result.restored ? "已恢复原群及历史归档，当前保持停用" : result.already_existed ? "该微信群已绑定" : "绑定成功");
        setSearchResult([]);
        setSearchName("");
        load();
      })
      .catch((error: unknown) => toast(`绑定失败：${String(error)}`));
  };

  if (loading && groups.length === 0) return <LoadingState label="正在加载群聊配置…" />;
  if (loadError && groups.length === 0) {
    return (
      <EmptyState
        title="群聊配置加载失败"
        description={loadError}
        action={<Button tone="secondary" onClick={load}>重新加载</Button>}
      />
    );
  }

  return (
    <div className="groups-page">
      <PageHeader
        title="群聊配置"
        description="管理真实微信群绑定、统计规则、生成和发送设置。"
        actions={
          <Button onClick={() => navigateToHash("/groups/new")}>
            <Plus size={18} aria-hidden="true" />
            新增群
          </Button>
        }
      />

      <section className="groups-bind-panel">
        <div className="groups-bind-copy">
          <div className="groups-section-icon"><LinkSimple size={19} aria-hidden="true" /></div>
          <div>
            <h2>按真实群名搜索并绑定</h2>
            <p>复用 WeChatDataAnalysis 的群解析能力，避免手动输入错误 ID。</p>
          </div>
        </div>
        <div className="groups-search-row">
          <label className="sr-only" htmlFor="group-bind-search">搜索真实微信群</label>
          <div className="groups-search-input">
            <MagnifyingGlass size={18} aria-hidden="true" />
            <input
              id="group-bind-search"
              value={searchName}
              placeholder="输入微信群名称关键词"
              onChange={(event) => setSearchName(event.target.value)}
              onKeyDown={(event) => event.key === "Enter" && doSearch()}
            />
          </div>
          <Button tone="secondary" onClick={doSearch} busy={searching}>搜索群</Button>
        </div>
        {searchResult.length > 0 && (
          <div className="groups-bind-results">
            {searchResult.map((match) => (
              <div className="groups-bind-result" key={match.id}>
                <div>
                  <strong>{match.name}</strong>
                  <span>{match.match_type === "exact" ? "精确匹配" : "模糊匹配"} · {match.provider}</span>
                </div>
                <Button tone="ghost" className="ui-button-compact" onClick={() => bindMatch(match)}>绑定</Button>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="groups-toolbar" aria-label="群聊筛选">
        <div className="groups-search-input groups-list-search">
          <MagnifyingGlass size={18} aria-hidden="true" />
          <label className="sr-only" htmlFor="group-list-search">搜索群聊</label>
          <input id="group-list-search" value={query} placeholder="搜索群名、微信群 ID 或发送目标" onChange={(event) => setQuery(event.target.value)} />
        </div>
        <div className="groups-filter-tabs" role="tablist" aria-label="群聊状态筛选">
          {(["all", "enabled", "disabled"] as const).map((item) => {
            const labels: Record<GroupFilter, string> = { all: "全部", enabled: "已启用", disabled: "已停用" };
            return (
              <button
                key={item}
                type="button"
                className={filter === item ? "is-active" : ""}
                role="tab"
                aria-selected={filter === item}
                onClick={() => setFilter(item)}
              >
                {labels[item]}
              </button>
            );
          })}
        </div>
        <span className="groups-result-count">显示 {filteredGroups.length} / {groups.length} 个群</span>
        <Button tone="secondary" className="ui-button-compact" onClick={syncNames} busy={syncingNames}>
          <ArrowsClockwise size={16} aria-hidden="true" />
          同步微信群名
        </Button>
        <Button tone="ghost" className="ui-button-compact" onClick={load} busy={loading}>
          <ArrowsClockwise size={16} aria-hidden="true" />
          刷新
        </Button>
      </section>

      {filteredGroups.length === 0 ? (
        <EmptyState
          title={groups.length === 0 ? "暂无群聊配置" : "没有匹配的群聊"}
          description={groups.length === 0 ? "可以新增群，或先按真实群名搜索并绑定。" : "请调整搜索关键词或状态筛选。"}
          action={groups.length === 0 ? <Button onClick={() => navigateToHash("/groups/new")}><Plus size={17} aria-hidden="true" />新增群</Button> : undefined}
        />
      ) : (
        <div className="groups-table-wrap">
          <table className="groups-table">
            <caption className="sr-only">群聊配置列表</caption>
            <thead>
              <tr>
                <th>群名称 / 绑定信息</th>
                <th>启用状态</th>
                <th>统计规则</th>
                <th>发送时间</th>
                <th>排行榜配置</th>
                <th>AI 图片</th>
                <th>发送目标</th>
                <th>最近配置</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {filteredGroups.map((group) => {
                const testResult = testResults[group.id];
                const currentName = group.wechat_group_name || group.display_name || "未命名群";
                const archiveNameDiffers = Boolean(group.display_name && group.display_name !== currentName);
                return (
                  <Fragment key={group.id}>
                    <tr>
                      <td data-label="群名称 / 绑定信息">
                        <div className="groups-name-cell">
                          <strong>{currentName}</strong>
                          {archiveNameDiffers && <span>归档名称：{group.display_name}</span>}
                          {group.wechat_group_id && <small>ID：{group.wechat_group_id}</small>}
                        </div>
                      </td>
                      <td data-label="启用状态">
                        <ToggleSwitch checked={group.enabled} label={`${group.display_name} 启用状态`} busy={toggleBusy === `${group.id}:enabled`} onChange={() => toggle(group, "enabled")} />
                      </td>
                      <td data-label="统计规则"><span className="groups-muted-cell">{group.schedule_rule || "daily_previous_day"}</span></td>
                      <td data-label="发送时间"><strong>{group.send_time || "—"}</strong></td>
                      <td data-label="排行榜配置">
                        <div className="groups-template-cell">
                          <strong>{group.ranking_template || "default"}</strong>
                          <span>Prompt：{group.image_prompt_template || "default"}</span>
                        </div>
                      </td>
                      <td data-label="AI 图片">
                        <ToggleSwitch checked={group.image_enabled} label={`${group.display_name} AI 图片开关`} busy={toggleBusy === `${group.id}:image_enabled`} onChange={() => toggle(group, "image_enabled")} />
                      </td>
                      <td data-label="发送目标">
                        <div className="groups-target-cell">
                          <span>{group.effective_send_target || "未设置"}</span>
                          <small>{group.send_target_mode === "manual" ? "人工覆盖" : "自动跟随"}</small>
                        </div>
                      </td>
                      <td data-label="最近配置"><span className="groups-muted-cell">{formatDateTime(group.updated_at)}</span></td>
                      <td data-label="操作">
                        <div className="groups-row-actions">
                          <Button tone="ghost" className="ui-button-compact groups-action-button" onClick={() => testRead(group)} busy={testBusy === group.id} title="测试读取" aria-label={`测试读取 ${group.display_name}`}>
                            <Flask size={16} aria-hidden="true" />
                          </Button>
                          <Button tone="secondary" className="ui-button-compact groups-action-button" onClick={() => navigateToHash(`/groups/${group.id}`)} title="编辑群配置" aria-label={`编辑 ${group.display_name}`}>
                            <PencilSimple size={16} aria-hidden="true" />
                          </Button>
                          <Button tone="danger" className="ui-button-compact groups-action-button" onClick={() => setDeleteTarget(group)} title="删除群配置" aria-label={`删除 ${group.display_name}`}>
                            <Trash size={16} aria-hidden="true" />
                          </Button>
                        </div>
                      </td>
                    </tr>
                    {testResult && (
                      <tr className="groups-test-row">
                        <td colSpan={9}><TestResult result={testResult} /></td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <ConfirmDialog
        open={Boolean(deleteTarget)}
        title="移入归档回收站？"
        description={deleteTarget ? `「${deleteTarget.display_name}」将停止任务和微信发送，并移入归档回收站；群配置与全部历史信息都不会删除。` : ""}
        confirmLabel="移入回收站"
        busy={deleteBusy}
        onConfirm={confirmDelete}
        onCancel={() => !deleteBusy && setDeleteTarget(null)}
      />
      <Toast message={msg} />
    </div>
  );
}
