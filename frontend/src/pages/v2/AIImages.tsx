import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowCounterClockwise,
  Copy,
  FloppyDisk,
  ImageSquare,
  PaperPlaneTilt,
  Play,
  Sparkle,
  WarningCircle,
} from "@phosphor-icons/react";
import {
  getGroupImagePrompt,
  getImagePromptTemplate,
  getRunDetail,
  getRunPrompt,
  getRuns,
  getV2File,
  GroupImagePromptConfig,
  GroupV2,
  ImageThemeOption,
  listGroups,
  listImageThemes,
  pipelineSend,
  regenerateRunImage,
  rebuildRunPrompt,
  resolveImageTheme,
  restoreRunPrompt,
  RunPromptConfig,
  saveRunPrompt,
  updateGroup,
  V2Run,
} from "../../api";
import {
  Button,
  ConfirmDialog,
  EmptyState,
  ImagePreviewTrigger,
  ImageViewer,
  LoadingState,
  StatusBadge,
  Toast,
} from "../../components/common";
import { copyText, useToast } from "../../components/ui";

const STATUS_LABELS: Record<string, string> = {
  PENDING: "待生成",
  DATA_READY: "数据就绪",
  RANKING_READY: "排行完成",
  PROMPT_READY: "Prompt 完成",
  IMAGE_READY: "图片完成",
  READY_TO_SEND: "待发送",
  SENT: "已发送",
  FAILED: "失败",
};

const REGEN_LABELS: Record<string, string> = {
  idle: "未重新生图",
  queued: "已排队",
  running: "生成中",
  fallback_queued: "已转入 Codex Desktop 队列",
  ready_for_review: "新图待审核",
  prompt_rebuilt: "Prompt 已重建，等待生图",
  failed: "重新生图失败",
  sent: "新图已发送",
};

interface ImageDetail {
  run: V2Run;
  files: string[];
}

function runKey(run: V2Run): string {
  return `${run.group_name}\u0000${run.run_date}`;
}

function formatDateTime(value: unknown): string {
  if (!value) return "—";
  return String(value).replace("T", " ").slice(0, 16);
}

function describeLoadError(scope: string, reason: unknown): string {
  const raw = reason instanceof Error ? reason.message : String(reason);
  let detail = raw;
  try {
    const parsed = JSON.parse(raw) as { detail?: unknown };
    if (typeof parsed.detail === "string") detail = parsed.detail;
  } catch {
    // 非 JSON 错误直接保留原始信息。
  }
  if (detail === "Not Found" || detail.includes('"detail":"Not Found"')) {
    return `${scope}接口尚未加载。当前后端可能仍是旧版本，请重启 GroupBrief 服务后重试。`;
  }
  return `${scope}加载失败：${detail}`;
}

function statusTone(status: string): "success" | "warning" | "danger" | "info" | "neutral" {
  if (["SENT", "IMAGE_READY", "READY_TO_SEND"].includes(status)) return "success";
  if (status === "FAILED") return "danger";
  if (["PROMPT_READY", "RANKING_READY"].includes(status)) return "info";
  if (["PENDING", "DATA_READY"].includes(status)) return "warning";
  return "neutral";
}

function StatusPill({ status }: { status: string }) {
  const normalized = status.toUpperCase();
  return <StatusBadge tone={statusTone(normalized)}>{STATUS_LABELS[normalized] || status || "未知"}</StatusBadge>;
}

function renderGroupPreview(content: string, group: GroupV2 | undefined, themeText: string): string {
  const name = group?.display_name || group?.wechat_group_name || "群名称（选择目标群后填入）";
  const variables: Record<string, string> = {
    "{{group_name}}": name,
    "{{period_start}}": "统计开始时间（生成时自动填入）",
    "{{period_end}}": "统计结束时间（生成时自动填入）",
    "{{report_date}}": "统计日期（从统计周期自动填入）",
    "{{message_count}}": "消息数（生成时自动填入）",
    "{{speaker_count}}": "发言人数（生成时自动填入）",
    "{{image_theme}}": themeText || "（在上方输入指定风格后自动填入）",
    "{{layout_name}}": "整张海报版式（生成时自动选择）",
    "{{layout_instruction}}": "版式结构指令（生成时自动填入）",
  };
  return Object.entries(variables).reduce(
    (preview, [token, value]) => preview.split(token).join(value),
    content.replace(/<!--[\s\S]*?-->/g, "").trim(),
  ).trim();
}

