import { useEffect, useState, type ReactNode } from "react";
import {
  ArrowLeft,
  Check,
  Compass,
  FloppyDisk,
  MagnifyingGlass,
  Play,
  Plus,
  ShieldCheck,
  UsersThree,
} from "@phosphor-icons/react";
import {
  DiscoveredGroup,
  GroupMatch,
  GroupPayload,
  GroupV2,
  createGroup,
  discoverGroups,
  listGroups,
  listImagePromptTemplates,
  listRankingTemplates,
  pipelineGenerate,
  resolveGroups,
  updateGroup,
  verifyGroupSendTarget,
} from "../../api";
import {
  Button,
  EmptyState,
  LoadingState,
  PageHeader,
  StatusBadge,
  Toast,
} from "../../components/common";
import { useToast } from "../../components/ui";
import { navigateToHash } from "../../navigation";

interface GroupDetailProps {
  groupId?: number;
  invalidGroupId?: string;
}

interface ExecutionState {
  tone: "success" | "warning" | "danger";
  title: string;
  detail: string;
}

const EMPTY_FORM: GroupPayload = {
  display_name: "",
  wechat_group_id: "",
  wechat_group_name: "",
  enabled: true,
  provider_preference: "",
  schedule_rule: "weekday_default",
  send_time: "08:30",
  summary_model: "gpt-5.6-sol",
  prompt_model: "gpt-5.6-sol",
  image_enabled: true,
  send_target: "",
  ranking_template: "default",
  image_prompt_template: "default",
  image_prompt_override: "",
  wechat_send_enabled: false,
};

