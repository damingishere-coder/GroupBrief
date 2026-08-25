import { useEffect, useMemo, useRef, useState } from "react";
import { FloppyDisk, Sparkle } from "@phosphor-icons/react";

import {
  getGroupImagePrompt,
  GroupImagePromptConfig,
  GroupV2,
  ImageThemeOption,
  resolveImageTheme,
  updateGroup,
} from "../../../api";
import { Button, EmptyState, LoadingState } from "../../../components/common";
import { ImageThemePicker } from "../../../components/ImageThemePicker";
import { describeLoadError, renderGroupPreview } from "./model";
import type { ToastFn } from "./useAIImageCatalogs";

interface ImageStylePanelProps {
  groups: GroupV2[];
  themes: ImageThemeOption[];
  catalogLoading: boolean;
  groupsError: string;
  themesError: string;
  globalDefaultPrompt: string;
  defaultTemplateError: string;
  loadCatalogs: () => Promise<void>;
  toast: ToastFn;
}

export function ImageStylePanel({
  groups,
  themes,
  catalogLoading,
  groupsError,
  themesError,
  globalDefaultPrompt,
  defaultTemplateError,
  loadCatalogs,
  toast,
}: ImageStylePanelProps) {
  const [defaultGroupId, setDefaultGroupId] = useState<number | null>(null);
  const [defaultConfig, setDefaultConfig] = useState<GroupImagePromptConfig | null>(null);
  const [defaultTheme, setDefaultTheme] = useState("ai_free");
  const [defaultCustom, setDefaultCustom] = useState("");
  const [defaultThemeText, setDefaultThemeText] = useState("");
  const [defaultThemeError, setDefaultThemeError] = useState("");
  const [defaultStyleTouched, setDefaultStyleTouched] = useState(false);
  const defaultStyleTouchedRef = useRef(false);
  const [defaultLoading, setDefaultLoading] = useState(false);
  const [defaultSaving, setDefaultSaving] = useState(false);
  const [defaultError, setDefaultError] = useState("");
  const [defaultReloadVersion, setDefaultReloadVersion] = useState(0);

  useEffect(() => {
    setDefaultGroupId((current) => groups.some((group) => group.id === current)
      ? current
      : null);
  }, [groups]);

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
          setDefaultTheme(config.image_theme || "ai_free");
          const savedCustom = config.image_theme === "custom" ? config.image_theme_custom || "" : "";
          setDefaultCustom(savedCustom);
          setDefaultThemeText(config.resolved_theme?.theme_text || "");
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
    if (defaultTheme !== "custom") return;
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
  }, [defaultCustom, defaultGroupId, defaultTheme]);

  const selectedDefaultGroup = groups.find((group) => group.id === defaultGroupId);
  const savedDefaultTheme = defaultConfig?.image_theme || "ai_free";
  const savedDefaultCustom = defaultConfig?.image_theme === "custom"
    ? defaultConfig.image_theme_custom.trim()
    : "";
  const currentDefaultCustom = defaultTheme === "custom" ? defaultCustom.trim() : "";
  const defaultDirty = Boolean(defaultConfig)
    && (defaultTheme !== savedDefaultTheme || currentDefaultCustom !== savedDefaultCustom);
  const defaultPreviewTheme = defaultTheme === "custom" && currentDefaultCustom
    ? defaultThemeText || `指定风格「${currentDefaultCustom}」（正在生成完整约束）`
    : defaultThemeText;
  const defaultPreview = useMemo(
    () => renderGroupPreview(defaultConfig?.content || globalDefaultPrompt, selectedDefaultGroup, defaultPreviewTheme),
    [defaultConfig?.content, defaultPreviewTheme, globalDefaultPrompt, selectedDefaultGroup],
  );

  const applyDefaultTheme = async (key: string) => {
    defaultStyleTouchedRef.current = true;
    setDefaultStyleTouched(true);
    setDefaultTheme(key);
    setDefaultThemeError("");
    if (key === "custom") {
      setDefaultThemeText("");
      return;
    }
    setDefaultCustom("");
    try {
      const resolved = await resolveImageTheme({
        image_theme: key,
        group_id: defaultGroupId ?? undefined,
      });
      setDefaultThemeText(resolved.theme_text);
    } catch (reason) {
      setDefaultThemeText("");
      setDefaultThemeError(`风格预览失败：${String(reason)}`);
    }
  };

  const saveDefaultStyle = async () => {
    if (!defaultConfig || defaultGroupId === null) return;
    setDefaultSaving(true);
    try {
      const custom = defaultTheme === "custom" ? defaultCustom.trim() : "";
      await updateGroup(defaultGroupId, {
        image_theme: defaultTheme,
        image_theme_custom: custom,
      });
      const refreshed = await getGroupImagePrompt(defaultGroupId);
      setDefaultConfig(refreshed);
      setDefaultTheme(refreshed.image_theme || "ai_free");
      setDefaultCustom(refreshed.image_theme === "custom" ? refreshed.image_theme_custom || "" : "");
      setDefaultThemeText(refreshed.resolved_theme?.theme_text || "");
      defaultStyleTouchedRef.current = false;
      setDefaultStyleTouched(false);
      const selectedThemeLabel = themes.find((theme) => theme.key === defaultTheme)?.label || "生图风格";
      toast(`已把「${selectedThemeLabel}」保存到「${selectedDefaultGroup?.display_name || selectedDefaultGroup?.wechat_group_name || `群 ${defaultGroupId}`}」`);
    } catch (reason) {
      toast(`生图风格保存失败：${String(reason)}`);
    } finally {
      setDefaultSaving(false);
    }
  };

  return (
    <section className="ai-images-theme-card" aria-label="群聊指定风格">
      <div className="ai-images-theme-heading">
        <div><h2>设置群聊生图风格</h2><p>默认由 AI 按聊天内容自由发挥；手动选择后才注入预设或自定义风格。</p></div>
        <Sparkle size={22} />
      </div>
      {catalogLoading && !groups.length ? <LoadingState label="正在读取群配置与主题目录…" /> : groupsError && !groups.length ? <EmptyState title="群配置加载失败" description={groupsError} action={<Button tone="secondary" onClick={loadCatalogs}>重新加载</Button>} /> : !groups.length ? <EmptyState title="暂无群配置" description="当前数据库中确实没有群，请先在群聊配置中创建群。" /> : (
        <>
          <ImageThemePicker
            themes={themes}
            value={defaultTheme}
            onChange={(key) => { void applyDefaultTheme(key); }}
            label="风格模式"
            loading={catalogLoading}
            error={themesError}
            disabled={defaultSaving}
          />
          {defaultTheme === "custom" && <label className="ai-images-theme-custom-field">
            <span><b>指定风格描述</b><small>{defaultCustom.length} / 80</small></span>
            <input maxLength={80} value={defaultCustom} placeholder="例如：低饱和黏土摄影、宋代工笔设色" onChange={(event) => {
              defaultStyleTouchedRef.current = true;
              setDefaultStyleTouched(true);
              setDefaultThemeText("");
              setDefaultCustom(event.target.value);
            }} />
          </label>}
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
            <Button tone="primary" onClick={saveDefaultStyle} busy={defaultSaving} disabled={defaultGroupId === null || defaultLoading || !defaultConfig || !defaultDirty || Boolean(defaultThemeError) || (defaultTheme === "custom" && !defaultCustom.trim())}><FloppyDisk size={16} />保存到目标群</Button>
          </div>
          {defaultStyleTouched && defaultGroupId !== null && defaultDirty && <div className="ai-images-style-draft-note">风格草稿已保留，切换目标群不会覆盖；明确保存后才会应用。</div>}
          {groupsError && <div className="ai-images-run-error">{groupsError}</div>}
          {themesError && <div className="ai-images-run-error">{themesError} <Button tone="ghost" className="ui-button-compact" onClick={loadCatalogs}>重新加载</Button></div>}
          {defaultTemplateError && <div className="ai-images-run-error">{defaultTemplateError}</div>}
          {defaultThemeError && <div className="ai-images-run-error">{defaultThemeError}</div>}
          {defaultLoading && <div className="ai-images-style-status">正在读取所选群聊的 Prompt…</div>}
          {defaultError && <div className="ai-images-run-error">{defaultError} <Button tone="ghost" className="ui-button-compact" onClick={() => setDefaultReloadVersion((current) => current + 1)}>重试</Button></div>}
        </>
      )}
    </section>
  );
}