export default function AIImages() {
  const { msg, toast } = useToast();
  const [groups, setGroups] = useState<GroupV2[]>([]);
  const [themes, setThemes] = useState<ImageThemeOption[]>([]);
  const [runs, setRuns] = useState<V2Run[]>([]);
  const [dateFilter, setDateFilter] = useState("");
  const [groupFilter, setGroupFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [selectedKey, setSelectedKey] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [groupsError, setGroupsError] = useState("");
  const [themesError, setThemesError] = useState("");

  const [defaultGroupId, setDefaultGroupId] = useState<number | null>(null);
  const [defaultConfig, setDefaultConfig] = useState<GroupImagePromptConfig | null>(null);
  const [globalDefaultPrompt, setGlobalDefaultPrompt] = useState("");
  const [defaultTemplateError, setDefaultTemplateError] = useState("");
  const [defaultCustom, setDefaultCustom] = useState("");
  const [defaultThemeText, setDefaultThemeText] = useState("");
  const [defaultThemeError, setDefaultThemeError] = useState("");
  const [defaultStyleTouched, setDefaultStyleTouched] = useState(false);
  const defaultStyleTouchedRef = useRef(false);
  const [defaultLoading, setDefaultLoading] = useState(false);
  const [defaultSaving, setDefaultSaving] = useState(false);
  const [defaultError, setDefaultError] = useState("");
  const [defaultReloadVersion, setDefaultReloadVersion] = useState(0);

  const [detail, setDetail] = useState<ImageDetail | null>(null);
  const [runPrompt, setRunPrompt] = useState<RunPromptConfig | null>(null);
  const [runDraft, setRunDraft] = useState("");
  const [runTheme, setRunTheme] = useState("random_preset");
  const [runCustom, setRunCustom] = useState("");
  const [detailLoading, setDetailLoading] = useState(false);
  const [runSaving, setRunSaving] = useState(false);
  const [rebuildingPrompt, setRebuildingPrompt] = useState(false);
  const [regenerating, setRegenerating] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [sending, setSending] = useState(false);
  const [sendConfirmOpen, setSendConfirmOpen] = useState(false);
  const [imageLoadError, setImageLoadError] = useState(false);
  const [imageViewerOpen, setImageViewerOpen] = useState(false);
  const [imageVersion, setImageVersion] = useState(0);
  const [detailError, setDetailError] = useState("");
  const [runPromptError, setRunPromptError] = useState("");
  const [detailReloadVersion, setDetailReloadVersion] = useState(0);

  const loadRuns = () => {
    setLoading(true);
    setError("");
    getRuns(dateFilter || undefined)
      .then((data) => {
        setRuns(data.runs);
        setSelectedKey((current) => data.runs.some((run) => runKey(run) === current)
          ? current
          : data.runs[0] ? runKey(data.runs[0]) : "");
      })
      .catch((reason: unknown) => {
        const message = `图片运行记录加载失败：${String(reason)}`;
        setError(message);
        toast(message);
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    loadRuns();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dateFilter]);

  const loadCatalogs = useCallback(async () => {
    setCatalogLoading(true);
    setGroupsError("");
    setThemesError("");
    setDefaultTemplateError("");
    const [groupResult, themeResult, promptResult] = await Promise.allSettled([
      listGroups(),
      listImageThemes(),
      getImagePromptTemplate("default"),
    ]);
    if (groupResult.status === "fulfilled") {
      const groupData = groupResult.value;
      setGroups(groupData);
      setDefaultGroupId((current) => groupData.some((group) => group.id === current)
        ? current
        : null);
    } else {
      const message = describeLoadError("群配置", groupResult.reason);
      setGroupsError(message);
      toast(message);
    }
    if (themeResult.status === "fulfilled") {
      setThemes(themeResult.value.themes);
    } else {
      const message = describeLoadError("主题目录", themeResult.reason);
      setThemesError(message);
      toast(message);
    }
    if (promptResult.status === "fulfilled") {
      setGlobalDefaultPrompt(promptResult.value.content);
    } else {
      const message = describeLoadError("默认 Prompt", promptResult.reason);
      setDefaultTemplateError(message);
      toast(message);
    }
    setCatalogLoading(false);
  }, [toast]);

  useEffect(() => {
    void loadCatalogs();
  }, [loadCatalogs]);

  useEffect(() => {
    if (defaultGroupId === null) {
      setDefaultConfig(null);
      setDefaultError("");
      return;
    }
    let cancelled = false;
    setDefaultLoading(true);
    setDefaultConfig(null);
    setDefaultError("");
    getGroupImagePrompt(defaultGroupId)
      .then((config) => {
        if (cancelled) return;
        setDefaultConfig(config);
        if (!defaultStyleTouchedRef.current) {
          const savedCustom = config.image_theme === "custom" ? config.image_theme_custom || "" : "";
          setDefaultCustom(savedCustom);
          setDefaultThemeText(savedCustom ? config.resolved_theme?.theme_text || "" : "");
        }
      })
      .catch((reason: unknown) => {
        if (cancelled) return;
        const message = describeLoadError("群级 Prompt", reason);
        setDefaultError(message);
        toast(message);
      })
      .finally(() => {
        if (!cancelled) setDefaultLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [defaultGroupId, defaultReloadVersion, toast]);

  useEffect(() => {
    const custom = defaultCustom.trim();
    if (!custom) {
      setDefaultThemeText("");
      setDefaultThemeError("");
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(() => {
      resolveImageTheme({
        image_theme: "custom",
        image_theme_custom: custom,
        group_id: defaultGroupId ?? undefined,
      }).then((resolved) => {
        if (cancelled) return;
        setDefaultThemeText(resolved.theme_text);
        setDefaultThemeError("");
      }).catch((reason: unknown) => {
        if (cancelled) return;
        setDefaultThemeText("");
        setDefaultThemeError(`指定风格预览失败：${String(reason)}`);
      });
    }, 250);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [defaultCustom, defaultGroupId]);

  const filteredRuns = runs.filter((run) => {
    const query = groupFilter.trim().toLocaleLowerCase();
    return (!query || run.group_name.toLocaleLowerCase().includes(query))
      && (statusFilter === "all" || run.status.toUpperCase() === statusFilter);
  });

  useEffect(() => {
    if (!filteredRuns.length) {
      setSelectedKey("");
    } else if (!filteredRuns.some((run) => runKey(run) === selectedKey)) {
      setSelectedKey(runKey(filteredRuns[0]));
    }
  }, [filteredRuns, selectedKey]);

  useEffect(() => {
    if (!selectedKey) {
      setDetail(null);
      setRunPrompt(null);
      setDetailError("");
      setRunPromptError("");
      return;
    }
    const selected = runs.find((run) => runKey(run) === selectedKey);
    if (!selected) return;
    let cancelled = false;
    setDetailLoading(true);
    setDetail(null);
    setRunPrompt(null);
    setDetailError("");
    setRunPromptError("");
    setImageLoadError(false);
    setImageViewerOpen(false);
    Promise.allSettled([
      getRunDetail(selected.group_name, selected.run_date),
      getRunPrompt(selected.group_name, selected.run_date),
    ]).then(([detailResult, promptResult]) => {
      if (cancelled) return;
      if (detailResult.status === "fulfilled") {
        setDetail(detailResult.value);
      } else {
        const message = describeLoadError("运行详情", detailResult.reason);
        setDetailError(message);
        toast(message);
      }
      if (promptResult.status === "fulfilled") {
        const prompt = promptResult.value;
        setRunPrompt(prompt);
        setRunDraft(prompt.content);
        setRunTheme(prompt.image_theme || "random_preset");
        setRunCustom(prompt.image_theme_custom || "");
      } else {
        const message = describeLoadError("当天 Prompt", promptResult.reason);
        setRunPromptError(message);
        toast(message);
      }
    }).finally(() => {
      if (!cancelled) setDetailLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [detailReloadVersion, runs, selectedKey, toast]);

  const regenStatus = String(detail?.run.image_regen_status || "idle");
  const currentImageSrc = detail
    ? `${getV2File(detail.run.group_name, detail.run.run_date, "daily_image.png")}?v=${imageVersion}`
    : "";
  useEffect(() => {
    if (!detail || !["queued", "running", "fallback_queued"].includes(regenStatus)) return;
    const groupName = detail.run.group_name;
    const runDate = detail.run.run_date;
    const timer = window.setInterval(() => {
      getRunDetail(groupName, runDate).then((next) => {
        setDetail(next);
        const nextStatus = String(next.run.image_regen_status || "idle");
        if (["ready_for_review", "failed"].includes(nextStatus)) {
          setImageVersion((current) => current + 1);
          loadRuns();
        }
      }).catch(() => undefined);
    }, regenStatus === "fallback_queued" ? 5000 : 2000);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [detail?.run.group_name, detail?.run.run_date, regenStatus]);

  const selectedDefaultGroup = groups.find((group) => group.id === defaultGroupId);
  const savedDefaultCustom = defaultConfig?.image_theme === "custom"
    ? defaultConfig.image_theme_custom.trim()
    : "";
  const defaultDirty = Boolean(defaultConfig) && defaultCustom.trim() !== savedDefaultCustom;
  const defaultPreviewTheme = defaultCustom.trim()
    ? defaultThemeText || `指定风格「${defaultCustom.trim()}」（正在生成完整约束）`
    : "";
  const defaultPreview = useMemo(
    () => renderGroupPreview(defaultConfig?.content || globalDefaultPrompt, selectedDefaultGroup, defaultPreviewTheme),
    [defaultConfig?.content, defaultPreviewTheme, globalDefaultPrompt, selectedDefaultGroup],
  );
  const runDirty = Boolean(runPrompt) && runDraft !== runPrompt?.content;

  const saveDefaultStyle = async () => {
    if (!defaultConfig || defaultGroupId === null) return;
    setDefaultSaving(true);
    try {
      const custom = defaultCustom.trim();
      await updateGroup(defaultGroupId, {
        image_theme: custom ? "custom" : "random_preset",
        image_theme_custom: custom,
      });
      const refreshed = await getGroupImagePrompt(defaultGroupId);
      setDefaultConfig(refreshed);
      setDefaultCustom(refreshed.image_theme === "custom" ? refreshed.image_theme_custom || "" : "");
      setDefaultThemeText(refreshed.image_theme === "custom" ? refreshed.resolved_theme?.theme_text || "" : "");
      defaultStyleTouchedRef.current = false;
      setDefaultStyleTouched(false);
      toast(custom ? `已把指定风格保存到「${selectedDefaultGroup?.display_name || selectedDefaultGroup?.wechat_group_name || `群 ${defaultGroupId}`}」` : "已清除该群的指定风格");
    } catch (reason) {
      toast(`指定风格保存失败：${String(reason)}`);
    } finally {
      setDefaultSaving(false);
    }
  };

  const applyRunTheme = async (key: string, custom = runCustom) => {
    setRunTheme(key);
    try {
      const resolved = await resolveImageTheme({
        image_theme: key,
        image_theme_custom: custom,
        prompt: runDraft,
        group_id: typeof detail?.run.group_id === "number" || typeof detail?.run.group_id === "string"
          ? detail.run.group_id
          : undefined,
        run_date: detail?.run.run_date,
      });
      setRunTheme(key);
      setRunDraft(resolved.prompt);
      if (key === "random_preset") toast(`该群当天随机风格已固定为：${resolved.display_name}`);
    } catch (reason) {
      toast(`当天主题替换失败：${String(reason)}`);
    }
  };

  const saveCurrentPrompt = async () => {
    if (!detail || !runPrompt) return;
    setRunSaving(true);
    try {
      const saved = await saveRunPrompt(detail.run.group_name, detail.run.run_date, {
        content: runDraft,
        expected_revision: runPrompt.revision,
        image_theme: runTheme,
        image_theme_custom: runCustom.trim(),
      });
      setRunPrompt(saved);
      setRunDraft(saved.content);
      toast("当天 Prompt 已保存；尚未重新生图，也不会自动发送");
    } catch (reason) {
      toast(`当天 Prompt 保存失败：${String(reason)}`);
    } finally {
      setRunSaving(false);
    }
  };

  const restoreCurrentPrompt = async () => {
    if (!detail) return;
    setRestoring(true);
    try {
      const restored = await restoreRunPrompt(detail.run.group_name, detail.run.run_date);
      setRunPrompt(restored);
      setRunDraft(restored.content);
      setRunTheme(restored.image_theme || "random_preset");
      setRunCustom(restored.image_theme_custom || "");
      toast("已恢复首次编辑前的 Prompt");
    } catch (reason) {
      toast(`恢复 Prompt 失败：${String(reason)}`);
    } finally {
      setRestoring(false);
    }
  };

  const rebuildCurrentPrompt = async () => {
    if (!detail) return;
    if (runDirty) {
      toast("当前 Prompt 有未保存修改，请先保存或恢复后再重建");
      return;
    }
    setRebuildingPrompt(true);
    try {
      const rebuilt = await rebuildRunPrompt(detail.run.group_name, detail.run.run_date);
      const prompt = await getRunPrompt(detail.run.group_name, detail.run.run_date);
      setDetail((current) => current ? { ...current, run: rebuilt.run } : current);
      setRunPrompt(prompt);
      setRunDraft(prompt.content);
      setRunTheme(prompt.image_theme || "random_preset");
      setRunCustom(prompt.image_theme_custom || "");
      loadRuns();
      toast("已从当天 messages.json 重建 Prompt；没有重新读取微信，也没有生图");
    } catch (reason) {
      toast(`Prompt 重建失败：${String(reason)}`);
    } finally {
      setRebuildingPrompt(false);
    }
  };

  const regenerate = async () => {
    if (!detail || !runPrompt) return;
    if (runDirty) {
      toast("请先保存当天 Prompt，再重新生图");
      return;
    }
    setRegenerating(true);
    try {
      const accepted = await regenerateRunImage(detail.run.group_name, detail.run.run_date);
      setDetail((current) => current ? { ...current, run: accepted.run } : current);
      toast("已加入单队列；生成成功后会停在人工审核状态");
    } catch (reason) {
      toast(`重新生图请求失败：${String(reason)}`);
    } finally {
      setRegenerating(false);
    }
  };

  const confirmSend = async () => {
    if (!detail) return;
    const groupId = Number(detail.run.group_id || groups.find((group) => group.display_name === detail.run.group_name || group.wechat_group_name === detail.run.group_name)?.id || 0);
    if (!groupId) {
      toast("运行记录缺少可用群 ID，无法发送");
      return;
    }
    setSending(true);
    try {
      const result = await pipelineSend({ group_id: groupId, run_date: detail.run.run_date, confirm_regenerated: true });
      if (result.result.status !== "sent") throw new Error(String(result.result.error || result.result.detail || "发送未成功"));
      setSendConfirmOpen(false);
      toast("已确认并发送文字与图片");
      loadRuns();
    } catch (reason) {
      toast(`发送失败：${String(reason)}`);
    } finally {
      setSending(false);
    }
  };

  if (loading && !runs.length && !groups.length) return <LoadingState label="正在加载 AI 图片工作台…" />;
  if (error && !runs.length) return <EmptyState title="AI 图片页面加载失败" description={error} action={<Button tone="secondary" onClick={loadRuns}>重新加载</Button>} />;

  return (
    <div className="ai-images-page">
      <section className="ai-images-theme-card" aria-label="群聊指定风格">
        <div className="ai-images-theme-heading">
          <div><h2>设置群聊生图风格</h2><p>先输入风格并确认 Prompt，再选择要保存到的群聊。</p></div>
          <Sparkle size={22} />
        </div>
        {catalogLoading && !groups.length ? <LoadingState label="正在读取群配置与主题目录…" /> : groupsError && !groups.length ? <EmptyState title="群配置加载失败" description={groupsError} action={<Button tone="secondary" onClick={loadCatalogs}>重新加载</Button>} /> : !groups.length ? <EmptyState title="暂无群配置" description="当前数据库中确实没有群，请先在群聊配置中创建群。" /> : (
          <>
            <label className="ai-images-theme-custom-field">
              <span><b>指定风格</b><small>{defaultCustom.length} / 80</small></span>
              <input
                maxLength={80}
                value={defaultCustom}
                placeholder="例如：复古港漫、黏土定格、宋代工笔长卷"
                onChange={(event) => {
                  defaultStyleTouchedRef.current = true;
                  setDefaultStyleTouched(true);
                  setDefaultThemeText("");
                  setDefaultCustom(event.target.value);
                }}
              />
            </label>
            <div className="ai-images-prompt-preview ai-images-default-preview">
              <span>生成时使用的 Prompt 预览</span>
              <pre aria-live="polite">{defaultPreview || "Prompt 预览暂不可用"}</pre>
            </div>
            <div className="ai-images-style-target-row">
              <label className="ai-images-theme-group-field">
                <span>目标群</span>
                <select
                  value={defaultGroupId ?? ""}
                  disabled={defaultSaving}
                  onChange={(event) => setDefaultGroupId(event.target.value ? Number(event.target.value) : null)}
                >
                  <option value="">请选择要保存到的群聊</option>
                  {groups.map((group) => <option key={group.id} value={group.id}>{group.display_name || group.wechat_group_name || `群 ${group.id}`} · ID {group.id}</option>)}
                </select>
              </label>
              <Button tone="primary" onClick={saveDefaultStyle} busy={defaultSaving} disabled={defaultGroupId === null || defaultLoading || !defaultConfig || !defaultDirty || Boolean(defaultThemeError)}><FloppyDisk size={16} />保存到目标群</Button>
            </div>
            {defaultStyleTouched && defaultGroupId !== null && defaultDirty && <div className="ai-images-style-draft-note">已保留你刚输入的风格，保存后才会应用到当前目标群。</div>}
            {groupsError && <div className="ai-images-run-error">{groupsError}</div>}
            {themesError && <div className="ai-images-run-error">{themesError} <Button tone="ghost" className="ui-button-compact" onClick={loadCatalogs}>重新加载</Button></div>}
            {defaultTemplateError && <div className="ai-images-run-error">{defaultTemplateError}</div>}
            {defaultThemeError && <div className="ai-images-run-error">{defaultThemeError}</div>}
            {defaultLoading && <div className="ai-images-style-status">正在读取所选群聊的 Prompt…</div>}
            {defaultError && <div className="ai-images-run-error">{defaultError} <Button tone="ghost" className="ui-button-compact" onClick={() => setDefaultReloadVersion((current) => current + 1)}>重试</Button></div>}
          </>
        )}
      </section>

      <section className="ai-images-filter-bar" aria-label="运行记录筛选">
        <label><span>运行日期</span><input type="date" value={dateFilter} onChange={(event) => setDateFilter(event.target.value)} /></label>
        <label><span>群名</span><input type="search" value={groupFilter} placeholder="搜索群名" onChange={(event) => setGroupFilter(event.target.value)} /></label>
        <label><span>状态</span><select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}><option value="all">全部状态</option>{Object.entries(STATUS_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <span className="ai-images-filter-count">显示 {filteredRuns.length} / {runs.length} 条</span>
      </section>

      <div className="ai-images-workspace">
        <section className="ai-images-run-list">
          <div className="ai-images-section-head"><div><h2>运行记录</h2><p>选择群和日期编辑当次内容。</p></div><ImageSquare size={22} /></div>
          {!filteredRuns.length ? <EmptyState title="没有匹配记录" description="请调整筛选条件，或先运行一次日报。" /> : <div className="ai-images-run-items">{filteredRuns.map((run) => <button type="button" key={runKey(run)} className={`ai-images-run-item ${selectedKey === runKey(run) ? "is-active" : ""}`} onClick={() => setSelectedKey(runKey(run))}><div><strong>{run.group_name}</strong><span>{run.run_date} · {formatDateTime(run.updated_at)}</span></div><StatusPill status={run.status} /></button>)}</div>}
        </section>

        <section className="ai-images-detail-panel">
          {detailLoading ? <LoadingState label="正在读取真实 Prompt 与图片…" /> : detailError && !detail ? <EmptyState title="运行详情加载失败" description={detailError} action={<Button tone="secondary" onClick={() => setDetailReloadVersion((current) => current + 1)}>重试</Button>} /> : !detail ? <EmptyState title="请选择运行记录" description="从左侧选择一条记录。" /> : (
            <>
              <div className="ai-images-detail-head"><div><span className="ai-images-eyebrow">当天真实运行</span><h2>{detail.run.group_name} · {detail.run.run_date}</h2><p><StatusPill status={detail.run.status} /> · 更新 {formatDateTime(detail.run.updated_at)}</p></div></div>
              <div className={`ai-images-regen-state ${regenStatus}`}><strong>{REGEN_LABELS[regenStatus] || regenStatus}</strong><span>{String(detail.run.image_regen_error || detail.run.image_regen_detail || "messages.json 已按运行日期保存；重建 Prompt 和重新生图都不会再次读取微信，也不会自动发送。")}</span></div>
              {runPrompt?.topic_selection && <section className="ai-images-topic-score-card" aria-label="选题评分">
                <div className="ai-images-content-heading"><div><h3>选题评分</h3><span>候选 {runPrompt.topic_selection.candidate_count} · 入选 {runPrompt.topic_selection.selected_count}</span></div><span>v{runPrompt.topic_selection.topic_selection_version}</span></div>
                <div className="ai-images-topic-score-list">{runPrompt.topic_selection.candidates.map((topic) => <article className={`ai-images-topic-score-item ${topic.selected ? "is-selected" : ""}`} key={topic.topic_id}>
                  <div className="ai-images-topic-score-title"><span>#{topic.rank}</span><strong>{topic.title}</strong><StatusBadge tone={topic.selected ? "success" : "neutral"}>{topic.selected ? "已入选" : "候选"}</StatusBadge><b>{topic.scores.total.toFixed(1)}</b></div>
                  <p>{topic.summary}</p>
                  <div className="ai-images-topic-score-grid"><span>讨论 {topic.scores.discussion}</span><span>参与 {topic.scores.participation}</span><span>有趣 {topic.scores.interestingness}</span><span>画面 {topic.scores.visual}</span><span>持续 {topic.scores.continuity}</span></div>
                  <small>{topic.evidence_message_count} 条证据 · {topic.participant_count} 人 · {topic.duration_minutes} 分钟{topic.score_reason ? ` · ${topic.score_reason}` : ""}</small>
                </article>)}</div>
              </section>}
              {runPrompt && <div className="ai-images-run-theme-row">
                <label><span>替换当天大主题</span><select value={runTheme} onChange={(event) => applyRunTheme(event.target.value)}>{themes.map((theme) => <option key={theme.key} value={theme.key}>{theme.label}</option>)}</select></label>
                {runTheme === "custom" && <label><span>自定义主题</span><input maxLength={80} value={runCustom} onChange={(event) => { const value = event.target.value; setRunCustom(value); if (value.trim()) applyRunTheme("custom", value); }} /></label>}
              </div>}
              <div className="ai-images-asset-grid">
                <div className="ai-images-preview-card"><div className="ai-images-content-heading"><h3>日报图片</h3><span>daily_image.png</span></div>{detail.files.includes("daily_image.png") && !imageLoadError ? <ImagePreviewTrigger src={currentImageSrc} alt="真实日报图片" imageClassName="ai-images-real-image" className="ai-images-real-image-trigger" onError={() => { setImageLoadError(true); setImageViewerOpen(false); }} onOpen={() => setImageViewerOpen(true)} /> : <EmptyState title="尚无可读图片" description="重新生图失败时会保留旧图；没有旧图时这里保持为空。" />}</div>
                {runPrompt ? <div className="ai-images-prompt-card ai-images-run-editor"><div className="ai-images-content-heading"><h3>当天生图 Prompt</h3><Button tone="ghost" className="ui-button-compact" onClick={() => copyText(runDraft, toast)} disabled={!runDraft}><Copy size={16} />复制</Button></div><textarea value={runDraft} onChange={(event) => setRunDraft(event.target.value)} /><div className="ai-images-run-actions"><Button tone="ghost" onClick={restoreCurrentPrompt} busy={restoring} disabled={!runPrompt.has_original}><ArrowCounterClockwise size={16} />恢复最初版本</Button><Button tone="secondary" onClick={saveCurrentPrompt} busy={runSaving} disabled={!runDirty}><FloppyDisk size={16} />保存 Prompt</Button><Button tone="secondary" onClick={rebuildCurrentPrompt} busy={rebuildingPrompt} disabled={runDirty || ["queued", "running"].includes(regenStatus)}><Sparkle size={16} />从当天消息重建 Prompt</Button><Button tone="primary" onClick={regenerate} busy={regenerating} disabled={runDirty || rebuildingPrompt || ["queued", "running"].includes(regenStatus)}><Play size={16} />按现有 Prompt 重画</Button></div></div> : <div className="ai-images-prompt-card"><EmptyState title="当天 Prompt 加载失败" description={runPromptError || "当天 Prompt 暂不可用；日报图片和运行状态仍可查看。"} action={<Button tone="secondary" onClick={() => setDetailReloadVersion((current) => current + 1)}>重新读取 Prompt</Button>} /></div>}
              </div>
              {regenStatus === "ready_for_review" && <div className="ai-images-review-actions"><WarningCircle size={18} /><span>请先检查新图。只有再次确认后才会发送文字和图片。</span><Button tone="primary" onClick={() => setSendConfirmOpen(true)}><PaperPlaneTilt size={17} />发送 / 重新发送</Button></div>}
              {detail.run.error && <div className="ai-images-run-error">主任务错误：{String(detail.run.error)}</div>}
            </>
          )}
        </section>
      </div>

      <ImageViewer
        open={imageViewerOpen && Boolean(detail)}
        src={currentImageSrc}
        alt="真实日报图片"
        filename="daily_image.png"
        title={detail ? `${detail.run.group_name} · ${detail.run.run_date} · 日报图片` : "日报图片"}
        onClose={() => setImageViewerOpen(false)}
        onDownloadError={toast}
      />

      <ConfirmDialog open={sendConfirmOpen} title="确认发送这张新图？" description="这会立即操作本机微信，向该运行绑定的发送目标粘贴并发送文字与图片。请确认预览、群名和日期都正确。" confirmLabel="确认发送" busy={sending} onCancel={() => setSendConfirmOpen(false)} onConfirm={confirmSend} />
      <Toast message={msg} />
    </div>
  );
}
