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
  ranking_text: string;
  prompt_text: string;
  ranking_file: string;
  prompt_file: string;
  poster_file: string;
  poster_status: string;
  email_status: string;
  created_at: string | null;
}
