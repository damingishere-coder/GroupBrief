import { StatusBadge } from "../../../components/common";
import type { GroupV2, V2Run } from "../../../api";


export const STATUS_LABELS: Record<string, string> = {
  PENDING: "待生成",
  DATA_READY: "数据就绪",
  RANKING_READY: "排行完成",
  PROMPT_READY: "Prompt 完成",
  IMAGE_READY: "图片完成",
  READY_TO_SEND: "待发送",
  SENT: "已发送",
  FAILED: "失败",
};

export const REGEN_LABELS: Record<string, string> = {
  idle: "未重新生图",
  queued: "已排队",
  running: "生成中",
  fallback_queued: "已转入 Codex Desktop 队列",
  ready_for_review: "新图待审核",
  prompt_rebuilt: "Prompt 已重建，等待生图",
  failed: "重新生图失败",
  sent: "新图已发送",
};

export interface ImageDetail {
  run: V2Run;
  files: string[];
}

export function runKey(run: V2Run): string {
  return `${run.group_name}\u0000${run.run_date}`;
}

export function formatDateTime(value: unknown): string {
  if (!value) return "—";
  return String(value).replace("T", " ").slice(0, 16);
}

export function describeLoadError(scope: string, reason: unknown): string {
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

function statusTone(
  status: string,
): "success" | "warning" | "danger" | "info" | "neutral" {
  if (["SENT", "IMAGE_READY", "READY_TO_SEND"].includes(status)) return "success";
  if (status === "FAILED") return "danger";
  if (["PROMPT_READY", "RANKING_READY"].includes(status)) return "info";
  if (["PENDING", "DATA_READY"].includes(status)) return "warning";
  return "neutral";
}

export function StatusPill({ status }: { status: string }) {
  const normalized = status.toUpperCase();
  return (
    <StatusBadge tone={statusTone(normalized)}>
      {STATUS_LABELS[normalized] || status || "未知"}
    </StatusBadge>
  );
}

export function renderGroupPreview(
  content: string,
  group: GroupV2 | undefined,
  themeText: string,
): string {
  const name = group?.display_name
    || group?.wechat_group_name
    || "群名称（选择目标群后填入）";
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
