import { get, post } from './api';
export interface SearchHit {
  type: 'message' | 'report' | 'memory'; id?: number; ref?: string; group_id: number | null;
  sender_name?: string; sent_at?: string; title?: string; period_start?: string;
  insight_id?: number; source_hash?: string; validation_state?: string;
  snippet: { text: string; match: boolean }[];
}
export interface SearchPage { items: SearchHit[]; next_cursor: string | null; index_version: number; data_version: string; warnings: string[]; ai_calls: number }
export interface LegacySearchReport { title: string; body: string; locator: string; source_hash: string; warnings: string[] }
export const searchApi = {
  query: (params: URLSearchParams) => get<SearchPage>(`/v2/search?${params}`),
  report: (ref: string, hash?: string) => get<LegacySearchReport>(`/v2/search/reports/${encodeURIComponent(ref)}${hash ? `?expected_hash=${encodeURIComponent(hash)}` : ''}`),
  index: (rebuild = false) => post<{ job_id: number }>('/v2/knowledge/index', { rebuild }),
};
