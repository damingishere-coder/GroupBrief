import { useEffect, useState } from "react";
import {
  ArrowSquareOut,
  ArrowsClockwise,
  ImageSquare,
  Trophy,
  X,
} from "@phosphor-icons/react";
import {
  getDashboard,
  getSystemHealth,
  readV2JsonFile,
  readV2TextFile,
} from "../../api";
import {
  Button,
  EmptyState,
  ImagePreviewTrigger,
  ImageViewer,
  LoadingState,
  Toast,
} from "../../components/common";
import { useFetch, useToast } from "../../components/ui";
import { useUnsavedChanges } from "../../components/useUnsavedChanges";
import {
  navigateToHash,
  updateWorkspaceQuery,
  useWorkspaceQuery,
} from "../../navigation";
import { AIImageRunWorkspace } from "./ai-images/AIImageRunWorkspace";
import { useAIImageRuns } from "./ai-images/useAIImageRuns";
import { useAIImageCatalogs } from "./ai-images/useAIImageCatalogs";
import { ReportActions } from "./ReportActions";
import { StatusPill, imageDeliveryAllowed } from "./ai-images/model";
import {
  formatRankingCount,
  INTERACTION_EXPLANATION,
  isTextPrimaryRanking,
  parseRanking,
  type ParsedRankingSummary,
} from "./rankingPolicy";

export interface ReportTarget {
  groupName: string;
  runDate: string;
}

