import { get, post } from './api';

export interface MessageRecord {
  id: number; group_id: number | null; sender_name: string; sender_id: string;
  sent_at: string; content: string; message_type: string; validation_state: string;
  identity_kind: string; sender_identity_quality: string;
}
export interface MessagePage { items: MessageRecord[]; next_cursor: string | null; data_version: string }
export interface MessageSource {
  batch_id: number; row_ordinal: number; source_locator: string; artifact_sha256: string;
  coverage_state: string; raw_record: Record<string, unknown>;
}
export interface SourcePage { items: MessageSource[]; next_offset: number | null }
export interface MessageContext { before: MessageRecord[]; message: MessageRecord; after: MessageRecord[] }
export interface KnowledgeStatus {
  available: boolean; worker_enabled: boolean; reason?: string; message_count?: number;
  source_count?: number; coverage?: Record<string, number>; data_version?: string;
}
export interface KnowledgeJob {
  id: number; job_kind: string; status: string; error_code: string; pause_requested: number;
  checkpoint: { next_item?: number }; result: { errors?: { locator: string; error: string }[] };
}
export interface BackfillPreview {
  version: string; record_count: number; ai_calls: number;
  filters: { group_id?: number | null; start?: string | null; end?: string | null };
  items: { locator: string; group_id: number | null; coverage_state: string }[];
  errors: { locator: string; error: string }[];
}
export const knowledgeApi = {
  status: () => get<KnowledgeStatus>('/v2/knowledge/status'),
  jobs: () => get<{ items: KnowledgeJob[] }>('/v2/knowledge/jobs'),
  messages: (params: URLSearchParams) => get<MessagePage>(`/v2/messages?${params}`),
  context: (id: number) => get<MessageContext>(`/v2/messages/${id}/context`),
  sources: (id: number, offset = 0) => get<SourcePage>(`/v2/messages/${id}/sources?offset=${offset}`),
  preview: (group_id?: number) => post<BackfillPreview>('/v2/knowledge/backfill/preview', { group_id }),
  backfill: (p: BackfillPreview) => post<{ job_id: number }>('/v2/knowledge/backfill', { ...p.filters, expected_version: p.version }),
  control: (id: number, action: 'pause' | 'retry') => post<KnowledgeJob>(`/v2/knowledge/jobs/${id}/${action}`),
};
