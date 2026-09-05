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
  next_send_at: string;
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
  summary_provider: string;
  prompt_provider: string;
  summary_model: string;
  prompt_model: string;
  image_enabled: boolean;
  send_target: string;
  effective_send_target: string;
  send_target_mode: "auto" | "manual";
  ranking_template: string;
  ranking_count_policy: "all_messages" | "text_primary_with_interactions";
  sender_name_policy: "resolved" | "wechat_data_analysis";
  image_prompt_template: string;
  image_theme: string;
  image_theme_custom: string;
  image_theme_remaining_runs?: number;
  has_image_prompt_override: boolean;
  wechat_send_enabled: boolean;
}

export interface GroupPayload {
  display_name: string;
  wechat_group_id: string;
  wechat_group_name?: string;
  enabled: boolean;
  provider_preference: string;
  schedule_rule: string;
  send_time: string;
  summary_provider: string;
  prompt_provider: string;
  summary_model: string;
  prompt_model: string;
  image_enabled: boolean;
  send_target: string;
  ranking_template: string;
  ranking_count_policy: "all_messages" | "text_primary_with_interactions";
  sender_name_policy: "resolved" | "wechat_data_analysis";
  image_prompt_template: string;
  image_theme?: string;
  image_theme_custom?: string;
  image_theme_apply_count?: number;
  image_prompt_override?: string;
  wechat_send_enabled?: boolean;
}

export interface DashboardCard {
  group_id: number;
  group_name: string;
  send_time: string;
  schedule_rule: string;
  image_enabled: boolean;
  ranking_template: string;
  ranking_count_policy: "all_messages" | "text_primary_with_interactions";
  image_prompt_template: string;
  status: string;
  period_start: string;
  period_end: string;
  message_count: number;
  speaker_count: number;
  image_url: string;
  image_status: string;
  image_fallback_level: number;
  image_fallback_reason: string;
  image_variant: string;
  image_delivery_eligible: boolean;
  ranking_preview: {
    rank: number;
    name: string;
    count: number;
    text_count: number;
    interaction_count: number;
    name_source: string;
  }[];
  ranking_error: string;
  error: string;
  sent_at: string;
  prompt_hold: boolean;
  prompt_hold_reason: string;
  prompt_operation_id: string;
  prompt_operation_status: string;
  wechat_send_enabled: boolean;
  send_hold: boolean;
  send_state: string;
  send_hold_reason: string;
  send_error: string;
  send_error_type: string;
  send_unknown_at: string;
  updated_at: string;
}

export type RuntimeNodeStatus = "pending" | "running" | "success" | "retry_pending" | "held" | "failed";

export interface RuntimeNode {
  id: "scheduler" | "data" | "ranking" | "prompt" | "image" | "send";
  label: string;
  status: RuntimeNodeStatus;
  completed_groups: number;
  total_groups: number;
}

export interface RuntimeGroup {
  group_id: string;
  group_name: string;
  run_status: string;
  current_node: RuntimeNode["id"];
  current_node_label: string;
  node_status: RuntimeNodeStatus;
  nodes: Pick<RuntimeNode, "id" | "label" | "status">[];
  last_error_type: string;
  last_error_summary: string;
  updated_at: string;
}

export type RuntimeOverallStatus =
  | "not_started"
  | "running"
  | "retry_pending"
  | "complete"
  | "partial"
  | "blocked"
  | "failed"
  | "needs_attention";

export interface DashboardRuntime {
  schema_version: number;
  run_date: string;
  run_id: string;
  updated_at: string;
  overall_status: RuntimeOverallStatus;
  scheduler: {
    scheduled_at?: string;
    send_scheduled_at?: string;
    next_generate_at?: string;
    next_send_at?: string;
    generation_started_at?: string;
    generation_completed_at?: string;
    generation_status?: string;
    state_status?: string;
    generation_error?: string;
  };
  summary: Record<string, unknown>;
  nodes: RuntimeNode[];
  groups: RuntimeGroup[];
}

export interface RuntimeLogItem {
  timestamp: string;
  level: "DEBUG" | "INFO" | "WARNING" | "ERROR" | "CRITICAL";
  source: "scheduler" | "app" | "provider" | "ai";
  message: string;
  redacted_or_truncated?: boolean;
}