function todayLocal(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(
    d.getDate()
  ).padStart(2, "0")}`;
}

function toForm(group: GroupV2): GroupPayload {
  return {
    display_name: group.display_name || "",
    wechat_group_id: group.wechat_group_id || "",
    wechat_group_name: group.wechat_group_name || "",
    enabled: group.enabled,
    provider_preference: group.provider_preference || "",
    schedule_rule: group.schedule_rule || "weekday_default",
    send_time: group.send_time || "08:30",
    summary_model: group.summary_model || "gpt-5.6-sol",
    prompt_model: group.prompt_model || "gpt-5.6-sol",
    image_enabled: group.image_enabled,
    send_target: group.send_target || "",
    ranking_template: group.ranking_template || "default",
    image_prompt_template: group.image_prompt_template || "default",
    wechat_send_enabled: group.wechat_send_enabled,
  };
}

function Field({
  id,
  label,
  error,
  required = false,
  children,
}: {
  id: string;
  label: string;
  error?: string;
  required?: boolean;
  children: ReactNode;
}) {
  return (
    <div className="group-detail-field">
      <label htmlFor={id}>{label}{required && <span className="required-mark"> *</span>}</label>
      {children}
      {error && <span className="group-detail-field-error">{error}</span>}
    </div>
  );
}

export default function GroupDetail({ groupId, invalidGroupId }: GroupDetailProps) {
  const { msg, toast } = useToast();
  const [form, setForm] = useState<GroupPayload>({ ...EMPTY_FORM });
  const [loading, setLoading] = useState(Boolean(groupId));
  const [loadError, setLoadError] = useState("");
  const [saving, setSaving] = useState(false);
  const [executing, setExecuting] = useState(false);
  const [executionDate, setExecutionDate] = useState(todayLocal());
  const [execution, setExecution] = useState<ExecutionState | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [rankingTemplates, setRankingTemplates] = useState<string[]>([]);
  const [imagePromptTemplates, setImagePromptTemplates] = useState<string[]>([]);
  const [templateError, setTemplateError] = useState("");
  const [discovered, setDiscovered] = useState<DiscoveredGroup[]>([]);
  const [discovering, setDiscovering] = useState(false);
  const [showDiscovered, setShowDiscovered] = useState(false);
  const [resolveQuery, setResolveQuery] = useState("");
  const [resolving, setResolving] = useState(false);
  const [matches, setMatches] = useState<GroupMatch[]>([]);
  const [verifyingTarget, setVerifyingTarget] = useState(false);

  useEffect(() => {
    let active = true;
    setForm({ ...EMPTY_FORM });
    setErrors({});
    setExecution(null);
    setLoadError("");
    setTemplateError("");

    if (invalidGroupId) {
      setLoading(false);
      setLoadError(`群 ID「${invalidGroupId}」无效`);
    } else if (groupId) {
      setLoading(true);
      listGroups()
        .then((groups) => {
          if (!active) return;
          const group = groups.find((item) => item.id === groupId);
          if (!group) {
            setLoadError(`找不到群 ID ${groupId}，它可能已被删除。`);
            return;
          }
          setForm(toForm(group));
        })
        .catch((error: unknown) => active && setLoadError(String(error)))
        .finally(() => active && setLoading(false));
    } else {
      setLoading(false);
    }

    const loadTemplates = async () => {
      const [rankingResult, promptResult] = await Promise.allSettled([
        listRankingTemplates(),
        listImagePromptTemplates(),
      ]);
      if (!active) return;
      const failures: string[] = [];
      if (rankingResult.status === "fulfilled") setRankingTemplates(rankingResult.value.templates);
      else failures.push(`排行榜模板：${String(rankingResult.reason)}`);
      if (promptResult.status === "fulfilled") setImagePromptTemplates(promptResult.value.templates);
      else failures.push(`Prompt 模板：${String(promptResult.reason)}`);
      if (failures.length > 0) {
        setTemplateError(`模板列表加载失败，已保留当前配置。${failures.join("；")}`);
        toast("模板列表加载失败，当前值仍可保存");
      }
    };
    void loadTemplates();

    return () => {
      active = false;
    };
  }, [groupId, invalidGroupId]);

  const setField = <K extends keyof GroupPayload>(field: K, value: GroupPayload[K]) => {
    setForm((current) => ({ ...current, [field]: value }));
    setErrors((current) => ({ ...current, [field]: "" }));
  };

  const pickDiscovered = (group: DiscoveredGroup) => {
    setField("display_name", group.group_name);
    setField("wechat_group_id", group.group_id);
    setField("wechat_group_name", group.group_name);
    setField("send_target", "");
    toast(`已回填「${group.group_name}」的真实群信息`);
  };

  const loadDiscovered = () => {
    if (discovering) return;
    setDiscovering(true);
    discoverGroups()
      .then((groups) => {
        setDiscovered(groups);
        setShowDiscovered(true);
        toast(`从微信发现 ${groups.length} 个群`);
      })
      .catch((error: unknown) => toast(`发现群失败：${String(error)}`))
      .finally(() => setDiscovering(false));
  };

  const searchGroups = () => {
    const query = resolveQuery.trim();
    if (!query || resolving) {
      if (!query) toast("请输入真实微信群名称关键词");
      return;
    }
    setResolving(true);
    resolveGroups(query)
      .then((result) => {
        setMatches(result);
        if (result.length === 0) toast("未找到匹配的真实微信群");
      })
      .catch((error: unknown) => toast(`搜索群失败：${String(error)}`))
      .finally(() => setResolving(false));
  };

  const validate = () => {
    const next: Record<string, string> = {};
    if (!form.display_name.trim()) next.display_name = "请填写展示名称";
    if (!form.wechat_group_id.trim()) next.wechat_group_id = "请绑定真实微信群 ID";
    if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(form.send_time.trim())) next.send_time = "请输入 HH:mm 格式，例如 08:30";
    setErrors(next);
    return Object.keys(next).length === 0;
  };

  const save = () => {
    if (saving || !validate()) return;
    setSaving(true);
    const payload: GroupPayload = {
      ...form,
      display_name: form.display_name.trim(),
      wechat_group_id: form.wechat_group_id.trim(),
      wechat_group_name: form.wechat_group_name?.trim() || form.display_name.trim(),
      send_target: form.send_target.trim(),
      send_time: form.send_time.trim(),
    };
    const request = groupId ? updateGroup(groupId, payload) : createGroup(payload);
    request
      .then((result) => {
        toast("restored" in result && result.restored ? "已恢复原群及历史归档，当前保持停用" : "群配置已保存");
        navigateToHash(`/groups/${result.id}`);
      })
      .catch((error: unknown) => toast(`保存失败：${String(error)}`))
      .finally(() => setSaving(false));
  };

  const execute = () => {
    if (!groupId || executing) return;
    setExecuting(true);
    setExecution(null);
    pipelineGenerate({ group_id: groupId, run_date: executionDate, force: true })
      .then((response) => {
        const result = response.results?.[0];
        if (!result) {
          setExecution({ tone: "warning", title: "接口未返回结果", detail: "请稍后在历史日报中查看任务状态。" });
        } else if (["failed", "error"].includes(result.status)) {
          setExecution({ tone: "danger", title: "执行失败", detail: result.detail || result.error_type || result.status });
        } else if (result.status === "skipped") {
          setExecution({ tone: "warning", title: "本次未执行", detail: result.detail || "该日期不生成日报。" });
        } else {
          setExecution({ tone: "success", title: "执行请求已完成", detail: result.detail || `当前状态：${result.status}` });
        }
      })
      .catch((error: unknown) => setExecution({ tone: "danger", title: "执行请求失败", detail: String(error) }))
      .finally(() => setExecuting(false));
  };

  const verifySendTarget = () => {
    if (!groupId || verifyingTarget) return;
    setVerifyingTarget(true);
    verifyGroupSendTarget(groupId)
      .then((result) => toast(`目标验证通过：${result.target}`))
      .catch((error: unknown) => toast(`目标验证未通过：${String(error)}`))
      .finally(() => setVerifyingTarget(false));
  };

  if (loading) return <LoadingState label="正在加载群配置…" />;
  if (loadError) {
    return (
      <EmptyState
        title="无法打开群配置"
        description={loadError}
        action={<Button tone="secondary" onClick={() => navigateToHash("/groups")}><ArrowLeft size={17} aria-hidden="true" />返回群聊配置</Button>}
      />
    );
  }

  const title = groupId ? "群配置详情" : "新增群聊";
  const currentRankingTemplates = rankingTemplates.includes(form.ranking_template)
    ? rankingTemplates
    : [form.ranking_template, ...rankingTemplates];
  const currentPromptTemplates = imagePromptTemplates.includes(form.image_prompt_template)
    ? imagePromptTemplates
    : [form.image_prompt_template, ...imagePromptTemplates];

  return (
    <div className="group-detail-page">
      <PageHeader
        title={title}
        description={groupId ? `群 ID ${groupId} · 修改后保存即可用于下一次任务。` : "绑定真实微信群后，再配置统计、模板与发送设置。"}
        actions={
          <>
            <StatusBadge tone={form.enabled ? "success" : "neutral"}>{form.enabled ? "已启用" : "已停用"}</StatusBadge>
            <Button tone="ghost" onClick={() => navigateToHash("/groups")}><ArrowLeft size={17} aria-hidden="true" />返回列表</Button>
            <Button onClick={save} busy={saving}><FloppyDisk size={17} aria-hidden="true" />保存配置</Button>
          </>
        }
      />

      {!groupId && (
        <section className="group-detail-card group-discovery-card">
          <div className="group-detail-section-heading">
            <div className="group-detail-heading-icon"><Compass size={20} aria-hidden="true" /></div>
            <div><h2>从微信发现真实群</h2><p>回填稳定 ID 和当前名称；发送目标默认自动跟随微信群名。</p></div>
          </div>
          <div className="group-discovery-actions">
            <Button tone="secondary" onClick={loadDiscovered} busy={discovering}><UsersThree size={17} aria-hidden="true" />发现微信群</Button>
            <div className="group-resolve-row">
              <label className="sr-only" htmlFor="group-resolve-query">按名称搜索微信群</label>
              <div className="groups-search-input"><MagnifyingGlass size={17} aria-hidden="true" /><input id="group-resolve-query" value={resolveQuery} placeholder="按名称搜索并绑定" onChange={(event) => setResolveQuery(event.target.value)} onKeyDown={(event) => event.key === "Enter" && searchGroups()} /></div>
              <Button tone="ghost" onClick={searchGroups} busy={resolving}>搜索</Button>
            </div>
          </div>
          {showDiscovered && <div className="group-discovery-list">
            {discovered.length === 0 ? <span className="groups-muted-cell">未发现群，请确认 WeChatDataAnalysis 已运行。</span> : discovered.map((group) => (
              <button type="button" className="group-discovery-item" key={group.group_id} onClick={() => pickDiscovered(group)} title={group.group_id}>
                <strong>{group.group_name}</strong><span>{group.member_count} 位成员</span>
              </button>
            ))}
          </div>}
          {matches.length > 0 && <div className="group-resolve-results">{matches.map((match) => (
            <div className="group-resolve-result" key={match.id}>
              <div><strong>{match.name}</strong><span>{match.match_type === "exact" ? "精确匹配" : "模糊匹配"} · {match.provider}</span></div>
              <Button tone="ghost" className="ui-button-compact" onClick={() => pickDiscovered({ group_id: match.id, group_name: match.name, member_count: match.member_count })}><Plus size={15} aria-hidden="true" />回填</Button>
            </div>
          ))}</div>}
        </section>
      )}

      <section className="group-detail-card">
        <div className="group-detail-section-heading"><div><h2>基础信息</h2><p>这些信息用于确定数据源中的真实群聊。</p></div></div>
        <div className="group-detail-form-grid">
          <Field id="display-name" label="展示 / 归档名称" required error={errors.display_name}>
            <input id="display-name" value={form.display_name} onChange={(event) => setField("display_name", event.target.value)} />
            <span className="group-detail-field-help">作为稳定的历史目录名称，不会随微信群改名自动变化。</span>
          </Field>
          <Field id="wechat-group-id" label="微信群 ID" required error={errors.wechat_group_id}>
            <input id="wechat-group-id" value={form.wechat_group_id} onChange={(event) => setField("wechat_group_id", event.target.value)} placeholder="例如 xxx@chatroom" />
          </Field>
          <Field id="wechat-group-name" label="微信当前群名（自动同步）">
            <input id="wechat-group-name" value={form.wechat_group_name || ""} onChange={(event) => setField("wechat_group_name", event.target.value)} />
            <span className="group-detail-field-help">系统会在每日生成和实际发送前按稳定群 ID 刷新。</span>
          </Field>
          <Field id="provider-preference" label="数据源偏好">
            <select id="provider-preference" value={form.provider_preference} onChange={(event) => setField("provider_preference", event.target.value)}>
              <option value="">自动选择</option>
              <option value="wechat_data_analysis">WeChatDataAnalysis</option>
              <option value="wechat-cli">wechat-cli</option>
            </select>
          </Field>
          <label className="group-detail-switch" htmlFor="group-enabled">
            <input id="group-enabled" type="checkbox" checked={form.enabled} onChange={(event) => setField("enabled", event.target.checked)} />
            <span><strong>启用该群</strong><small>启用后纳入自动生成任务</small></span>
          </label>
        </div>
      </section>

      <section className="group-detail-card">
        <div className="group-detail-section-heading"><div><h2>统计与规则</h2><p>V2 默认使用工作日规则：周一统计周五至周日，周末不生成。</p></div></div>
        <div className="group-detail-form-grid">
          <Field id="schedule-rule" label="统计周期规则">
            <select id="schedule-rule" value={form.schedule_rule} onChange={(event) => setField("schedule_rule", event.target.value)}>
              <option value="weekday_default">工作日默认（周一=周五至周日）</option>
            </select>
          </Field>
          <Field id="send-time" label="发送时间" required error={errors.send_time}>
            <input id="send-time" type="time" value={form.send_time} onChange={(event) => setField("send_time", event.target.value)} />
          </Field>
        </div>
      </section>

      <section className="group-detail-card">
        <div className="group-detail-section-heading"><div><h2>内容模块</h2><p>图片是可选模块，关闭后任务仍可生成排行榜文字。</p></div></div>
        <div className="group-detail-form-grid">
          <label className="group-detail-switch" htmlFor="image-enabled">
            <input id="image-enabled" type="checkbox" checked={form.image_enabled} onChange={(event) => setField("image_enabled", event.target.checked)} />
            <span><strong>启用 AI 图片</strong><small>启用后会进入串行生图阶段</small></span>
          </label>
          <Field id="send-target" label="发送目标（可选人工覆盖）" error={errors.send_target}>
            <input id="send-target" value={form.send_target} onChange={(event) => setField("send_target", event.target.value)} placeholder="留空则自动跟随微信当前群名" />
            <span className="group-detail-field-help">
              {form.send_target.trim()
                ? `人工覆盖：将搜索「${form.send_target.trim()}」`
                : `自动跟随：将搜索「${form.wechat_group_name?.trim() || form.display_name.trim() || "尚未设置"}」`}
            </span>
          </Field>
          <label className="group-detail-switch" htmlFor="wechat-send-enabled">
            <input id="wechat-send-enabled" type="checkbox" checked={Boolean(form.wechat_send_enabled)} onChange={(event) => setField("wechat_send_enabled", event.target.checked)} />
            <span><strong>允许微信自动发送</strong><small>默认关闭；仅在目标验证和文件传输助手验收完成后启用</small></span>
          </label>
          <div className="group-detail-field">
            <label>发送目标安全校验</label>
            <Button tone="secondary" onClick={verifySendTarget} busy={verifyingTarget} disabled={!groupId} title={!groupId ? "请先保存群配置" : "只打开并核对聊天，不发送内容"}>
              <ShieldCheck size={17} aria-hidden="true" />查找并验证目标
            </Button>
            <span className="groups-muted-cell">使用已保存配置；只操作搜索和聊天定位，不发送任何内容。</span>
          </div>
        </div>
      </section>

      <section className="group-detail-card">
        <div className="group-detail-section-heading"><div><h2>模型与模板</h2><p>{templateError || "模板选择来自真实模板列表 API。"}</p></div></div>
        <div className="group-detail-form-grid">
          <Field id="summary-model" label="摘要模型">
            <select id="summary-model" value={form.summary_model} onChange={(event) => setField("summary_model", event.target.value)}>
              <option value="gpt-5.6-sol">GPT-5.6 Sol（Codex 主用）</option>
            </select>
          </Field>
          <Field id="prompt-model" label="Prompt 模型">
            <select id="prompt-model" value={form.prompt_model} onChange={(event) => setField("prompt_model", event.target.value)}>
              <option value="gpt-5.6-sol">GPT-5.6 Sol（Codex 主用）</option>
            </select>
          </Field>
          <Field id="ranking-template" label="排行榜模板">
            <select id="ranking-template" value={form.ranking_template} onChange={(event) => setField("ranking_template", event.target.value)}>
              {currentRankingTemplates.map((name) => <option key={name} value={name}>{name}</option>)}
              {currentRankingTemplates.length === 0 && <option value="default">default（列表加载失败）</option>}
            </select>
          </Field>
          <Field id="image-prompt-template" label="生图 Prompt 模板">
            <select id="image-prompt-template" value={form.image_prompt_template} onChange={(event) => setField("image_prompt_template", event.target.value)}>
              {currentPromptTemplates.map((name) => <option key={name} value={name}>{name}</option>)}
              {currentPromptTemplates.length === 0 && <option value="default">default（列表加载失败）</option>}
            </select>
          </Field>
        </div>
      </section>

      <section className="group-detail-card group-detail-footer-card">
        <div className="group-detail-section-heading"><div><h2>保存与立即执行</h2><p>立即执行只对已保存群可用，后端完成后请到历史日报查看落盘结果。</p></div></div>
        <div className="group-detail-footer-actions">
          <Button onClick={save} busy={saving}><FloppyDisk size={17} aria-hidden="true" />保存配置</Button>
          <div className="group-execute-controls">
            <label htmlFor="execution-date">执行日期</label>
            <input id="execution-date" type="date" value={executionDate} onChange={(event) => setExecutionDate(event.target.value || todayLocal())} />
            <Button tone="secondary" onClick={execute} busy={executing} disabled={!groupId} title={!groupId ? "请先保存群配置" : "调用后端执行一次生成任务"}>
              <Play size={17} aria-hidden="true" />立即执行一次
            </Button>
          </div>
        </div>
        {execution && <div className={`group-execution-result ${execution.tone}`}><StatusBadge tone={execution.tone}>{execution.title}</StatusBadge><span>{execution.detail}</span><Check size={17} aria-hidden="true" /></div>}
      </section>

      <Toast message={msg} />
    </div>
  );
}
