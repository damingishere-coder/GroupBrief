import { useEffect, useMemo, useRef, useState } from "react";
import { Check, ImageSquare, MagnifyingGlass, X } from "@phosphor-icons/react";

import type { ImageThemeOption } from "../api";
import { Button } from "./common";
import { AnimatePresence, m, MOTION_EASE } from "./motion";

const THEME_CATEGORIES = [
  "印刷与编辑", "绘画与纸本", "立体与手作", "动漫与数字", "科技与结构", "传统与复古",
] as const;

type ThemeTab = "ai_free" | "random_preset" | "preset" | "custom";

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
  return <span className="image-theme-swatches" aria-hidden="true">{colors.map((color) => <i key={color} style={{ backgroundColor: color }} />)}</span>;
}

function tabForKey(key: string): ThemeTab {
  if (key === "ai_free" || key === "random_preset" || key === "custom") return key;
  return "preset";
}

export function ImageThemePicker({ themes, value, customValue = "", onConfirm, label, loading = false, error = "", disabled = false }: ImageThemePickerProps) {
  const [open, setOpen] = useState(false);
  const [activeTab, setActiveTab] = useState<ThemeTab>(() => tabForKey(value));
  const [draftKey, setDraftKey] = useState(value);
  const [draftCustom, setDraftCustom] = useState(customValue);
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [previewBroken, setPreviewBroken] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const modalRef = useRef<HTMLElement>(null);
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
    if (!open) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : triggerRef.current;
    const frame = window.requestAnimationFrame(() => modalRef.current?.focus());
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        setOpen(false);
        return;
      }
      if (event.key !== "Tab" || !modalRef.current) return;
      const focusable = Array.from(modalRef.current.querySelectorAll<HTMLElement>("button:not(:disabled), input:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex='-1'])"));
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
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
    if (tab !== "preset") setDraftKey(tab);
    else if (!themes.some((theme) => theme.kind === "preset" && theme.key === draftKey)) setDraftKey(themes.find((theme) => theme.kind === "preset")?.key || "");
  };

  const status = current?.key === "ai_free" ? "不注入预设风格"
    : current?.key === "random_preset" ? `每日随机 · ${current.variation_count} 种组合`
      : current?.key === "custom" ? customValue.trim() || "自定义描述"
        : current ? `${current.variation_count} 种微变化` : "兼容主题";

  return <div className="image-theme-picker">
    <span className="image-theme-picker-label">{label}</span>
    <button ref={triggerRef} type="button" className="image-theme-picker-trigger" aria-haspopup="dialog" aria-expanded={open} disabled={disabled} onClick={openCenter}>
      <ThemeSwatches colors={current?.swatches || []} />
      <span><b>{current?.label || (loading ? "正在加载风格…" : value || "请选择风格")}</b><small>{status}</small></span>
      <i className="image-theme-picker-caret" aria-hidden="true">⌄</i>
    </button>

    <AnimatePresence>
      {open && <m.div className="image-theme-center-backdrop" role="presentation" onMouseDown={() => setOpen(false)} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.18 }}>
        <m.section ref={modalRef} tabIndex={-1} className="image-theme-center" role="dialog" aria-modal="true" aria-labelledby="image-theme-center-title" onMouseDown={(event) => event.stopPropagation()} initial={{ opacity: 0, scale: 0.985, y: 6 }} animate={{ opacity: 1, scale: 1, y: 0 }} exit={{ opacity: 0, scale: 0.985, y: 5 }} transition={{ duration: 0.22, ease: MOTION_EASE }}>
          <header className="image-theme-center-head">
            <div><h2 id="image-theme-center-title">风格中心</h2><p>先在窗口中预览和调整，确认后才应用到当前编辑内容。</p></div>
            <button type="button" aria-label="关闭风格中心" onClick={() => setOpen(false)}><X size={20} /></button>
          </header>

          <div className="image-theme-center-tabs" role="tablist" aria-label="风格模式">
            {([["ai_free", "AI 自由发挥"], ["random_preset", "每日随机"], ["preset", "预设风格"], ["custom", "自定义描述"]] as const).map(([tab, tabLabel]) => <button key={tab} type="button" role="tab" aria-selected={activeTab === tab} className={activeTab === tab ? "is-active" : ""} onClick={() => selectTab(tab)}>{tabLabel}</button>)}
          </div>

          <div className="image-theme-center-body">
            {loading ? <div className="image-theme-picker-state">正在读取风格目录…</div> : error ? <div className="image-theme-picker-state is-error">{error}</div> : activeTab === "preset" ? <div className="image-theme-preset-layout">
              <aside className="image-theme-preset-browser">
                <label className="image-theme-search"><MagnifyingGlass size={16} /><input type="search" value={query} placeholder="搜索水墨、像素、纸艺…" onChange={(event) => setQuery(event.target.value)} /></label>
                <div className="image-theme-category-list" aria-label="风格分类">
                  <button type="button" className={!category ? "is-active" : ""} onClick={() => setCategory("")}>全部</button>
                  {THEME_CATEGORIES.map((item) => <button type="button" key={item} className={category === item ? "is-active" : ""} onClick={() => setCategory(item)}>{item}</button>)}
                </div>
                <div className="image-theme-preset-list">
                  {presets.map((theme) => <button type="button" key={theme.key} className={draftKey === theme.key ? "is-active" : ""} onClick={() => setDraftKey(theme.key)}><ThemeSwatches colors={theme.swatches} /><span><b>{theme.label}</b><small>{theme.category}</small></span>{draftKey === theme.key && <Check size={17} aria-hidden="true" />}</button>)}
                  {!presets.length && <div className="image-theme-picker-state">没有匹配的预设风格。</div>}
                </div>
              </aside>
              <section className="image-theme-preview-panel" aria-live="polite">
                <div className="image-theme-preview-frame" style={{ background: `linear-gradient(145deg, ${(selectedDraft?.swatches || ["#eef2f7"])[0]}, ${(selectedDraft?.swatches || ["#ffffff", "#ffffff"])[1]})` }}>
                  <AnimatePresence mode="wait" initial={false}>
                    {selectedDraft?.preview_url && !previewBroken ? <m.img key={selectedDraft.key} src={selectedDraft.preview_url} alt={`${selectedDraft.label}统一虚构场景示例`} onError={() => setPreviewBroken(true)} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.18 }} /> : <m.div key={`placeholder-${selectedDraft?.key || "none"}`} className="image-theme-preview-placeholder" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}><ImageSquare size={36} /><span>示例图暂不可用，仍可根据色板和说明选择</span></m.div>}
                  </AnimatePresence>
                </div>
                <div className="image-theme-preview-copy"><span>{selectedDraft?.category || "预设风格"}</span><h3>{selectedDraft?.label || "请选择一种风格"}</h3><p>{selectedDraft?.description || "从左侧 22 个风格家族中选择后查看说明和示例。"}</p><ThemeSwatches colors={selectedDraft?.swatches || []} />{selectedDraft && <small>每天保留 {selectedDraft.variation_count} 种可复现微变化；示例只展示画材、配色、纹理和光影。</small>}</div>
              </section>
            </div> : activeTab === "custom" ? <section className="image-theme-mode-panel image-theme-custom-panel">
              <span>自定义描述</span><h3>用一句话描述你想要的视觉语言</h3><p>内容只会在点击“使用这个风格”时解析和提交，输入过程中不会触发接口更新。</p>
              <label><span><b>风格描述</b><small>{draftCustom.length} / 80</small></span><textarea autoFocus maxLength={80} value={draftCustom} placeholder="例如：低饱和黏土摄影、宋代工笔设色" onChange={(event) => setDraftCustom(event.target.value)} /></label>
            </section> : <section className="image-theme-mode-panel">
              <span>{activeTab === "ai_free" ? "默认模式" : "可复现随机模式"}</span><h3>{themes.find((theme) => theme.key === activeTab)?.label || "风格模式"}</h3><p>{themes.find((theme) => theme.key === activeTab)?.description}</p>
              {activeTab === "ai_free" ? <small>不注入画材、配色、纹理或光影，让模型根据当天真实聊天内容统一决定视觉风格。</small> : <small>按群聊和运行日期稳定选择风格；同一群同一天重复预览时结果保持一致。</small>}
            </section>}
          </div>

          <footer className="image-theme-center-actions">
            <span>{draftKey === value && (draftKey !== "custom" || draftCustom.trim() === customValue.trim()) ? "当前没有未确认修改" : "修改仍在窗口草稿中"}</span>
            <div><Button tone="secondary" onClick={() => setOpen(false)}>取消</Button><Button tone="primary" disabled={!draftKey || (draftKey === "custom" && !draftCustom.trim())} onClick={() => { void onConfirm(draftKey, draftKey === "custom" ? draftCustom.trim() : ""); setOpen(false); }}>使用这个风格</Button></div>
          </footer>
        </m.section>
      </m.div>}
    </AnimatePresence>
  </div>;
}
