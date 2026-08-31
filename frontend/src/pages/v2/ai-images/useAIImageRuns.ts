import { useEffect, useMemo, useState } from "react";

import {
  claimRunImageCandidate,
  getRunImageCandidates,
  getRunDetail,
  getRunPrompt,
  getRuns,
  getV2File,
  GroupV2,
  ImageCandidate,
  pipelineSend,
  rebuildRunPrompt,
  regenerateRunImage,
  resolveImageTheme,
  restoreRunPrompt,
  RunPromptConfig,
  saveRunPrompt,
  V2Run,
} from "../../../api";
import { shanghaiDateInputValue } from "../../../date";
import {
  describeLoadError,
  ImageDetail,
  regenerationPollDelay,
  runKey,
} from "./model";
import type { ToastFn } from "./useAIImageCatalogs";

export function useAIImageRuns(groups: GroupV2[], toast: ToastFn) {
  const [runs, setRuns] = useState<V2Run[]>([]);
  const [dateFilter, setDateFilter] = useState(shanghaiDateInputValue);
  const [groupFilter, setGroupFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [selectedKey, setSelectedKey] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [detail, setDetail] = useState<ImageDetail | null>(null);
  const [runPrompt, setRunPrompt] = useState<RunPromptConfig | null>(null);
  const [runDraft, setRunDraft] = useState("");
  const [runTheme, setRunTheme] = useState("ai_free");
  const [runCustom, setRunCustom] = useState("");
  const [detailLoading, setDetailLoading] = useState(false);
  const [runSaving, setRunSaving] = useState(false);
  const [rebuildingPrompt, setRebuildingPrompt] = useState(false);
  const [regenerating, setRegenerating] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [sending, setSending] = useState(false);
  const [sendConfirmOpen, setSendConfirmOpen] = useState(false);
  const [imageLoadError, setImageLoadError] = useState(false);
  const [imageViewerOpen, setImageViewerOpen] = useState(false);
  const [imageVersion, setImageVersion] = useState(0);
  const [detailError, setDetailError] = useState("");
  const [runPromptError, setRunPromptError] = useState("");
  const [regenPollError, setRegenPollError] = useState("");
  const [detailReloadVersion, setDetailReloadVersion] = useState(0);
  const [imageCandidates, setImageCandidates] = useState<ImageCandidate[]>([]);
  const [candidateLoading, setCandidateLoading] = useState(false);
  const [candidateClaiming, setCandidateClaiming] = useState("");

  const loadRuns = () => {
    setLoading(true);
    setError("");
    getRuns(dateFilter || undefined)
      .then((data) => {
        setRuns(data.runs);
        setSelectedKey((current) => data.runs.some((run) => runKey(run) === current)
          ? current
          : data.runs[0] ? runKey(data.runs[0]) : "");
      })
      .catch((reason: unknown) => {
        const message = `图片运行记录加载失败：${String(reason)}`;
        setError(message);
        toast(message);
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    loadRuns();
    // loadRuns intentionally follows the selected date and the current toast handler.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dateFilter, toast]);

  const filteredRuns = useMemo(() => runs.filter((run) => {
    const query = groupFilter.trim().toLocaleLowerCase();
    return (!query || run.group_name.toLocaleLowerCase().includes(query))
      && (statusFilter === "all" || run.status.toUpperCase() === statusFilter);
  }), [groupFilter, runs, statusFilter]);

  useEffect(() => {
    if (!filteredRuns.length) {
      setSelectedKey("");
    } else if (!filteredRuns.some((run) => runKey(run) === selectedKey)) {
      setSelectedKey(runKey(filteredRuns[0]));
    }
  }, [filteredRuns, selectedKey]);

  useEffect(() => {
    if (!selectedKey) {
      setDetail(null);
      setRunPrompt(null);
      setDetailError("");
      setRunPromptError("");
      return;
    }
    const selected = runs.find((run) => runKey(run) === selectedKey);
    if (!selected) return;
    let cancelled = false;
    setDetailLoading(true);
    setDetail(null);
    setRunPrompt(null);
    setDetailError("");
    setRunPromptError("");
    setImageLoadError(false);
    setImageViewerOpen(false);
    Promise.allSettled([
      getRunDetail(selected.group_name, selected.run_date),
      getRunPrompt(selected.group_name, selected.run_date),
    ]).then(([detailResult, promptResult]) => {
      if (cancelled) return;
      if (detailResult.status === "fulfilled") {
        setDetail(detailResult.value);
      } else {
        const message = describeLoadError("运行详情", detailResult.reason);
        setDetailError(message);
        toast(message);
      }
      if (promptResult.status === "fulfilled") {
        const prompt = promptResult.value;
        setRunPrompt(prompt);
        setRunDraft(prompt.content);
        setRunTheme(prompt.image_theme || "ai_free");
        setRunCustom(prompt.image_theme_custom || "");
      } else {
        const message = describeLoadError("当天 Prompt", promptResult.reason);
        setRunPromptError(message);
        toast(message);
      }
    }).finally(() => {
      if (!cancelled) setDetailLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [detailReloadVersion, runs, selectedKey, toast]);

  const persistedRegenStatus = String(detail?.run.image_regen_status || "idle");
  const imageJobStatus = String(
    (detail?.run.image_job as { status?: unknown } | undefined)?.status || "",
  );
  const regenStatus = ["", "idle"].includes(persistedRegenStatus)
    && ["ambiguous_result", "result_unknown"].includes(imageJobStatus)
    ? imageJobStatus
    : persistedRegenStatus;
  const currentImageSrc = detail
    ? `${getV2File(detail.run.group_name, detail.run.run_date, "daily_image.png")}?v=${imageVersion}`
    : "";

  useEffect(() => {
    if (!detail || !["ambiguous_result", "result_unknown"].includes(regenStatus)) {
      setImageCandidates([]);
      return;
    }
    let cancelled = false;
    setCandidateLoading(true);
    getRunImageCandidates(detail.run.group_name, detail.run.run_date)
      .then((result) => {
        if (!cancelled) setImageCandidates(result.candidates);
      })
      .catch((reason: unknown) => {
        if (!cancelled) toast(`候选图片加载失败：${String(reason)}`);
      })
      .finally(() => {
        if (!cancelled) setCandidateLoading(false);
      });
    return () => { cancelled = true; };
  }, [detail?.run.group_name, detail?.run.run_date, regenStatus, toast]);

  useEffect(() => {
    if (!detail || !["queued", "running", "fallback_queued"].includes(regenStatus)) {
      setRegenPollError("");
      return;
    }
    const groupName = detail.run.group_name;
    const runDate = detail.run.run_date;
    let cancelled = false;
    let timer: number | undefined;
    let consecutiveFailures = 0;

    const schedule = (delay: number) => {
      timer = window.setTimeout(poll, delay);
    };
    const poll = () => {
      let continuePolling = true;
      let nextDelay = regenerationPollDelay(regenStatus, consecutiveFailures);
      getRunDetail(groupName, runDate)
        .then((next) => {
          if (cancelled) return;
          consecutiveFailures = 0;
          setRegenPollError("");
          setDetail(next);
          const nextStatus = String(next.run.image_regen_status || "idle");
          continuePolling = ["queued", "running", "fallback_queued"].includes(nextStatus);
          nextDelay = regenerationPollDelay(nextStatus, 0);
          if (["ready_for_review", "failed", "ambiguous_result", "result_unknown"].includes(nextStatus)) {
            setImageVersion((current) => current + 1);
            loadRuns();
          }
        })
        .catch((reason: unknown) => {
          if (cancelled) return;
          consecutiveFailures += 1;
          nextDelay = regenerationPollDelay(regenStatus, consecutiveFailures);
          setRegenPollError(
            `重新生图状态刷新失败，将在 ${Math.round(nextDelay / 1000)} 秒后重试：${String(reason)}`,
          );
        })
        .finally(() => {
          if (!cancelled && continuePolling) schedule(nextDelay);
        });
    };

    schedule(regenerationPollDelay(regenStatus, 0));
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
    // Polling is keyed by the persisted run identity and regeneration state.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [detail?.run.group_name, detail?.run.run_date, regenStatus]);

  const runDirty = Boolean(runPrompt) && runDraft !== runPrompt?.content;

  const applyRunTheme = async (key: string, custom = runCustom) => {
    setRunTheme(key);
    setRunCustom(key === "custom" ? custom : "");
    if (key === "custom" && !custom.trim()) return;
    try {
      const resolved = await resolveImageTheme({
        image_theme: key,
        image_theme_custom: key === "custom" ? custom : "",
        prompt: runDraft,
        group_id: typeof detail?.run.group_id === "number" || typeof detail?.run.group_id === "string"
          ? detail.run.group_id
          : undefined,
        run_date: detail?.run.run_date,
      });
      setRunTheme(key);
      setRunDraft(resolved.prompt);
      if (key === "random_preset") toast(`该群当天随机风格已固定为：${resolved.display_name}`);
      else if (key !== "custom") toast(`当天风格已替换为：${resolved.display_name}`);
    } catch (reason) {
      toast(`当天主题替换失败：${String(reason)}`);
    }
  };

  const saveCurrentPrompt = async () => {
    if (!detail || !runPrompt) return;
    setRunSaving(true);
    try {
      const saved = await saveRunPrompt(detail.run.group_name, detail.run.run_date, {
        content: runDraft,
        expected_revision: runPrompt.revision,
        image_theme: runTheme,
        image_theme_custom: runTheme === "custom" ? runCustom.trim() : "",
      });
      setRunPrompt(saved);
      setRunDraft(saved.content);
      toast("当天 Prompt 已保存；尚未重新生图，也不会自动发送");
    } catch (reason) {
      toast(`当天 Prompt 保存失败：${String(reason)}`);
    } finally {
      setRunSaving(false);
    }
  };

  const restoreCurrentPrompt = async () => {
    if (!detail) return;
    setRestoring(true);
    try {
      const restored = await restoreRunPrompt(detail.run.group_name, detail.run.run_date);
      setRunPrompt(restored);
      setRunDraft(restored.content);
      setRunTheme(restored.image_theme || "ai_free");
      setRunCustom(restored.image_theme_custom || "");
      toast("已恢复首次编辑前的 Prompt");
    } catch (reason) {
      toast(`恢复 Prompt 失败：${String(reason)}`);
    } finally {
      setRestoring(false);
    }
  };

  const rebuildCurrentPrompt = async () => {
    if (!detail) return;
    if (runDirty) {
      toast("当前 Prompt 有未保存修改，请先保存或恢复后再重建");
      return;
    }
    setRebuildingPrompt(true);
    try {
      const rebuilt = await rebuildRunPrompt(detail.run.group_name, detail.run.run_date);
      const prompt = await getRunPrompt(detail.run.group_name, detail.run.run_date);
      setDetail((current) => current ? { ...current, run: rebuilt.run } : current);
      setRunPrompt(prompt);
      setRunDraft(prompt.content);
      setRunTheme(prompt.image_theme || "ai_free");
      setRunCustom(prompt.image_theme_custom || "");
      loadRuns();
      toast("已复用当天已校验选题和既定分镜重建 Prompt；没有重新读取微信，也没有生图");
    } catch (reason) {
      toast(`Prompt 重建失败：${String(reason)}`);
    } finally {
      setRebuildingPrompt(false);
    }
  };

  const regenerate = async () => {
    if (!detail || !runPrompt) return;
    if (runDirty) {
      toast("请先保存当天 Prompt，再重新生图");
      return;
    }
    setRegenerating(true);
    try {
      const accepted = await regenerateRunImage(detail.run.group_name, detail.run.run_date);
      setDetail((current) => current ? { ...current, run: accepted.run } : current);
      toast("已加入 2 路受控队列；生成成功后会停在人工审核状态");
    } catch (reason) {
      toast(`重新生图请求失败：${String(reason)}`);
    } finally {
      setRegenerating(false);
    }
  };

  const claimCandidate = async (candidate: ImageCandidate) => {
    if (!detail) return;
    setCandidateClaiming(candidate.candidate_id);
    try {
      const claimed = await claimRunImageCandidate(
        detail.run.group_name,
        detail.run.run_date,
        { job_id: candidate.job_id, candidate_id: candidate.candidate_id },
      );
      setDetail((current) => current ? { ...current, run: claimed.run } : current);
      setImageCandidates([]);
      setImageVersion((current) => current + 1);
      loadRuns();
      toast("候选图片已按 job_id 和哈希认领，旧图已备份；仍需人工审核，不会自动发送");
    } catch (reason) {
      toast(`候选图片认领失败：${String(reason)}`);
    } finally {
      setCandidateClaiming("");
    }
  };

  const confirmSend = async () => {
    if (!detail) return;
    const groupId = Number(detail.run.group_id || groups.find((group) => group.display_name === detail.run.group_name || group.wechat_group_name === detail.run.group_name)?.id || 0);
    if (!groupId) {
      toast("运行记录缺少可用群 ID，无法发送");
      return;
    }
    setSending(true);
    try {
      const result = await pipelineSend({ group_id: groupId, run_date: detail.run.run_date, confirm_regenerated: true });
      if (result.result.status !== "sent") throw new Error(String(result.result.error || result.result.detail || "发送未成功"));
      setSendConfirmOpen(false);
      toast("已确认并发送文字与图片");
      loadRuns();
    } catch (reason) {
      toast(`发送失败：${String(reason)}`);
    } finally {
      setSending(false);
    }
  };

  return {
    runs,
    dateFilter,
    setDateFilter,
    groupFilter,
    setGroupFilter,
    statusFilter,
    setStatusFilter,
    selectedKey,
    setSelectedKey,
    loading,
    error,
    detail,
    runPrompt,
    runDraft,
    setRunDraft,
    runTheme,
    runCustom,
    setRunCustom,
    detailLoading,
    runSaving,
    rebuildingPrompt,
    regenerating,
    restoring,
    sending,
    sendConfirmOpen,
    setSendConfirmOpen,
    imageLoadError,
    setImageLoadError,
    imageViewerOpen,
    setImageViewerOpen,
    detailError,
    runPromptError,
    regenPollError,
    imageCandidates,
    candidateLoading,
    candidateClaiming,
    setDetailReloadVersion,
    filteredRuns,
    regenStatus,
    currentImageSrc,
    runDirty,
    loadRuns,
    applyRunTheme,
    saveCurrentPrompt,
    restoreCurrentPrompt,
    rebuildCurrentPrompt,
    regenerate,
    claimCandidate,
    confirmSend,
  };
}

export type AIImageRunsModel = ReturnType<typeof useAIImageRuns>;
