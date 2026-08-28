import type { RuntimeNodeStatus, RuntimeOverallStatus } from "../../api";

export const RUNTIME_NODE_META: Record<RuntimeNodeStatus, { label: string; tone: "success" | "warning" | "danger" | "info" | "neutral" }> = {
  pending: { label: "等待", tone: "neutral" },
  running: { label: "运行中", tone: "info" },
  success: { label: "完成", tone: "success" },
  retry_pending: { label: "等待重试", tone: "warning" },
  held: { label: "需要处理", tone: "danger" },
  failed: { label: "失败", tone: "danger" },
};

export function runtimeRefreshDelay(
  status: RuntimeOverallStatus,
  options: {
    isToday: boolean;
    visible: boolean;
    scheduledAt?: string;
    now?: number;
  },
): number | null {
  if (!options.isToday || !options.visible) return null;
  if (status === "running" || status === "retry_pending") return 3_000;
  if (status !== "not_started") return null;

  const scheduled = options.scheduledAt ? Date.parse(options.scheduledAt) : Number.NaN;
  const now = options.now ?? Date.now();
  if (Number.isFinite(scheduled) && scheduled > now) {
    return Math.max(250, Math.min(30_000, scheduled - now));
  }
  return 30_000;
}

export function formatRuntimeTime(value?: string): string {
  if (!value) return "—";
  return value.replace("T", " ").slice(0, 19);
}
