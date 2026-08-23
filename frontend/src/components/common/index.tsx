import { CircleNotch, Info, X } from "@phosphor-icons/react";
import type { ButtonHTMLAttributes, ReactNode } from "react";

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

export function Button({ tone = "primary", busy = false, children, disabled, className = "", ...props }: ButtonProps) {
  return (
    <button className={`ui-button ${tone} ${className}`} type="button" disabled={disabled || busy} {...props}>
      {busy && <CircleNotch className="spin" size={17} aria-hidden="true" />}
      {children}
    </button>
  );
}

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
  if (!message) return null;
  return (
    <div className="ui-toast" role="status">
      <span>{message}</span>
      {onClose && <button type="button" aria-label="关闭提示" onClick={onClose}><X size={16} /></button>}
    </div>
  );
}

export function ConfirmDialog({ open, title, description, confirmLabel = "确认", busy = false, onConfirm, onCancel }: { open: boolean; title: string; description: string; confirmLabel?: string; busy?: boolean; onConfirm: () => void; onCancel: () => void }) {
  if (!open) return null;
  return (
    <div className="ui-dialog-backdrop" role="presentation" onMouseDown={onCancel}>
      <section className="ui-dialog" role="dialog" aria-modal="true" aria-labelledby="confirm-dialog-title" onMouseDown={(event) => event.stopPropagation()}>
        <h2 id="confirm-dialog-title">{title}</h2>
        <p>{description}</p>
        <div className="ui-dialog-actions">
          <Button tone="secondary" onClick={onCancel} disabled={busy}>取消</Button>
          <Button tone="danger" onClick={onConfirm} busy={busy}>{busy ? "处理中…" : confirmLabel}</Button>
        </div>
      </section>
    </div>
  );
}