/** Shared by today's desk and the archive gallery. Parent keys by immutable run identity. */
export default function ReportWorkspace({
  target,
  onUpdated,
  closable = true,
}: {
  target: ReportTarget;
  onUpdated?: () => void;
  closable?: boolean;
}) {
  const { msg, toast } = useToast();
  const catalogs = useAIImageCatalogs(toast);
  const model = useAIImageRuns(catalogs.groups, toast, target);
  const health = useFetch(getSystemHealth);
  const dashboard = useFetch(
    () => getDashboard(target.runDate),
    [target.runDate],
  );
  const card = dashboard.data?.cards.find(
    (item) => item.group_name === target.groupName,
  );
  const [actionBusy, setActionBusy] = useState(false);
  const query = useWorkspaceQuery();
  const panel = query.get("panel") || "preview";
  const [ranking, setRanking] = useState<ParsedRankingSummary | null>(null);
  const [rankingText, setRankingText] = useState("");
  const [rankingError, setRankingError] = useState("");
  const [rankingLoading, setRankingLoading] = useState(true);
  const [viewer, setViewer] = useState(false);
  const mutating =
    actionBusy ||
    model.runSaving ||
    model.restoring ||
    model.rebuildingPrompt ||
    model.regenerating ||
    model.sending ||
    Boolean(model.candidateClaiming);
  useUnsavedChanges(model.runDirty, mutating || model.sendConfirmOpen);
  useEffect(() => {
    let active = true;
    setRankingLoading(true);
    setRanking(null);
    setRankingText("");
    setRankingError("");
    Promise.allSettled([
      readV2JsonFile(target.groupName, target.runDate, "ranking.json"),
      readV2TextFile(target.groupName, target.runDate, "ranking.txt"),
    ]).then(([json, text]) => {
      if (!active) return;
      if (json.status === "fulfilled") {
        const parsed = parseRanking(json.value);
        setRanking(parsed.summary);
        setRankingError(parsed.error);
      } else setRankingError("排行榜暂不可用，可在运行详情中检查生成状态。");
      if (text.status === "fulfilled") setRankingText(text.value);
      setRankingLoading(false);
    });
    return () => {
      active = false;
    };
  }, [target.groupName, target.runDate, model.detail?.run.updated_at]);
  useEffect(() => {
    dashboard.reload();
    onUpdated?.();
  }, [model.detail?.run.updated_at, dashboard.reload, onUpdated]);
  const run = model.detail?.run;
  const group = catalogs.groups.find(
    (item) => String(item.id) === String(run?.group_id),
  );
  const sendAllowed = Boolean(
    health.data?.checks.wechat_sender?.ok &&
      group?.enabled &&
      group?.wechat_send_enabled &&
      imageDeliveryAllowed(run) &&
      run?.send_hold_reason !== "SEND_RESULT_UNKNOWN" &&
      run?.send_state !== "unknown",
  );
  return (
    <section className="report-workspace" aria-label="日报处理工作区">
      <header className="report-workspace-head">
        <div>
          <span className="studio-eyebrow">REPORT WORKSPACE / 日报处理</span>
          <h2>{target.groupName}</h2>
          <p>
            {target.runDate} ·{" "}
            {run ? <StatusPill status={run.status} /> : "正在读取"}
            {model.runDirty && <span className="draft-indicator">未保存</span>}
          </p>
        </div>
        {closable && <Button
          tone="ghost"
          aria-label="关闭日报工作区"
          disabled={mutating}
          onClick={() =>
            updateWorkspaceQuery({
              group: null,
              panel: null,
              ...(query.has("collectionDate")
                ? { date: query.get("collectionDate"), collectionDate: null }
                : {}),
            })
          }
        >
          <X size={21} />
        </Button>}
      </header>
      <div className="report-tabs" role="tablist" aria-label="日报内容">
        {[
          ["preview", "日报预览"],
          ["edit", "图片与提示词"],
          ["run", "运行详情"],
        ].map(([key, label]) => (
          <button
            type="button"
            key={key}
            role="tab"
            aria-selected={panel === key}
            className={panel === key ? "active" : ""}
            onClick={() => updateWorkspaceQuery({ panel: key })}
          >
            {label}
          </button>
        ))}
      </div>
      <div className="report-workspace-body">
        {model.error && (
          <div className="studio-alert" role="alert">
            {model.error}
            <Button tone="ghost" onClick={model.loadRuns}>
              重试
            </Button>
          </div>
        )}
        {panel === "preview" && (
          <div className="report-preview" role="tabpanel" aria-label="日报预览">
            <div className="report-paper">
              <div className="report-section-title">
                <ImageSquare size={19} />
                <h3>今日的精彩，凝成一张图</h3>
              </div>
              {model.detailLoading ? (
                <LoadingState />
              ) : model.detail?.files.includes("daily_image.png") &&
                !model.imageLoadError ? (
                <>
                  <ImagePreviewTrigger
                    src={model.currentImageSrc}
                    alt={`${target.groupName} 日报图片`}
                    imageClassName="report-main-image"
                    onError={() => model.setImageLoadError(true)}
                    onOpen={() => setViewer(true)}
                  />
                  <p className="report-image-hint">
                    点击图片查看原图 ·{" "}
                    {run?.image_delivery_eligible === false
                      ? "此图不可发送，请检查运行详情"
                      : "请核对图片内容后再发送"}
                  </p>
                </>
              ) : (
                <EmptyState
                  title={
                    model.imageLoadError ? "图片读取失败" : "日报图片还未就绪"
                  }
                  description="可以先查看排行榜，或打开图片与提示词处理。"
                />
              )}
            </div>
            <div className="report-ranking">
              <div className="report-section-title">
                <Trophy size={20} weight="fill" />
                <h3>群聊活跃榜</h3>
              </div>
              {rankingLoading ? (
                <LoadingState />
              ) : ranking ? (
                <>
                  <div className="report-ranking-summary">
                    <strong>
                      {ranking.messageCount ?? "—"}
                      <small>条消息</small>
                    </strong>
                    <strong>
                      {ranking.speakerCount ?? "—"}
                      <small>位参与者</small>
                    </strong>
                  </div>
                  <ol>
                    {ranking.topSpeakers.map((speaker, index) => (
                      <li key={`${speaker.rank}-${index}`}>
                        <span>{String(speaker.rank).padStart(2, "0")}</span>
                        <strong>{speaker.name}</strong>
                        <small>
                          {formatRankingCount(ranking.countPolicy, {
                            count: speaker.count,
                            text_count: speaker.textCount,
                            interaction_count: speaker.interactionCount,
                          })}
                        </small>
                      </li>
                    ))}
                  </ol>
                  {isTextPrimaryRanking(ranking.countPolicy) && (
                    <p>{INTERACTION_EXPLANATION}</p>
                  )}
                </>
              ) : (
                <EmptyState title="暂未生成排行榜" />
              )}
              {rankingError && <p role="status">{rankingError}</p>}
              {rankingText && (
                <details>
                  <summary>查看完整发送文字</summary>
                  <pre>{rankingText}</pre>
                </details>
              )}
            </div>
          </div>
        )}
        <div
          hidden={panel !== "edit"}
          role="tabpanel"
          aria-label="图片与提示词"
        >
          <AIImageRunWorkspace
            embedded
            model={model}
            themes={catalogs.themes}
            catalogLoading={catalogs.catalogLoading}
            themesError={catalogs.themesError}
            toast={toast}
            sendAllowed={sendAllowed}
          />
        </div>
        {panel === "run" && (
          <div
            className="report-run-details"
            role="tabpanel"
            aria-label="运行详情"
          >
            {model.detailLoading ? (
              <LoadingState />
            ) : !run ? (
              <EmptyState
                title="暂无运行详情"
                description={model.detailError}
                action={<Button onClick={model.loadRuns}>重新读取</Button>}
              />
            ) : (
              <>
                <div className="report-section-title">
                  <h3>本次日报进度</h3>
                  <StatusPill status={run.status} />
                </div>
                <dl>
                  {[
                    ["运行日期", target.runDate],
                    [
                      "消息统计周期",
                      `${run.period_start || "—"} → ${run.period_end || "—"}`,
                    ],
                    ["更新时间", String(run.updated_at || "—")],
                    ["图片状态", model.regenStatus],
                    ["发送状态", String(run.send_state || "尚无记录")],
                    ["发送暂停原因", String(run.send_hold_reason || "无")],
                  ].map(([name, value]) => (
                    <div key={name}>
                      <dt>{name}</dt>
                      <dd>{value}</dd>
                    </div>
                  ))}
                </dl>
                {Boolean(run.error || run.send_error) && (
                  <div className="studio-alert" role="alert">
                    {String(run.error || run.send_error)}
                  </div>
                )}
                <details>
                  <summary>输出文件与诊断数据</summary>
                  <ul>
                    {model.detail?.files.map((file) => (
                      <li key={file}>{file}</li>
                    ))}
                  </ul>
                  <pre>{JSON.stringify(run, null, 2)}</pre>
                </details>
              </>
            )}
            <Button
              tone="secondary"
              onClick={() =>
                navigateToHash(
                  `tasks?${new URLSearchParams({ date: target.runDate, group: target.groupName })}`,
                )
              }
            >
              <ArrowSquareOut size={17} />
              打开任务与恢复
            </Button>
          </div>
        )}
      </div>
      <footer className="report-workspace-footer">
        <span>
          {mutating
            ? "正在处理，请稍候…"
            : model.runDirty
              ? "提示词有未保存修改"
              : "核对群名、日期与内容后再操作"}
        </span>
        {card ? (
          <ReportActions
            card={card}
            runDate={target.runDate}
            senderOk={Boolean(health.data?.checks.wechat_sender?.ok)}
            senderDetail={health.error || "发送服务尚未就绪"}
            disabled={
              model.runDirty ||
              model.runSaving ||
              model.detailLoading ||
              model.rebuildingPrompt ||
              model.regenerating ||
              model.restoring ||
              model.sending
            }
            onBusyChange={setActionBusy}
            onUpdated={() => {
              model.loadRuns();
              dashboard.reload();
              onUpdated?.();
            }}
          />
        ) : (
          <Button
            tone="secondary"
            disabled={mutating || model.runDirty}
            onClick={model.loadRuns}
          >
            <ArrowsClockwise size={16} />
            刷新状态
          </Button>
        )}
      </footer>
      <ImageViewer
        open={viewer}
        src={model.currentImageSrc}
        alt={`${target.groupName} 日报图片`}
        title={`${target.groupName} · ${target.runDate}`}
        filename="daily_image.png"
        onClose={() => setViewer(false)}
      />
      <Toast message={msg} />
    </section>
  );
}
