import { useEffect, useMemo, useState } from "react";
import { CheckCircle, FloppyDisk, MagnifyingGlass, Sparkle, WarningCircle } from "@phosphor-icons/react";

import {
  batchUpdateGroupImageTheme,
  type BatchImageThemeResponse,
  type GroupV2,
  type ImageThemeOption,
  resolveImageTheme,
} from "../../../api";
import { Button, EmptyState, LoadingState } from "../../../components/common";
import { ImageThemePicker } from "../../../components/ImageThemePicker";
import { describeApiError } from "./model";
import type { ToastFn } from "./useAIImageCatalogs";

interface ImageStylePanelProps {
  groups: GroupV2[];
  themes: ImageThemeOption[];
  catalogLoading: boolean;
  groupsError: string;
  themesError: string;
  loadCatalogs: () => Promise<void>;
  toast: ToastFn;
}

function groupName(group: GroupV2): string {
  return group.display_name || group.wechat_group_name || `群 ${group.id}`;
}

function themeLabel(group: GroupV2, themes: ImageThemeOption[]): string {
  const label = group.image_theme === "custom"
    ? group.image_theme_custom || "自定义描述"
    : themes.find((theme) => theme.key === group.image_theme)?.label || group.image_theme || "AI 自由发挥";
  return (group.image_theme_remaining_runs ?? 0) > 0
    ? `${label}（剩余 ${group.image_theme_remaining_runs} 次）`
    : label;
}

