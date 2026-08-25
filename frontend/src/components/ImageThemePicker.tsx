import { useEffect, useMemo, useRef, useState } from "react";
import { ImageThemeOption } from "../api";

const THEME_CATEGORIES = [
  "印刷与编辑",
  "绘画与纸本",
  "立体与手作",
  "动漫与数字",
  "科技与结构",
  "传统与复古",
] as const;

interface ImageThemePickerProps {
  themes: ImageThemeOption[];
  value: string;
  onChange: (key: string) => void;
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

export function ImageThemePicker({
  themes,
  value,
  onChange,
  label,
  loading = false,
  error = "",
  disabled = false,
}: ImageThemePickerProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const current = themes.find((theme) => theme.key === value);
  const modes = themes.filter((theme) => theme.kind === "mode");
  const presets = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase();
    return themes.filter((theme) => {
      if (theme.kind !== "preset") return false;
      if (category && theme.category !== category) return false;
      if (!normalized) return true;
      return `${theme.label} ${theme.description} ${theme.category}`.toLocaleLowerCase().includes(normalized);
    });
  }, [category, query, themes]);

  useEffect(() => {
    if (!open) return;
    const handlePointer = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handlePointer);
    window.setTimeout(() => searchRef.current?.focus(), 0);
    return () => document.removeEventListener("mousedown", handlePointer);
  }, [open]);

  const close = () => {
    setOpen(false);
    window.setTimeout(() => triggerRef.current?.focus(), 0);
  };

  const choose = (key: string) => {
    onChange(key);
    close();
  };

  const status = current?.key === "ai_free"
    ? "不注入预设风格"
    : current?.key === "random_preset"
    ? `每日随机 · ${current.variation_count} 种组合`
    : current?.key === "custom"
      ? "自定义描述"
      : current ? `${current.variation_count} 种微变化` : "兼容主题";

  return (
    <div className="image-theme-picker" ref={rootRef} onKeyDown={(event) => {
      if (event.key === "Escape" && open) {
        event.preventDefault();
        close();
      }
    }}>
      <span className="image-theme-picker-label">{label}</span>
      <button
        ref={triggerRef}
        type="button"
        className="image-theme-picker-trigger"
        aria-haspopup="dialog"
        aria-expanded={open}
        disabled={disabled}
        onClick={() => setOpen((currentOpen) => !currentOpen)}
      >
        <ThemeSwatches colors={current?.swatches || []} />
        <span><b>{current?.label || (loading ? "正在加载风格…" : value || "请选择风格")}</b><small>{status}</small></span>
        <i className="image-theme-picker-caret" aria-hidden="true">⌄</i>
      </button>
      {open && (
        <div className="image-theme-picker-popover" role="dialog" aria-label={`${label}选择器`}>
          <div className="image-theme-picker-popover-head">
            <div><strong>选择生图风格</strong><span>预设家族每天产生可复现的细微变化</span></div>
            <button type="button" aria-label="关闭风格选择器" onClick={close}>×</button>
          </div>
          {loading ? <div className="image-theme-picker-state">正在读取风格目录…</div> : error ? <div className="image-theme-picker-state is-error">{error}</div> : (
            <>
              <div className="image-theme-mode-list" aria-label="风格模式">
                {modes.map((theme) => (
                  <button
                    type="button"
                    key={theme.key}
                    className={value === theme.key ? "is-active" : ""}
                    onClick={() => choose(theme.key)}
                  >
                    <span><b>{theme.label}</b><small>{theme.description}</small></span>
                    <em>{theme.key === "ai_free" ? "默认" : theme.key === "random_preset" ? `${theme.variation_count} 种` : "80 字内"}</em>
                  </button>
                ))}
              </div>
              <label className="image-theme-search">
                <span>搜索预设</span>
                <input ref={searchRef} type="search" value={query} placeholder="例如：水墨、像素、纸艺" onChange={(event) => setQuery(event.target.value)} />
              </label>
              <div className="image-theme-category-list" aria-label="风格分类">
                <button type="button" className={!category ? "is-active" : ""} onClick={() => setCategory("")}>全部</button>
                {THEME_CATEGORIES.map((item) => <button type="button" key={item} className={category === item ? "is-active" : ""} onClick={() => setCategory(item)}>{item}</button>)}
              </div>
              {presets.length ? <div className="image-theme-preset-grid">
                {presets.map((theme) => (
                  <button
                    type="button"
                    key={theme.key}
                    className={value === theme.key ? "is-active" : ""}
                    onClick={() => choose(theme.key)}
                  >
                    <ThemeSwatches colors={theme.swatches} />
                    <span><b>{theme.label}</b><small>{theme.description}</small></span>
                    <em>{theme.variation_count} 种微变化</em>
                  </button>
                ))}
              </div> : <div className="image-theme-picker-state">没有匹配的风格，换个关键词或分类试试。</div>}
            </>
          )}
        </div>
      )}
    </div>
  );
}
