import { useEffect, useMemo, useRef, useState } from "react";
import {
  CaretRight,
  Check,
  ImageSquare,
  MagnifyingGlass,
  Palette,
  PencilSimple,
  Shuffle,
  Sparkle,
  X,
} from "@phosphor-icons/react";

import type { ImageThemeOption } from "../api";
import { Button } from "./common";
import { AnimatePresence, m, MOTION_EASE } from "./motion";

const THEME_CATEGORIES = [
  "印刷与编辑", "绘画与纸本", "立体与手作", "动漫与数字", "科技与结构", "传统与复古",
] as const;

type ThemeTab = "ai_free" | "random_preset" | "preset" | "custom";

const MODE_ITEMS: Array<{
  key: ThemeTab;
  label: string;
  short: string;
  icon: typeof Sparkle;
}> = [
  { key: "ai_free", label: "AI 自由发挥", short: "按当天内容决定画面", icon: Sparkle },
  { key: "random_preset", label: "每日随机", short: "同群同日稳定复现", icon: Shuffle },
  { key: "preset", label: "预设风格", short: "从风格目录中选择", icon: Palette },
  { key: "custom", label: "自定义描述", short: "用一句话定义视觉语言", icon: PencilSimple },
];

interface ImageThemePickerProps {
  themes: ImageThemeOption[];
  value: string;
  customValue?: string;
  onConfirm: (key: string, custom: string) => void | Promise<void>;
  label: string;
  loading?: boolean;
  error?: string;
  disabled?: boolean;
}

function ThemeSwatches({ colors }: { colors: string[] }) {
  if (!colors.length) return null;
  return (
    <span className="image-theme-swatches" aria-hidden="true">
      {colors.map((color) => <i key={color} style={{ backgroundColor: color }} />)}
    </span>
  );
}

function tabForKey(key: string): ThemeTab {
  if (key === "ai_free" || key === "random_preset" || key === "custom") return key;
  return "preset";
}

function ThemeVisual({ theme, tab }: { theme?: ImageThemeOption; tab: ThemeTab }) {
  if (theme?.swatches.length) {
    return <span className="image-theme-trigger-visual is-swatches"><ThemeSwatches colors={theme.swatches} /></span>;
  }
  const Icon = MODE_ITEMS.find((item) => item.key === tab)?.icon || Palette;
  return <span className="image-theme-trigger-visual" aria-hidden="true"><Icon size={20} weight="duotone" /></span>;
}

