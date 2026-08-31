import {
  ArrowCounterClockwise,
  CaretDown,
  CaretUp,
  Copy,
  FloppyDisk,
  ImageSquare,
  PaperPlaneTilt,
  Play,
  Sparkle,
  WarningCircle,
} from "@phosphor-icons/react";
import { useEffect, useId, useState } from "react";

import type { ImageThemeOption } from "../../../api";
import {
  Button,
  ConfirmDialog,
  EmptyState,
  ImagePreviewTrigger,
  ImageViewer,
  LoadingState,
  StatusBadge,
} from "../../../components/common";
import { ImageThemePicker } from "../../../components/ImageThemePicker";
import { copyText } from "../../../components/ui";
import { ContentSwap } from "../../../components/motion";
import { formatDateTime, REGEN_LABELS, runKey, STATUS_LABELS, StatusPill } from "./model";
import type { ToastFn } from "./useAIImageCatalogs";
import type { AIImageRunsModel } from "./useAIImageRuns";

interface AIImageRunWorkspaceProps {
  model: AIImageRunsModel;
  themes: ImageThemeOption[];
  catalogLoading: boolean;
  themesError: string;
  toast: ToastFn;
}

export function AIImageRunWorkspace({
  model,
  themes,
  catalogLoading,
  themesError,
  toast,
}: AIImageRunWorkspaceProps) {
  const {
    runs,
    dateFilter,
    setDateFilter,
    groupFilter,
    setGroupFilter,
    statusFilter,
    setStatusFilter,
    selectedKey,
    setSelectedKey,
    detail,
    runPrompt,
    runDraft,
    setRunDraft,
    runTheme,
    runCustom,
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
    applyRunTheme,
    saveCurrentPrompt,
    restoreCurrentPrompt,
    rebuildCurrentPrompt,
    regenerate,
    claimCandidate,
    confirmSend,
  } = model;
  const [topicsExpanded, setTopicsExpanded] = useState(false);
  const topicScoreListId = useId();
  const topicSelection = runPrompt?.topic_selection;
  const topicCandidates = topicSelection?.candidates || [];
  const hiddenTopicCount = Math.max(0, topicCandidates.length - 2);
  const visibleTopicCandidates = topicsExpanded ? topicCandidates : topicCandidates.slice(0, 2);

  useEffect(() => {
    setTopicsExpanded(false);
  }, [selectedKey]);

  return (
    <>
      <section className="ai-images-filter-bar" aria-label="运行记录筛选">
        <label><span>运行日期</span><input type="date" value={dateFilter} onChange={(event) => setDateFilter(event.target.value)} /></label>
        <label><span>群名</span><input type="search" value={groupFilter} placeholder="搜索群名" onChange={(event) => setGroupFilter(event.target.value)} /></label>
        <label><span>状态</span><select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}><option value="all">全部状态</option>{Object.entries(STATUS_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <span className="ai-images-filter-count">显示 {filteredRuns.length} / {runs.length} 条</span>
      </section>

      <div className="ai-images-workspace">
        <section className="ai-images-run-list">
          <div className="ai-images-section-head"><div><h2>运行记录</h2><p>选择群和日期编辑当次内容。</p></div><ImageSquare size={22} /></div>
          {!filteredRuns.length ? <EmptyState title="没有匹配记录" description="请调整筛选条件，或先运行一次日报。" /> : <div className="ai-images-run-items">{filteredRuns.map((run) => <button type="button" key={runKey(run)} className={`ai-images-run-item ${selectedKey === runKey(run) ? "is-active" : ""}`} onClick={() => setSelectedKey(runKey(run))}><div><strong>{run.group_name}</strong><span>{run.run_date} · {formatDateTime(run.updated_at)}</span></div><StatusPill status={run.status} /></button>)}</div>}
        </section>

        <section className="ai-images-detail-panel">
          <ContentSwap swapKey={detailLoading ? "loading" : detail ? selectedKey : "empty"}>
            {detailLoading ? <LoadingState label="正在读取真实 Prompt 与图片…" /> : detailError && !detail ? <EmptyState title="运行详情加载失败" description={detailError} action={<Button tone="secondary" onClick={() => setDetailReloadVersion((current) => current + 1)}>重试</Button>} /> : !detail ? <EmptyState title="请选择运行记录" description="从左侧选择一条记录。" /> : (
            <>
              <div className="ai-images-detail-head"><div><span className="ai-images-eyebrow">当天真实运行</span><h2>{detail.run.group_name} · {detail.run.run_date}</h2><p><StatusPill status={detail.run.status} /> · 更新 {formatDateTime(detail.run.updated_at)}</p></div></div>
              <div className={`ai-images-regen-state ${regenStatus}`}><strong>{REGEN_LABELS[regenStatus] || regenStatus}</strong><span>{String(detail.run.image_regen_error || detail.run.image_regen_detail || "messages.json 已按运行日期保存；重建 Prompt 和重新生图都不会再次读取微信，也不会自动发送。")}</span></div>
              {regenPollError && <div className="ai-images-run-error" role="alert">{regenPollError}</div>}
              {topicSelection && <section className="ai-images-topic-score-card" aria-label="选题评分">
                <div className="ai-images-content-heading"><div><h3>选题评分</h3><span>候选 {topicSelection.candidate_count} · 入选 {topicSelection.selected_count}</span></div><div className="ai-images-topic-score-heading-actions"><span>v{topicSelection.topic_selection_version}</span>{hiddenTopicCount > 0 && <button className="ai-images-topic-score-toggle" type="button" aria-expanded={topicsExpanded} aria-controls={topicScoreListId} onClick={() => setTopicsExpanded((current) => !current)}>{topicsExpanded ? <CaretUp size={15} aria-hidden="true" /> : <CaretDown size={15} aria-hidden="true" />}{topicsExpanded ? "收起至 2 个" : `展开其余 ${hiddenTopicCount} 个`}</button>}</div></div>
                <div className="ai-images-topic-score-list" id={topicScoreListId}>{visibleTopicCandidates.map((topic) => <article className={`ai-images-topic-score-item ${topic.selected ? "is-selected" : ""}`} key={topic.topic_id}>
                  <div className="ai-images-topic-score-title"><span>#{topic.rank}</span><strong>{topic.title}</strong><StatusBadge tone={topic.selected ? "success" : "neutral"}>{topic.selected ? "已入选" : "候选"}</StatusBadge><b>{topic.scores.total.toFixed(1)}</b></div>
                  <p>{topic.summary}</p>
                  <div className="ai-images-topic-score-grid"><span>讨论 {topic.scores.discussion}</span><span>参与 {topic.scores.participation}</span><span>有趣 {topic.scores.comedy}</span><span>群内感 {topic.scores.group_recognition}</span><span>画面 {topic.scores.visual}</span><span>持续 {topic.scores.continuity}</span></div>
                  <small>{topic.evidence_message_count} 条证据 · {topic.participant_count} 人 · {topic.duration_minutes} 分钟{topic.score_reason ? ` · ${topic.score_reason}` : ""}</small>
                </article>)}</div>
              </section>}
              {runPrompt && <div className="ai-images-run-theme-row">
                <ImageThemePicker themes={themes} value={runTheme} customValue={runCustom} onConfirm={(key, custom) => applyRunTheme(key, custom)} label="替换当天大主题" loading={catalogLoading} error={themesError} disabled={runSaving} />
              </div>}
              <div className="ai-images-asset-grid">
                <div className="ai-images-preview-card"><div className="ai-images-content-heading"><h3>日报图片</h3><span>daily_image.png</span></div>{detail.files.includes("daily_image.png") && !imageLoadError ? <ImagePreviewTrigger src={currentImageSrc} alt="真实日报图片" imageClassName="ai-images-real-image" className="ai-images-real-image-trigger" onError={() => { setImageLoadError(true); setImageViewerOpen(false); }} onOpen={() => setImageViewerOpen(true)} /> : <EmptyState title="尚无可读图片" description="重新生图失败时会保留旧图；没有旧图时这里保持为空。" />}</div>
                {runPrompt ? <div className="ai-images-prompt-card ai-images-run-editor"><div className="ai-images-content-heading"><h3>当天生图 Prompt</h3><Button tone="ghost" className="ui-button-compact" onClick={() => copyText(runDraft, toast)} disabled={!runDraft}><Copy size={16} />复制</Button></div><textarea value={runDraft} onChange={(event) => setRunDraft(event.target.value)} /><div className="ai-images-run-actions"><Button tone="ghost" onClick={restoreCurrentPrompt} busy={restoring} disabled={!runPrompt.has_original}><ArrowCounterClockwise size={16} />恢复最初版本</Button><Button tone="secondary" onClick={saveCurrentPrompt} busy={runSaving} disabled={!runDirty}><FloppyDisk size={16} />保存 Prompt</Button><Button tone="secondary" onClick={rebuildCurrentPrompt} busy={rebuildingPrompt} disabled={runDirty || ["queued", "running"].includes(regenStatus)}><Sparkle size={16} />复用已校验选题重建 Prompt</Button><Button tone="primary" onClick={regenerate} busy={regenerating} disabled={runDirty || rebuildingPrompt || ["queued", "running"].includes(regenStatus)}><Play size={16} />按现有 Prompt 重画</Button></div></div> : <div className="ai-images-prompt-card"><EmptyState title="当天 Prompt 加载失败" description={runPromptError || "当天 Prompt 暂不可用；日报图片和运行状态仍可查看。"} action={<Button tone="secondary" onClick={() => setDetailReloadVersion((current) => current + 1)}>重新读取 Prompt</Button>} /></div>}
              </div>
              {["ambiguous_result", "result_unknown"].includes(regenStatus) && <section className="ai-images-candidate-card" aria-label="生图候选人工认领">
                <div className="ai-images-content-heading"><div><h3>本次任务候选图片</h3><span>只显示当前 job_id、群和日期都匹配的候选</span></div></div>
                {candidateLoading ? <LoadingState label="正在核对候选哈希…" /> : !imageCandidates.length ? <EmptyState title="没有可认领候选" description="系统已保留旧图，也不会自动重复提交结果未知的生图调用。" /> : <div className="ai-images-candidate-grid">{imageCandidates.map((candidate) => <article key={candidate.candidate_id}>
                  <img src={candidate.preview_url} alt={`${candidate.group_name} ${candidate.run_date} 候选图`} />
                  <strong>{candidate.group_name} · {candidate.run_date}</strong>
                  <small>任务 {candidate.job_id.slice(0, 12)} · 哈希 {candidate.sha256.slice(0, 12)}</small>
                  <Button tone="secondary" busy={candidateClaiming === candidate.candidate_id} disabled={Boolean(candidateClaiming)} onClick={() => { void claimCandidate(candidate); }}>确认归属并替换</Button>
                </article>)}</div>}
              </section>}
              {regenStatus === "ready_for_review" && <div className="ai-images-review-actions"><WarningCircle size={18} /><span>请先检查新图。只有再次确认后才会发送文字和图片。</span><Button tone="primary" onClick={() => setSendConfirmOpen(true)}><PaperPlaneTilt size={17} />发送 / 重新发送</Button></div>}
              {detail.run.error && <div className="ai-images-run-error">主任务错误：{String(detail.run.error)}</div>}
            </>
            )}
          </ContentSwap>
        </section>
      </div>

      <ImageViewer
        open={imageViewerOpen && Boolean(detail)}
        src={currentImageSrc}
        alt="真实日报图片"
        filename="daily_image.png"
        title={detail ? `${detail.run.group_name} · ${detail.run.run_date} · 日报图片` : "日报图片"}
        onClose={() => setImageViewerOpen(false)}
        onDownloadError={toast}
      />

      <ConfirmDialog open={sendConfirmOpen} title="确认发送这张新图？" description="这会立即操作本机微信，向该运行绑定的发送目标粘贴并发送文字与图片。请确认预览、群名和日期都正确。" confirmLabel="确认发送" busy={sending} onCancel={() => setSendConfirmOpen(false)} onConfirm={confirmSend} />
    </>
  );
}
