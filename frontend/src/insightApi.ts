import { get, post } from './api';
import type { MessageRecord } from './knowledgeApi';
export type InsightKind = 'weekly';
export interface InsightSummary {
  id: number; group_id: number; kind: InsightKind; period_start: string; period_end: string;
  revision: number; status: string; ai_status: string;
}
export interface MemberInsight {
  identity_key: string; rank: number; name: string; count: number; active_days: number;
  rank_change: number | null; count_change: number | null; active_days_change: number | null;
  new_to_top: boolean; newly_active: boolean;
}
export interface CoverageStatus { complete: boolean; gaps: { start: string; end: string }[]; invalid_messages: number; source_scopes: string[] }
export interface MetricChange { current: number; previous: number; delta: number | null; percent: number | null; state: string }
export interface InsightDetail extends InsightSummary {
  sections?: InsightSection[];
  metrics_version: string; coverage: { current: CoverageStatus; previous: CoverageStatus };
  metrics: { message_count: number; speaker_count: number; uncertain_identity_messages: number; count_policy: string;
    active_people_min: number; active_people_max: number; members: MemberInsight[];
    champion: MemberInsight | null; comparison: Record<string, MetricChange>;
    daily_counts: { date: string; count: number | null; complete: boolean }[];
    inactive_previous_members: MemberInsight[];
  };
}
export interface InsightSection {
  key: string; kind: string; title: string; summary?: string; note?: string; memory_id?: number; memory_entry_id?: number; storyline_id?: number; review_status?: string;
  claims?: {key: string; text: string; message_ids: number[]}[];
  items?: {memory_id: number; title: string; discussion_days: number; participants: number; evidence_messages: number; entry_count: number; lifecycle: string}[];
}
export const insightApi = {
  list: (group?: string, history = false) => get<{ items: InsightSummary[] }>(`/v2/insights?kind=weekly&include_history=${history}${group ? `&group_id=${encodeURIComponent(group)}` : ''}`),
  detail: (id: number) => get<InsightDetail>(`/v2/insights/${id}`),
  build: (group_id: number, day: string) => post<{ job_id: number }>('/v2/insights/build', { group_id, day, kind: 'weekly' }),
  messages: (id: number, offset = 0) => get<{ items: MessageRecord[]; next_offset: number | null }>(`/v2/insights/${id}/messages?offset=${offset}`),
};
