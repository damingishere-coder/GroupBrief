import { get, post, patch, put } from './api';
export interface MemorySource { message_id: number; claim_key: string; quote: string; relation: string; validation_state: string }
export interface MemoryEntry { id: number; memory_id: number; summary: string; observed_start: string; event_at: string | null; event_time_basis: string; claims: { key: string; text: string }[]; sources: MemorySource[] }
export interface Memory { id: number; group_id: number; type: string; title: string; summary: string; status: string; version: number; merged_into_id: number | null; merge_operation_id?: number | null; last_source_at: string; keywords: string[]; entries?: MemoryEntry[]; next_offset?: number | null }
export interface MergePreview { source: Memory; target: Memory; version: string; warnings: string[] }
export interface MemoryPreview { version: string; filters: { group_id: number | null; start: string; end: string; mode: string }; message_count: number; ai_calls_estimate: number; warnings: string[] }
export interface MemoryStatus { enabled: boolean; ai_enabled: boolean; limits: {calls: number; input: number; output: number}; groups: {group_id: number; calls: number; reserved_input: number; reserved_output: number}[]; operations: {id: number; job_id: number; status: string}[] }
export const memoryApi = {
  budgets: (calls: number,input: number,output: number) => put('/settings',{values:{knowledge_memory_call_budget:String(calls),knowledge_memory_input_budget:String(input),knowledge_memory_output_budget:String(output)}}),
  list: (params: URLSearchParams) => get<{ items: Memory[]; next_offset: number | null }>(`/v2/memories?${params}`),
  detail: (id: number, offset=0) => get<Memory>(`/v2/memories/${id}?offset=${offset}`),
  previewMerge: (source_id: number, target_id: number) => post<MergePreview>('/v2/memories/merge-preview', { source_id, target_id }),
  merge: (p: MergePreview) => post<{ operation_id: number }>('/v2/memories/merge', { source_id:p.source.id,target_id:p.target.id,expected_version:p.version }),
  undo: (m: Memory, operation_id: number) => post(`/v2/memories/${m.id}/undo-merge`, { operation_id,expected_version:m.version }),
  edit: (m: Memory,title: string,status: string) => patch<Memory>(`/v2/memories/${m.id}`,{title,status,expected_version:m.version}),
  status: () => get<MemoryStatus>('/v2/knowledge/memory/status'),
  preview: (group_id?: number,mode='reuse') => post<MemoryPreview>('/v2/knowledge/memory/backfill/preview',{group_id,mode}),
  backfill: (p: MemoryPreview) => post<{job_ids: number[]}>('/v2/knowledge/memory/backfill',{...p.filters,expected_version:p.version}),
  resolve: (job: number,operation_id: number,resolution: string,note: string) => post(`/v2/knowledge/jobs/${job}/resolve-unknown`,{operation_id,resolution,note}),
};
