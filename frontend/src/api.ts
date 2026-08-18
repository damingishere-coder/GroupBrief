const BASE = "/api";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export const get = <T>(path: string) => request<T>(path);
export const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: JSON.stringify(body ?? {}) });
export const put = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: "PUT", body: JSON.stringify(body ?? {}) });
export const del = <T>(path: string) => request<T>(path, { method: "DELETE" });

export interface Group {
  id: number;
  display_name: string;
  wechat_group_id: string;
  wechat_group_name: string;
  enabled: boolean;
  provider_preference: string;
  created_at: string;
  updated_at: string;
}

export interface SystemStatus {
  version: string;
  status: string;
  now: string | null;
  timezone: string;
  report_date: string;
  range_start: string;
  range_end: string;
  should_run_today: boolean;
  is_weekend_summary: boolean;
  next_generate_at: string;
  enabled_groups: number;
  total_groups: number;
}

export interface Run {
  id: number;
  report_date: string;
  range_start: string;
  range_end: string;
  trigger_type: string;
  status: string;
  started_at: string | null;
  finished_at: string | null;
  error_message: string;
}

export interface LatestReport {
  id: number;
  group_run_id: number;
  group_id: number | null;
  ranking_text: string;
  prompt_text: string;
  ranking_file: string;
  prompt_file: string;
  poster_file: string;
  poster_status: string;
  email_status: string;
  created_at: string | null;
}

// ================= V2 =================

export interface GroupV2 extends Group {
  schedule_rule: string;
  send_time: string;
  summary_model: string;
  prompt_model: string;
  image_enabled: boolean;
  send_target: string;
  ranking_template: string;
  image_prompt_template: string;
}

export interface DashboardCard {
  group_id: number;
  group_name: string;
  send_time: string;
  schedule_rule: string;
  image_enabled: boolean;
  ranking_template: string;
  image_prompt_template: string;
  status: string;
  period_start: string;
  period_end: string;
  message_count: number;
  speaker_count: number;
  image_url: string;
  error: string;
  sent_at: string;
  updated_at: string;
}

export interface Dashboard {
  today: string;
  should_run: boolean;
  period_start: string;
  period_end: string;
  enabled_groups: number;
  counts: { pending: number; generated: number; sent: number; failed: number };
  next_send: string;
  cards: DashboardCard[];
}

export interface V2Run {
  group_name: string;
  run_date: string;
  status: string;
  period_start?: string;
  period_end?: string;
  [key: string]: unknown;
}

export interface SystemHealth {
  checks: Record<string, { ok: boolean; status: string; detail: string }>;
}

export interface TemplateItem {
  name: string;
  content: string;
}

export const getDashboard = () => get<Dashboard>("/v2/dashboard");
export const getRuns = (runDate?: string) =>
  get<{ runs: V2Run[]; total: number }>(`/v2/runs${runDate ? `?run_date=${runDate}` : ""}`);
export const getRunDetail = (group: string, date: string) =>
  get<{ run: V2Run; files: string[] }>(`/v2/runs/${encodeURIComponent(group)}/${date}`);
export const getSystemHealth = () => get<SystemHealth>("/v2/system/health");
export const pipelineGenerate = (body: { group_id?: number; run_date?: string; force?: boolean }) =>
  post<{ results: { status: string; group_name?: string; error_type?: string }[] }>("/v2/pipeline/generate", body);
export const pipelineSendDue = () => post<{ results: { status: string; group_name?: string }[] }>("/v2/pipeline/send-due");
export const pipelineSend = (body: { group_id: number; run_date?: string }) =>
  post<{ result: { status: string; group_name?: string; error_type?: string } }>("/v2/pipeline/send", body);
export const getV2File = (group: string, date: string, file: string) =>
  `/api/v2/files/${encodeURIComponent(group)}/${date}/${file}`;

// 模板中心
export const listRankingTemplates = () => get<{ templates: string[]; previews: Record<string, string> }>("/v2/templates/ranking");
export const getRankingTemplate = (name: string) => get<TemplateItem>(`/v2/templates/ranking/${name}`);
export const saveRankingTemplate = (name: string, content: string) =>
  put<{ ok: boolean }>(`/v2/templates/ranking/${name}`, { content });
export const resetRankingTemplate = (name: string) =>
  post<{ ok: boolean; content: string }>(`/v2/templates/ranking/${name}/reset`);
export const deleteRankingTemplate = (name: string) => del<{ ok: boolean }>(`/v2/templates/ranking/${name}`);
export const previewRankingTemplate = (name: string, content: string) =>
  post<{ ok: boolean; rendered: string }>(`/v2/templates/ranking/${name}/preview`, { content });

export const listImagePromptTemplates = () => get<{ templates: string[] }>("/v2/templates/image_prompt");
export const getImagePromptTemplate = (name: string) => get<TemplateItem>(`/v2/templates/image_prompt/${name}`);
export const saveImagePromptTemplate = (name: string, content: string) =>
  put<{ ok: boolean }>(`/v2/templates/image_prompt/${name}`, { content });
export const resetImagePromptTemplate = (name: string) =>
  post<{ ok: boolean; content: string }>(`/v2/templates/image_prompt/${name}/reset`);
export const deleteImagePromptTemplate = (name: string) => del<{ ok: boolean }>(`/v2/templates/image_prompt/${name}`);
