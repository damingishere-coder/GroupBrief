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
  ambiguous_result: "发现候选，等待人工认领",
  result_unknown: "结果未知，已停止重试",
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
  const stableId = run.group_id || run.wechat_group_id || run.group_name;
  return `${stableId}\u0000${run.wechat_group_id || ""}\u0000${run.run_date}`;
}

export function formatDateTime(value: unknown): string {
  if (!value) return "—";
  return String(value).replace("T", " ").slice(0, 16);
}

export function regenerationPollDelay(
  status: string,
  consecutiveFailures: number,
): number {
  const base = status === "fallback_queued" ? 5000 : 2000;
  return Math.min(base * (2 ** Math.max(0, consecutiveFailures)), 30_000);
}

export function describeApiError(reason: unknown): string {
  const raw = reason instanceof Error ? reason.message : String(reason);
  let detail = raw;
  try {
    const parsed = JSON.parse(raw) as { detail?: unknown };
    if (typeof parsed.detail === "string") detail = parsed.detail;
  } catch {
    // 非 JSON 错误直接保留原始信息。
  }
  return detail;
}

export function describeLoadError(scope: string, reason: unknown): string {
  const detail = describeApiError(reason);
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
    "{{main_title}}": "当天真实主标题（生成时自动填入）",
    "{{subtitle}}": "当天真实副标题（生成时自动填入）",
    "{{overall_visual}}": themeText
      ? `固定群聊漫画要求\n\n本次手动视觉风格：${themeText}`
      : "固定群聊漫画要求\n\n根据当天真实聊天内容自由选择统一视觉风格。",
    "{{panels}}": "【版面1】\n当天真实话题的完整漫画导演稿（生成时自动填入）",
    "{{text_rules}}": "固定文字合同（生成时自动填入）",
    "{{footer_summary}}": "当天真实底部总结（生成时自动填入）",
    "{{image_theme}}": themeText || "（在上方输入指定风格后自动填入）",
    "{{layout_name}}": "整张海报版式（生成时自动选择）",
    "{{layout_instruction}}": "版式结构指令（生成时自动填入）",
  };
  return Object.entries(variables).reduce(
    (preview, [token, value]) => preview.split(token).join(value),
    content.replace(/<!--[\s\S]*?-->/g, "").trim(),
  ).trim();
}