export function ImageStylePanel({
  groups,
  themes,
  catalogLoading,
  groupsError,
  themesError,
  loadCatalogs,
  toast,
}: ImageStylePanelProps) {
  const [selectedIds, setSelectedIds] = useState<number[]>([]);
  const [groupQuery, setGroupQuery] = useState("");
  const [theme, setTheme] = useState("ai_free");
  const [custom, setCustom] = useState("");
  const [themeText, setThemeText] = useState("");
  const [themeConfirmed, setThemeConfirmed] = useState(false);
  const [applyCount, setApplyCount] = useState(1);
  const [themeError, setThemeError] = useState("");
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState<BatchImageThemeResponse | null>(null);

  useEffect(() => {
    const available = new Set(groups.map((group) => group.id));
    setSelectedIds((current) => current.filter((groupId) => available.has(groupId)));
  }, [groups]);

  const filteredGroups = useMemo(() => {
    const normalized = groupQuery.trim().toLocaleLowerCase();
    if (!normalized) return groups;
    return groups.filter((group) => `${groupName(group)} ${group.id} ${group.wechat_group_id}`.toLocaleLowerCase().includes(normalized));
  }, [groupQuery, groups]);
  const selectedSet = useMemo(() => new Set(selectedIds), [selectedIds]);
  const selectedVisibleCount = filteredGroups.filter((group) => selectedSet.has(group.id)).length;
  const selectedThemeLabel = theme === "custom"
    ? custom.trim() || "自定义描述"
    : themes.find((item) => item.key === theme)?.label || theme;
  const limitedTheme = theme !== "ai_free" && theme !== "random_preset";
  const sharedPreview = themeConfirmed
    ? `【共享视觉风格】\n${themeText || selectedThemeLabel}\n\n【应用范围】\n${limitedTheme ? `用于接下来 ${applyCount} 次成功生图，耗尽后自动回到每日随机。` : "作为持续模式使用，不消耗次数。"}\n每个群原有 Prompt 模板、群名、统计周期与内容变量均保持不变。`
    : "请先在上方打开风格中心并点击“使用这个风格”。确认后再选择需要同步的群。";

  const applyTheme = async (key: string, customValue = "") => {
    const normalizedCustom = key === "custom" ? customValue.trim() : "";
    setTheme(key);
    setCustom(normalizedCustom);
    setThemeConfirmed(true);
    if (key !== "ai_free" && key !== "random_preset") setApplyCount(1);
    setThemeError("");
    setResult(null);
    try {
      const resolved = await resolveImageTheme({
        image_theme: key,
        image_theme_custom: normalizedCustom,
      });
      setThemeText(resolved.theme_text);
    } catch (reason) {
      setThemeText("");
      setThemeError(`风格预览失败：${describeApiError(reason)}`);
    }
  };

  const toggleGroup = (groupId: number) => {
    setResult(null);
    setSelectedIds((current) => current.includes(groupId)
      ? current.filter((item) => item !== groupId)
      : [...current, groupId]);
  };

  const selectVisible = () => {
    setResult(null);
    setSelectedIds((current) => Array.from(new Set([
      ...current,
      ...filteredGroups.map((group) => group.id),
    ])));
  };

  const clearSelection = () => {
    setResult(null);
    setSelectedIds([]);
  };

  const saveStyle = async () => {
    if (!themeConfirmed || !selectedIds.length) return;
    const requestedIds = [...selectedIds];
    setSaving(true);
    try {
      const response = await batchUpdateGroupImageTheme({
        group_ids: requestedIds,
        image_theme: theme,
        image_theme_custom: theme === "custom" ? custom.trim() : "",
        image_theme_apply_count: limitedTheme ? applyCount : 1,
      });
      setResult(response);
      setSelectedIds(response.failed.map((item) => item.group_id));
      await loadCatalogs();
      if (response.status === "success") {
        toast(`已把「${selectedThemeLabel}」应用到 ${response.success.length} 个群${limitedTheme ? `，每群 ${applyCount} 次` : ""}`);
      } else if (response.status === "partial") {
        toast(`已保存 ${response.success.length} 个群，${response.failed.length} 个群需要重试`);
      } else {
        toast(`${response.failed.length} 个群保存失败，已保留为重试项`);
      }
    } catch (reason) {
      const message = `批量保存失败：${describeApiError(reason)}`;
      setResult(null);
      toast(message);
    } finally {
      setSaving(false);
    }
  };

  const retryingFailures = Boolean(result?.failed.length) && selectedIds.length === result?.failed.length;
  const saveDisabled = saving
    || !themeConfirmed
    || !selectedIds.length
    || Boolean(themeError)
    || (theme === "custom" && !custom.trim());

  return (
    <section className="ai-images-theme-card" aria-label="群聊指定风格">
      <div className="ai-images-theme-heading">
        <div><h2>设置群聊生图风格</h2><p>先确认一套共享风格，再勾选一个或多个群同步应用；停用群也可以预先配置。</p></div>
        <Sparkle size={22} />
      </div>
      {catalogLoading && !groups.length ? <LoadingState label="正在读取群配置与主题目录…" />
        : groupsError && !groups.length ? <EmptyState title="群配置加载失败" description={groupsError} action={<Button tone="secondary" onClick={loadCatalogs}>重新加载</Button>} />
          : !groups.length ? <EmptyState title="暂无群配置" description="当前数据库中确实没有群，请先在群聊配置中创建群。" />
            : (
              <>
                <div className="ai-images-style-layout">
                  <div className="ai-images-style-config">
                    <ImageThemePicker themes={themes} value={theme} customValue={custom} onConfirm={applyTheme} label="共享风格" loading={catalogLoading} error={themesError} disabled={saving} />
                    <div className={`ai-images-style-confirmation ${themeConfirmed ? "is-confirmed" : ""}`}>
                      {themeConfirmed ? <CheckCircle size={18} weight="fill" /> : <WarningCircle size={18} />}
                      <span>{themeConfirmed ? `已确认：${selectedThemeLabel}` : "尚未确认风格，群选择不会触发保存"}</span>
                    </div>
                    {themeConfirmed && limitedTheme && (
                      <label className="ai-images-theme-use-count">
                        <span><b>使用次数</b><small>默认只用下一次，耗尽后自动回到每日随机</small></span>
                        <select value={applyCount} disabled={saving} onChange={(event) => setApplyCount(Number(event.target.value))}>
                          {[1, 2, 3, 5, 10, 30].map((count) => <option key={count} value={count}>{count} 次</option>)}
                        </select>
                      </label>
                    )}
                    <div className="ai-images-prompt-preview ai-images-default-preview">
                      <span>共享风格注入预览</span>
                      <pre aria-live="polite">{sharedPreview}</pre>
                      <small>各群原有 Prompt 模板不会被覆盖；新配置只从后续新建运行开始生效。</small>
                    </div>
                  </div>

                  <section className="ai-images-group-picker" aria-label="选择目标群">
                    <div className="ai-images-group-picker-head">
                      <div><b>选择目标群</b><span>已选 {selectedIds.length} 个</span></div>
                      <div><button type="button" disabled={!filteredGroups.length || selectedVisibleCount === filteredGroups.length} onClick={selectVisible}>全选当前列表</button><button type="button" disabled={!selectedIds.length} onClick={clearSelection}>清空</button></div>
                    </div>
                    <label className="ai-images-group-search"><MagnifyingGlass size={16} /><input type="search" value={groupQuery} placeholder="搜索群名或 ID" onChange={(event) => setGroupQuery(event.target.value)} /></label>
                    <div className="ai-images-group-checklist">
                      {filteredGroups.map((group) => (
                        <label key={group.id} className={selectedSet.has(group.id) ? "is-selected" : ""}>
                          <input type="checkbox" checked={selectedSet.has(group.id)} disabled={saving} onChange={() => toggleGroup(group.id)} />
                          <span className="ai-images-group-copy"><b>{groupName(group)}</b><small>ID {group.id} · 当前：{themeLabel(group, themes)}</small></span>
                          <em className={group.enabled ? "is-enabled" : "is-disabled"}>{group.enabled ? "已启用" : "已停用"}</em>
                        </label>
                      ))}
                      {!filteredGroups.length && <div className="image-theme-picker-state">没有匹配的群。</div>}
                    </div>
                  </section>
                </div>

                <div className="ai-images-style-submit-row">
                  <span>{themeConfirmed ? `将「${selectedThemeLabel}」应用到后续${limitedTheme ? ` ${applyCount} 次成功生图` : "所有新运行"}` : "请先确认共享风格"}</span>
                  <Button tone="primary" onClick={saveStyle} busy={saving} disabled={saveDisabled}><FloppyDisk size={16} />{retryingFailures ? "重试失败群" : `应用到 ${selectedIds.length} 个群`}</Button>
                </div>

                {result && (
                  <div className={`ai-images-batch-result is-${result.status}`} role="status">
                    <strong>{result.status === "success" ? "全部保存成功" : result.status === "partial" ? "部分群保存成功" : "本次保存失败"}</strong>
                    {result.success.length > 0 && <div><b>成功（{result.success.length}）</b><ul>{result.success.map((item) => <li key={item.group_id}>{item.group_name} · ID {item.group_id}</li>)}</ul></div>}
                    {result.failed.length > 0 && <div><b>失败（{result.failed.length}）</b><ul>{result.failed.map((item) => <li key={item.group_id}>ID {item.group_id}：{item.reason}</li>)}</ul><small>失败群已保留为选中状态，可直接点击“重试失败群”。</small></div>}
                  </div>
                )}
                {groupsError && <div className="ai-images-run-error">{groupsError}</div>}
                {themesError && <div className="ai-images-run-error">{themesError} <Button tone="ghost" className="ui-button-compact" onClick={loadCatalogs}>重新加载</Button></div>}
                {themeError && <div className="ai-images-run-error">{themeError}</div>}
              </>
            )}
    </section>
  );
}
