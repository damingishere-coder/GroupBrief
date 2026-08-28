export const TEXT_PRIMARY_WITH_INTERACTIONS = "text_primary_with_interactions";

export const INTERACTION_EXPLANATION = "说明：互动指图片、表情、引用等非文字消息，仅展示活跃度，不影响排名。";

export type RankingCountPolicy = "all_messages" | "text_primary_with_interactions";

export interface RankingCountFields {
  count: number;
  text_count?: number;
  interaction_count?: number;
}

export interface ParsedRankingSpeaker {
  rank: number;
  name: string;
  count: number;
  textCount: number;
  interactionCount: number;
  nameSource: string;
}

export interface ParsedRankingSummary {
  groupName: string;
  periodStart: string;
  periodEnd: string;
  messageCount: number | null;
  speakerCount: number | null;
  countPolicy: RankingCountPolicy;
  textMessageCount: number | null;
  interactionMessageCount: number | null;
  textSpeakerCount: number | null;
  topSpeakers: ParsedRankingSpeaker[];
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function asText(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function asCount(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value) && value >= 0) return Math.round(value);
  return null;
}

export function isTextPrimaryRanking(policy: string): boolean {
  return policy === TEXT_PRIMARY_WITH_INTERACTIONS;
}

export function formatRankingCount(policy: string, speaker: RankingCountFields): string {
  if (!isTextPrimaryRanking(policy)) return `${speaker.count} 条`;
  return `文字 ${speaker.text_count ?? speaker.count}｜互动 ${speaker.interaction_count ?? 0}`;
}

export function parseRanking(value: unknown): { summary: ParsedRankingSummary | null; error: string } {
  const record = asRecord(value);
  if (!record) return { summary: null, error: "ranking.json 不是对象格式，无法解析排行榜。" };
  const rawSpeakers = record.top_speakers;
  if (!Array.isArray(rawSpeakers)) {
    return { summary: null, error: "ranking.json 缺少有效的 top_speakers 数组。" };
  }
  const countPolicy: RankingCountPolicy = asText(record.count_policy) === TEXT_PRIMARY_WITH_INTERACTIONS
    ? TEXT_PRIMARY_WITH_INTERACTIONS
    : "all_messages";
  const topSpeakers: ParsedRankingSpeaker[] = [];
  let malformed = false;
  rawSpeakers.forEach((item, index) => {
    const speaker = asRecord(item);
    const name = speaker ? asText(speaker.name).trim() : "";
    const count = speaker ? asCount(speaker.count) : null;
    if (!name || count === null) {
      malformed = true;
      return;
    }
    topSpeakers.push({
      rank: asCount(speaker?.rank) || index + 1,
      name,
      count,
      textCount: asCount(speaker?.text_count) ?? count,
      interactionCount: asCount(speaker?.interaction_count) ?? 0,
      nameSource: asText(speaker?.name_source),
    });
  });
  return {
    summary: {
      groupName: asText(record.group_name),
      periodStart: asText(record.period_start),
      periodEnd: asText(record.period_end),
      messageCount: asCount(record.message_count),
      speakerCount: asCount(record.speaker_count),
      countPolicy,
      textMessageCount: asCount(record.text_message_count),
      interactionMessageCount: asCount(record.interaction_message_count),
      textSpeakerCount: asCount(record.text_speaker_count),
      topSpeakers,
    },
    error: malformed ? "ranking.json 中有部分排行项格式异常，已跳过异常项。" : "",
  };
}