export interface RuntimeLogsResponse {
  run_date: string;
  updated_at: string;
  items: RuntimeLogItem[];
  truncated: boolean;
}

export interface Dashboard {
  today: string;
  should_run: boolean;
  period_start: string;
  period_end: string;
  enabled_groups: number;
  counts: { pending: number; generated: number; sent: number; failed: number; held: number };
  next_send: string;
  daily_status: {
    overall_status: RuntimeOverallStatus;
    updated_at?: string;
    summary: Record<string, unknown>;
  };
  runtime: DashboardRuntime;
  cards: DashboardCard[];
}

export interface V2Run {
  group_name: string;
  run_date: string;
  status: string;
  period_start?: string;
  period_end?: string;
  files?: string[];
  [key: string]: unknown;
}

export interface V2RunDetail {
  run: V2Run;
  files: string[];
}

export type ArchiveGroupState = "active" | "deleted" | "orphaned";

export interface ArchiveGroup {
  archive_key: string;
  group_id: number | null;
  wechat_group_id: string;
  display_name: string;
  state: ArchiveGroupState;
  enabled: boolean;
  deleted_at: string | null;
  created_at: string;
  run_count: number;
  run_dates: string[];
  runs: V2Run[];
}

export interface ArchiveGroupsResponse {
  groups: ArchiveGroup[];
  active_count: number;
  trash_count: number;
}

export interface ArchivedMessage {
  message_id: string;
  group_id: string;
  group_name: string;
  sender_id: string;
  sender_name: string;
  timestamp: string;
  message_type: string;
  content: string;
}

export interface SystemHealth {
  checks: Record<string, { ok: boolean; status: string; detail: string }>;
  warnings?: string[];
}

export interface SystemReadiness extends SystemHealth {
  ready: boolean;
  scheduler_owner: string;
  scheduler_active: boolean;
}

export interface StartupCheck {
  checks: { name: string; ok: boolean; status: string; detail: string }[];
}

export interface RecoveryInfo {
  incomplete: V2Run[];
  integrity: { group_name: string; run_date: string; status: string; missing: string[]; ok: boolean }[];
}

export interface RecoveryBacklogItem {
  run_date: string;
  group_id: number | null;
  group_name: string;
  status: string;
  reason: string;
  safe_stage: "generation_only" | "manual_review_only";
  recoverable: boolean;
  manifest_source: string;
  estimated_summary_calls?: number;
  estimated_image_calls?: number;
}

export interface RecoveryBacklog {
  generated_at: string;
  automatic_recovery_dates: string[];
  lookback_days: number;
  version: string;
  items: RecoveryBacklogItem[];
}

export interface ProviderCatalogResponse {
  catalog: {
    history: { provider: string; label: string; available: boolean; capabilities: string[] }[];
    ai: { provider: string; label: string; available: boolean; models: string[]; capabilities: string[] }[];
  };
  [key: string]: unknown;
}

export interface WeeklyInsight {
  schema_version: number;
  week_start: string;
  week_end: string;
  group_id: number;
  group_name: string;
  status: string;
  narrative: string;
  narrative_source: string;
  ai_status: string;
  actual_provider: string;
  actual_model: string;
  generated_at: string;
  aggregation: {
    message_count: number;
    missing_days: string[];
    contributors: { identity_key: string; name: string; count: number }[];
    topics: { title: string; days: number }[];
  };
  card_url?: string;
}

export interface TemplateItem {
  name: string;
  content: string;
}

export interface ImageThemeOption {
  key: string;
  label: string;
  description: string;
  kind: "mode" | "preset";
  category: string;
  swatches: string[];
  variation_count: number;
  preview_url: string;
}

export interface ResolvedImageTheme {
  requested_key: string;
  actual_key: string;
  display_name: string;
  theme_text: string;
  prompt: string;
  style_signature: string;
  style_seed: string;
}

export interface GroupImagePromptConfig {
  group_id: number;
  template_name: string;
  source: "global" | "group_override";
  content: string;
  revision: string;
  image_theme: string;
  image_theme_custom: string;
  image_theme_remaining_runs?: number;
  resolved_theme: Record<string, string>;
  preview: string;
}

