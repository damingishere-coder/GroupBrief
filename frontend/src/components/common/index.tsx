import { CircleNotch, Info, X } from "@phosphor-icons/react";
import { forwardRef, useEffect, useRef, type ButtonHTMLAttributes, type ReactNode } from "react";
import { AnimatePresence, m, MOTION_EASE } from "../motion";

export { ImagePreviewTrigger, ImageViewer } from "./ImageViewer";
export type { ImagePreviewTriggerProps, ImageViewerProps } from "./ImageViewer";

export function PageHeader({ title, description, actions }: { title: string; description?: string; actions?: ReactNode }) {
  return (
    <header className="ui-page-header">
      <div>
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      {actions && <div className="ui-page-actions">{actions}</div>}
    </header>
  );
}

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  tone?: "primary" | "secondary" | "danger" | "ghost";
  busy?: boolean;
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { tone = "primary", busy = false, children, disabled, className = "", ...props },
  ref,
) {
  return (
    <button ref={ref} className={`ui-button ${tone} ${className}`} type="button" disabled={disabled || busy} {...props}>
      {busy && <CircleNotch className="spin" size={17} aria-hidden="true" />}
      {children}
    </button>
  );
});

export function StatusBadge({ tone = "neutral", children }: { tone?: "success" | "warning" | "danger" | "info" | "neutral"; children: ReactNode }) {
  return <span className={`ui-status ${tone}`}>{children}</span>;
}

export function EmptyState({ title, description, action }: { title: string; description?: string; action?: ReactNode }) {
  return (
    <div className="ui-empty">
      <Info size={25} />
      <strong>{title}</strong>
      {description && <p>{description}</p>}
      {action}
    </div>
  );
}

export function LoadingState({ label = "正在加载…" }: { label?: string }) {
  return <div className="ui-loading"><CircleNotch className="spin" size={22} /><span>{label}</span></div>;
}

export function Toast({ message, onClose }: { message: string; onClose?: () => void }) {
  return (
    <AnimatePresence>
      {message && (
        <m.div className="ui-toast" role="status" aria-live="polite" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 8 }} transition={{ duration: 0.18, ease: MOTION_EASE }}>
          <span>{message}</span>
          {onClose && <button type="button" aria-label="关闭提示" onClick={onClose}><X size={16} /></button>}
        </m.div>
      )}
    </AnimatePresence>
  );
}

export function ConfirmDialog({ open, title, description, confirmLabel = "确认", busy = false, onConfirm, onCancel }: { open: boolean; title: string; description: string; confirmLabel?: string; busy?: boolean; onConfirm: () => void; onCancel: () => void }) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);
  const busyRef = useRef(busy);
  const onCancelRef = useRef(onCancel);
  busyRef.current = busy;
  onCancelRef.current = onCancel;
  useEffect(() => {
    if (!open) return;
    returnFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const frame = window.requestAnimationFrame(() => cancelRef.current?.focus());
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busyRef.current) {
        event.preventDefault();
        onCancelRef.current();
        return;
      }
      if (event.key !== "Tab") return;
      const dialog = cancelRef.current?.closest<HTMLElement>("[role='dialog']");
      if (!dialog) return;
      const focusable = Array.from(
        dialog.querySelectorAll<HTMLElement>(
          "button:not(:disabled), input:not(:disabled), textarea:not(:disabled), select:not(:disabled), [tabindex]:not([tabindex='-1'])",
        ),
      );
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
      window.requestAnimationFrame(() => returnFocusRef.current?.focus());
    };
  }, [open]);
  return (
    <AnimatePresence>
      {open && (
        <m.div className="ui-dialog-backdrop" role="presentation" onMouseDown={() => !busy && onCancel()} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.18 }}>
          <m.section className="ui-dialog" role="dialog" aria-modal="true" aria-labelledby="confirm-dialog-title" onMouseDown={(event) => event.stopPropagation()} initial={{ opacity: 0, scale: 0.985, y: 5 }} animate={{ opacity: 1, scale: 1, y: 0 }} exit={{ opacity: 0, scale: 0.985, y: 4 }} transition={{ duration: 0.2, ease: MOTION_EASE }}>
            <h2 id="confirm-dialog-title">{title}</h2>
            <p>{description}</p>
            <div className="ui-dialog-actions">
              <Button ref={cancelRef} tone="secondary" onClick={onCancel} disabled={busy}>取消</Button>
              <Button tone="danger" onClick={onConfirm} busy={busy}>{busy ? "处理中…" : confirmLabel}</Button>
            </div>
          </m.section>
        </m.div>
      )}
    </AnimatePresence>
  );
}
