import {get,post} from './api';
import type {MemoryEntry} from './memoryApi';
export interface Storyline {id: number;group_id: number;title: string;summary: string;version: number;entries?: MemoryEntry[]}
export interface StoryInput {group_id: number;title: string;entry_ids: number[];storyline_id: number | null}
export interface StoryPreview extends StoryInput {version: string;warnings: string[];entries: MemoryEntry[];subjects: string[]}
export const storylineApi={
  list:(group?: string)=>get<{items: Storyline[]}>(`/v2/storylines${group?'?group_id='+encodeURIComponent(group):''}`),
  detail:(id: number)=>get<Storyline>(`/v2/storylines/${id}`),
  preview:(input: StoryInput)=>post<StoryPreview>('/v2/storylines/link-preview',input),
  link:(p: StoryPreview)=>post<{id: number}>('/v2/storylines/link',{group_id:p.group_id,title:p.title,entry_ids:p.entry_ids,storyline_id:p.storyline_id,expected_version:p.version}),
  unlink:(s: Storyline,entry_id: number)=>post(`/v2/storylines/${s.id}/unlink`,{entry_id,expected_version:s.version}),
};