export interface BatchImageThemeSuccess {
  group_id: number;
  group_name: string;
  remaining_runs: number;
}

export interface BatchImageThemeFailure {
  group_id: number;
  code: "GROUP_NOT_FOUND" | "GROUP_DELETED" | "DATABASE_SAVE_FAILED";
  reason: string;
}

export interface BatchImageThemeResponse {
  status: "success" | "partial" | "failed";
  requested_count: number;
  success: BatchImageThemeSuccess[];
  failed: BatchImageThemeFailure[];
}

export interface RunPromptConfig {
  group_name: string;
  run_date: string;
  content: string;
  revision: string;
  has_original: boolean;
  image_theme: string;
  image_theme_custom: string;
  prompt_edited_at: string;
  topic_selection?: TopicSelection | null;
}

export interface TopicScores {
  discussion: number;
  participation: number;
  comedy: number;
  group_recognition: number;
  visual: number;
  continuity: number;
  total: number;
}

export interface TopicCandidate {
  topic_id: string;
  rank: number;
  title: string;
  summary: string;
  evidence_message_count: number;
  participant_count: number;
  duration_minutes: number;
  score_reason: string;
  selected: boolean;
  scores: TopicScores;
}

export interface TopicSelection {
  topic_selection_version: string;
  candidate_count: number;
  selected_count: number;
  selected_topic_ids: string[];
  candidates: TopicCandidate[];
}

export interface ImageCandidate {
  candidate_id: string;
  job_id: string;
  group_id: number | string;
  wechat_group_id: string;
  group_name: string;
  run_date: string;
  sha256: string;
  size_bytes: number;
  preview_url: string;
}

// 群发现 / 解析绑定 / 测试读取（兼容保留能力）
export interface DiscoveredGroup {
  group_id: string;
  group_name: string;
  member_count: number;
}
export interface GroupMatch {
  id: string;
  name: string;
  member_count: number;
  provider: string;
  match_type: string;
}
export interface TestReadResult {
  provider: string;
  status: string;
  detail: string;
  message_count: number;
  raw_message_count: number;
}
export interface GroupNameSyncChange {
  id: number | null;
  wechat_group_id: string;
  old_name: string;
  new_name: string;
}
export interface GroupNameSyncSkip {
  id: number | null;
  wechat_group_id: string;
  reason: string;
}
export interface GroupNameSyncResult {
  status: "ok" | "partial" | "unavailable";
  source: string;
  checked: number;
  updated: GroupNameSyncChange[];
  unchanged: number;
  skipped: GroupNameSyncSkip[];
  synced_at: string;
  detail: string;
}
export const discoverGroups = () => get<DiscoveredGroup[]>("/groups/discover");
export const resolveGroups = (name: string) =>
  get<GroupMatch[]>(`/groups/resolve?name=${encodeURIComponent(name)}`);
export const bindGroupFromName = (body: { name: string; group_id?: string }) =>
  post<{ id: number; bound: boolean; already_existed: boolean; restored?: boolean; enabled?: boolean }>("/groups/from-name", body);
export const testReadGroup = (groupId: number) =>
  post<TestReadResult>(`/groups/${groupId}/test-read`);

export const listGroups = () => get<GroupV2[]>("/groups");
export const getProviderCatalog = () => get<ProviderCatalogResponse>("/system/providers");
export const syncWechatGroupNames = () => post<GroupNameSyncResult>("/groups/sync-wechat-names");
export const createGroup = (body: GroupPayload) => post<{ id: number; restored?: boolean }>("/groups", body);
export const updateGroup = (groupId: number, body: Partial<GroupPayload>) =>
  put<{ id: number }>(`/groups/${groupId}`, body);
export const batchUpdateGroupImageTheme = (body: {
  group_ids: number[];
  image_theme: string;
  image_theme_custom: string;
  image_theme_apply_count: number;
}) => put<BatchImageThemeResponse>("/groups/batch/image-theme", body);
export const verifyGroupSendTarget = (groupId: number) =>
  post<{ ok: boolean; target: string; detail: string }>(`/groups/${groupId}/verify-send-target`);