export function ImageThemePicker({
  themes,
  value,
  customValue = "",
  onConfirm,
  label,
  loading = false,
  error = "",
  disabled = false,
}: ImageThemePickerProps) {
  const [open, setOpen] = useState(false);
  const [activeTab, setActiveTab] = useState<ThemeTab>(() => tabForKey(value));
  const [draftKey, setDraftKey] = useState(value);
  const [draftCustom, setDraftCustom] = useState(customValue);
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [previewBroken, setPreviewBroken] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const modalRef = useRef<HTMLElement>(null);
  const confirmingRef = useRef(false);
  const current = themes.find((theme) => theme.key === value);
  const selectedDraft = themes.find((theme) => theme.key === draftKey);
  const presets = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase();
    return themes.filter((theme) => theme.kind === "preset"
      && (!category || theme.category === category)
      && (!normalized || `${theme.label} ${theme.description} ${theme.category}`.toLocaleLowerCase().includes(normalized)));
  }, [category, query, themes]);

  useEffect(() => setPreviewBroken(false), [draftKey]);
  useEffect(() => {
    confirmingRef.current = confirming;
  }, [confirming]);

  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : triggerRef.current;
    const frame = window.requestAnimationFrame(() => modalRef.current?.focus());
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !confirmingRef.current) {
        event.preventDefault();
        setOpen(false);
        return;
      }
      if (event.key !== "Tab" || !modalRef.current) return;
      const focusable = Array.from(modalRef.current.querySelectorAll<HTMLElement>(
        "button:not(:disabled), input:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex='-1'])",
      ));
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && (document.activeElement === first || document.activeElement === modalRef.current)) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      window.cancelAnimationFrame(frame);
      document.removeEventListener("keydown", onKeyDown);
      window.requestAnimationFrame(() => previous?.focus());
    };
  }, [open]);

  const openCenter = () => {
    setDraftKey(value);
    setDraftCustom(customValue);
    setActiveTab(tabForKey(value));
    setQuery("");
    setCategory("");
    setOpen(true);
  };

  const selectTab = (tab: ThemeTab) => {
    setActiveTab(tab);
    if (tab !== "preset") {
      setDraftKey(tab);
      return;
    }
    if (!themes.some((theme) => theme.kind === "preset" && theme.key === draftKey)) {
      setDraftKey(themes.find((theme) => theme.kind === "preset")?.key || "");
    }
  };

  const confirmDraft = async () => {
    if (!draftKey || (draftKey === "custom" && !draftCustom.trim())) return;
    setConfirming(true);
    try {
      await onConfirm(draftKey, draftKey === "custom" ? draftCustom.trim() : "");
      setOpen(false);
    } finally {
      setConfirming(false);
    }
  };

  const currentTab = tabForKey(value);
  const status = current?.key === "ai_free" ? "不注入预设风格"
    : current?.key === "random_preset" ? `每日随机 · ${current.variation_count} 种组合`
      : current?.key === "custom" ? customValue.trim() || "自定义描述"
        : current ? `${current.variation_count} 种微变化` : "兼容主题";
  const unchanged = draftKey === value
    && (draftKey !== "custom" || draftCustom.trim() === customValue.trim());

  return (
    <div className="image-theme-picker">
      <span className="image-theme-picker-label">{label}</span>
      <button ref={triggerRef} type="button" className="image-theme-picker-trigger" aria-haspopup="dialog" aria-expanded={open} disabled={disabled} onClick={openCenter}>
        <ThemeVisual theme={current} tab={currentTab} />
        <span className="image-theme-trigger-copy"><b>{current?.label || (loading ? "正在加载风格…" : value || "请选择风格")}</b><small>{status}</small></span>
        <span className="image-theme-trigger-action" aria-hidden="true"><em>更换</em><CaretRight size={16} /></span>
      </button>

      <AnimatePresence>
        {open && (
          <m.div className="image-theme-center-backdrop" role="presentation" onMouseDown={() => !confirming && setOpen(false)} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.18 }}>
            <m.section ref={modalRef} tabIndex={-1} className="image-theme-center" role="dialog" aria-modal="true" aria-labelledby="image-theme-center-title" onMouseDown={(event) => event.stopPropagation()} initial={{ opacity: 0, scale: 0.985, y: 6 }} animate={{ opacity: 1, scale: 1, y: 0 }} exit={{ opacity: 0, scale: 0.985, y: 5 }} transition={{ duration: 0.22, ease: MOTION_EASE }}>
              <header className="image-theme-center-head">
                <div><h2 id="image-theme-center-title">风格中心</h2><p>选择和预览只保存在窗口草稿中，确认后才应用。</p></div>
                <button type="button" aria-label="关闭风格中心" disabled={confirming} onClick={() => setOpen(false)}><X size={20} /></button>
              </header>

              <div className="image-theme-center-workspace">
                <aside className="image-theme-center-nav" role="tablist" aria-label="风格模式">
                  {MODE_ITEMS.map((item) => {
                    const Icon = item.icon;
                    return (
                      <button key={item.key} type="button" role="tab" aria-selected={activeTab === item.key} aria-controls="image-theme-center-panel" className={activeTab === item.key ? "is-active" : ""} onClick={() => selectTab(item.key)}>
                        <Icon size={19} weight="duotone" />
                        <span><b>{item.label}</b><small>{item.short}</small></span>
                        {activeTab === item.key && <Check size={15} aria-hidden="true" />}
                      </button>
                    );
                  })}
                </aside>

                <div id="image-theme-center-panel" className="image-theme-center-body" role="tabpanel">
                  {loading ? <div className="image-theme-picker-state">正在读取风格目录…</div>
                    : error ? <div className="image-theme-picker-state is-error">{error}</div>
                      : activeTab === "preset" ? (
                        <div className="image-theme-preset-layout">
                          <section className="image-theme-preset-browser" aria-label="预设风格目录">
                            <label className="image-theme-search"><MagnifyingGlass size={16} /><input type="search" value={query} placeholder="搜索水墨、像素、纸艺…" onChange={(event) => setQuery(event.target.value)} /></label>
                            <div className="image-theme-category-list" aria-label="风格分类">
                              <button type="button" className={!category ? "is-active" : ""} onClick={() => setCategory("")}>全部</button>
                              {THEME_CATEGORIES.map((item) => <button type="button" key={item} className={category === item ? "is-active" : ""} onClick={() => setCategory(item)}>{item}</button>)}
                            </div>
                            <div className="image-theme-preset-cards">
                              {presets.map((theme) => (
                                <button type="button" key={theme.key} className={draftKey === theme.key ? "is-active" : ""} onClick={() => setDraftKey(theme.key)}>
                                  <span className="image-theme-preset-thumb" style={{ background: `linear-gradient(145deg, ${theme.swatches[0] || "#eaf2ff"}, ${theme.swatches[1] || "#ffffff"})` }}>{theme.preview_url && <img src={theme.preview_url} alt="" loading="lazy" />}</span>
                                  <span><b>{theme.label}</b><small>{theme.category}</small></span>
                                  {draftKey === theme.key && <Check size={16} aria-hidden="true" />}
                                </button>
                              ))}
                              {!presets.length && <div className="image-theme-picker-state">没有匹配的预设风格。</div>}
                            </div>
                          </section>
                          <section className="image-theme-preview-panel" aria-live="polite">
                            <div className="image-theme-preview-frame" style={{ background: `linear-gradient(145deg, ${(selectedDraft?.swatches || ["#eaf2ff"])[0]}, ${(selectedDraft?.swatches || ["#ffffff", "#ffffff"])[1]})` }}>
                              <AnimatePresence mode="wait" initial={false}>
                                {selectedDraft?.preview_url && !previewBroken ? <m.img key={selectedDraft.key} src={selectedDraft.preview_url} alt={`${selectedDraft.label}统一虚构场景示例`} onError={() => setPreviewBroken(true)} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.18 }} />
                                  : <m.div key={`placeholder-${selectedDraft?.key || "none"}`} className="image-theme-preview-placeholder" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}><ImageSquare size={34} /><span>示例图暂不可用，仍可按色板和说明选择</span></m.div>}
                              </AnimatePresence>
                            </div>
                            <div className="image-theme-preview-copy"><span>{selectedDraft?.category || "预设风格"}</span><h3>{selectedDraft?.label || "请选择一种风格"}</h3><p>{selectedDraft?.description || "从风格目录中选择后查看画材、配色、纹理和光影说明。"}</p><ThemeSwatches colors={selectedDraft?.swatches || []} />{selectedDraft && <small>每天保留 {selectedDraft.variation_count} 种可复现微变化；示例不代表真实群聊内容。</small>}</div>
                          </section>
                        </div>
                      ) : activeTab === "custom" ? (
                        <section className="image-theme-mode-panel image-theme-custom-panel">
                          <span>自定义描述</span><h3>用一句话定义视觉语言</h3><p>仅注入共享视觉风格，不会覆盖群原有 Prompt 模板。</p>
                          <label><span><b>风格描述</b><small>{draftCustom.length} / 80</small></span><textarea maxLength={80} value={draftCustom} aria-describedby="image-theme-custom-hint" placeholder="例如：低饱和黏土摄影、宋代工笔设色" onChange={(event) => setDraftCustom(event.target.value.replace(/[\r\n\t]+/g, " "))} /></label>
                          <small id="image-theme-custom-hint">示例：低饱和黏土摄影、宋代工笔设色。最多 80 字，不支持换行。</small>
                        </section>
                      ) : (
                        <section className="image-theme-mode-panel">
                          <span>{activeTab === "ai_free" ? "默认模式" : "可复现随机模式"}</span><h3>{themes.find((theme) => theme.key === activeTab)?.label || "风格模式"}</h3><p>{themes.find((theme) => theme.key === activeTab)?.description}</p>
                          <div className="image-theme-mode-facts">
                            <article><b>效果</b><p>{activeTab === "ai_free" ? "模型按当天真实聊天内容统一决定画材、配色与光影。" : "每天从完整预设库选择一套风格，并保留可复现微变化。"}</p></article>
                            <article><b>适用</b><p>{activeTab === "ai_free" ? "话题变化大、希望画面紧跟内容的群聊。" : "希望群日报每天有变化、同一天重试又保持一致。"}</p></article>
                            <article><b>规则</b><p>{activeTab === "ai_free" ? "不注入预设视觉约束。" : "按群标识和上海日期独立选择；不同群互不共享随机结果。"}</p></article>
                          </div>
                        </section>
                      )}
                </div>
              </div>

              <footer className="image-theme-center-actions">
                <span>{unchanged ? "当前没有未确认修改" : "修改仍在窗口草稿中"}</span>
                <div><Button tone="secondary" disabled={confirming} onClick={() => setOpen(false)}>取消</Button><Button tone="primary" busy={confirming} disabled={!draftKey || (draftKey === "custom" && !draftCustom.trim())} onClick={confirmDraft}>使用这个风格</Button></div>
              </footer>
            </m.section>
          </m.div>
        )}
      </AnimatePresence>
    </div>
  );
}