export const deleteGroup = (groupId: number) => del<{ ok: boolean; deleted_at: string | null }>(`/groups/${groupId}`);
export const restoreGroup = (groupId: number) =>
  post<{ ok: boolean; id: number; enabled: boolean; wechat_send_enabled: boolean }>(`/groups/${groupId}/restore`);

export const getDashboard = (runDate?: string) =>
  get<Dashboard>(`/v2/dashboard${runDate ? `?run_date=${encodeURIComponent(runDate)}` : ""}`);
export const getRuntimeLogs = (
  runDate: string,
  options: { tail?: number; sources?: string; levels?: string } = {},
) => {
  const params = new URLSearchParams({
    run_date: runDate,
    tail: String(options.tail ?? 100),
  });
  if (options.sources) params.set("sources", options.sources);
  if (options.levels) params.set("levels", options.levels);
  return get<RuntimeLogsResponse>(`/v2/runtime/logs?${params.toString()}`);
};
export const getArchiveGroups = () => get<ArchiveGroupsResponse>("/v2/archive/groups");
export const getRuns = (runDate?: string, options?: { includeFiles?: boolean }) => {
  const params = new URLSearchParams();
  if (runDate) params.set("run_date", runDate);
  if (options?.includeFiles) params.set("include_files", "true");
  const query = params.toString();
  return get<{ runs: V2Run[]; total: number }>(`/v2/runs${query ? `?${query}` : ""}`);
};
export const getRunDetail = (group: string, date: string) =>
  get<V2RunDetail>(`/v2/runs/${encodeURIComponent(group)}/${date}`);
export type SettingsValues = Record<string, string>;
export const getSettings = () => get<SettingsValues>("/settings");
export const saveSettings = (values: SettingsValues) => put<{ ok: boolean }>("/settings", { values });
export const getSystemHealth = () => get<SystemHealth>("/v2/system/health");
export const getSystemReadiness = () => get<SystemReadiness>("/system/ready");
export const getStartupChecks = () => get<StartupCheck>("/v2/system/startup");
export const getRecoveryInfo = () => get<RecoveryInfo>("/v2/system/recovery");
export const getRecoveryBacklog = (lookbackDays = 30) =>
  get<RecoveryBacklog>(`/v2/recovery/backlog?lookback_days=${lookbackDays}`);
export const confirmRecovery = (body: { expected_version: string; tasks: { run_date: string; group_id: number }[] }) =>
  post<{ status: string; generation_only: boolean; send_invoked: boolean; results: { status: string; group_name?: string }[] }>("/v2/recovery/confirm", body);
export const listWeeklyInsights = () => get<{ schema_version: number; items: WeeklyInsight[] }>("/v2/weekly");
export const getWeeklyInsight = (weekStart: string, groupId: number) =>
  get<WeeklyInsight>(`/v2/weekly/${weekStart}/${groupId}`);
export const retryFailed = (body: { group_id?: number; run_date?: string }) =>
  post<{ results: { group_name?: string; status: string; detail?: string }[] }>("/v2/pipeline/retry-failed", body);
export const pipelineGenerate = (body: { group_id?: number; run_date?: string; force?: boolean; refresh_messages?: boolean }) =>
  post<{ results: { status: string; group_name?: string; error_type?: string; detail?: string }[] }>("/v2/pipeline/generate", body);
export const pipelineSendDue = () => post<{ results: { status: string; group_name?: string }[] }>("/v2/pipeline/send-due");
export const pipelineSend = (body: { group_id: number; run_date?: string; confirm_regenerated?: boolean; confirm_late_send?: boolean }) =>
  post<{ result: { status: string; group_name?: string; error_type?: string; error?: string; detail?: string } }>("/v2/pipeline/send", body);
export const resolveSendUnknown = (body: { group_id: number; run_date: string; resolution: "text_sent" | "not_sent"; expected_send_unknown_at: string }) =>
  post<{ result: { status: string; group_name: string; resolution: string; next_stage: string; detail: string } }>("/v2/pipeline/resolve-send-unknown", body);
export const resetSendFailure = (body: { group_id: number; run_date: string; expected_updated_at: string; expected_state_version: number }) =>
  post<{ result: { status: "prepared"; group_name: string; send_state: "ready"; run_status: string; updated_at: string; state_version: number; detail: string } }>("/v2/pipeline/reset-send-failure", body);
export const resolvePromptUnknown = (body: { group_id: number; run_date: string; expected_operation_id: string }) =>
  post<{ result: { status: string; group_name: string; resolution: string; next_stage: string; detail: string } }>("/v2/pipeline/resolve-prompt-unknown", body);
export const resolveManualSend = (body: { group_id: number; run_date: string; resolution: "all_sent" | "text_sent" | "not_sent"; expected_updated_at: string }) =>
  post<{ result: { status: string; group_name: string; resolution: string; next_stage: string; detail: string; run_status: string; updated_at: string } }>("/v2/pipeline/resolve-manual-send", body);
export const getV2File = (group: string, date: string, file: string) =>
  `/api/v2/files/${encodeURIComponent(group)}/${date}/${file}`;

export async function readV2TextFile(group: string, date: string, file: string): Promise<string> {
  const response = await fetch(getV2File(group, date, file));
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`读取 ${file} 失败：${detail || `HTTP ${response.status}`}`);
  }
  return response.text();
}

export async function readV2JsonFile<T>(group: string, date: string, file: string): Promise<T> {
  const text = await readV2TextFile(group, date, file);
  try {
    return JSON.parse(text) as T;
  } catch {
    throw new Error(`${file} 不是有效的 JSON 文件`);
  }
}

// AI 图片主题、群级默认 Prompt 与运行级 Prompt
export const listImageThemes = () => get<{ themes: ImageThemeOption[] }>("/v2/image-themes");
export const resolveImageTheme = (body: { image_theme: string; image_theme_custom?: string; prompt?: string; group_id?: number | string; run_date?: string }) =>
  post<ResolvedImageTheme>("/v2/image-themes/resolve", body);
export const getGroupImagePrompt = (groupId: number) =>
  get<GroupImagePromptConfig>(`/groups/${groupId}/image-prompt`);
export const saveGroupImagePrompt = (
  groupId: number,
  body: { content: string; inherit_global: boolean; image_theme: string; image_theme_custom?: string; expected_revision: string },
) => put<GroupImagePromptConfig>(`/groups/${groupId}/image-prompt`, body);
export const getRunPrompt = (group: string, date: string) =>
  get<RunPromptConfig>(`/v2/runs/${encodeURIComponent(group)}/${date}/prompt`);
export const saveRunPrompt = (
  group: string,
  date: string,
  body: { content: string; expected_revision: string; image_theme: string; image_theme_custom?: string },
) => put<RunPromptConfig>(`/v2/runs/${encodeURIComponent(group)}/${date}/prompt`, body);
export const restoreRunPrompt = (group: string, date: string) =>
  post<RunPromptConfig>(`/v2/runs/${encodeURIComponent(group)}/${date}/prompt/restore`);
export const refreshRunMessages = (group: string, date: string) =>
  post<{ result: { status: string; detail?: string }; run: V2Run }>(`/v2/runs/${encodeURIComponent(group)}/${date}/refresh-messages`);
export const rebuildRunPrompt = (group: string, date: string) =>
  post<{ result: { status: string; detail?: string }; run: V2Run }>(`/v2/runs/${encodeURIComponent(group)}/${date}/rebuild-prompt`);
export const regenerateRunImage = (group: string, date: string) =>
  post<{ accepted: boolean; run: V2Run }>(`/v2/runs/${encodeURIComponent(group)}/${date}/regenerate-image`);
export const getRunImageCandidates = (group: string, date: string) =>
  get<{ candidates: ImageCandidate[] }>(`/v2/runs/${encodeURIComponent(group)}/${date}/image-candidates`);
export const claimRunImageCandidate = (
  group: string,
  date: string,
  body: { job_id: string; candidate_id: string },
) => post<{ claimed: boolean; run: V2Run }>(`/v2/runs/${encodeURIComponent(group)}/${date}/image-candidates/claim`, body);

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
