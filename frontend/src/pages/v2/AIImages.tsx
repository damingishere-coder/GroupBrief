import { useReportScroll } from "../../components/useReportScroll";
import { useEffect, useState } from "react";
import {
  ArrowUpRight,
  ArrowsClockwise,
  ImageSquare,
  MagnifyingGlass,
  Palette,
  SquaresFour,
  TextT,
} from "@phosphor-icons/react";
import { getRuns, getV2File, type V2Run } from "../../api";
import {
  Button,
  EmptyState,
  LoadingState,
  PageHeader,
  Toast,
} from "../../components/common";
import { useFetch, useToast } from "../../components/ui";
import { shanghaiDateInputValue } from "../../date";
import { updateWorkspaceQuery, useWorkspaceQuery } from "../../navigation";
import { ImageStylePanel } from "./ai-images/ImageStylePanel";
import { useAIImageCatalogs } from "./ai-images/useAIImageCatalogs";
import { StatusPill } from "./ai-images/model";
import ReportWorkspace from "./ReportWorkspace";
import { TemplateEditor } from "./Templates";

function Cover({ run }: { run: V2Run }) {
  const [broken, setBroken] = useState(false);
  return run.files?.includes("daily_image.png") && !broken ? (
    <img
      loading="lazy"
      src={getV2File(run.group_name, run.run_date, "daily_image.png")}
      alt={`${run.group_name} 日报图片`}
      onError={() => setBroken(true)}
    />
  ) : (
    <div className="gallery-placeholder">
      <ImageSquare size={36} />
      <span>{broken ? "图片读取失败" : "图片尚未就绪"}</span>
      <small>{run.run_date}</small>
    </div>
  );
}
function Styles() {
  const { msg, toast } = useToast();
  const catalogs = useAIImageCatalogs(toast);
  return (
    <>
      <ImageStylePanel {...catalogs} toast={toast} />
      <Toast message={msg} />
    </>
  );
}

export default function AIImages() {
  const query = useWorkspaceQuery();
  const date = query.get("date") ?? shanghaiDateInputValue();
  const view =
    query.get("view") ||
    (window.location.hash.split("?")[0] === "#/templates"
      ? "templates"
      : "workspace");
  const isGallery = view === "gallery";
  const collectionDate = query.get("collectionDate") ?? date;
  const group = query.get("group");
  const rememberScroll = useReportScroll(group, false);
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("all");
  const runs = useFetch(
    async () => ({ ...(await getRuns(collectionDate || undefined, { includeFiles: true })), collectionDate }),
    [collectionDate],
  );
  const currentRuns = runs.data?.collectionDate === collectionDate ? runs.data.runs : [];
  const filtered = currentRuns.filter(
    (run) =>
      run.group_name
        .toLocaleLowerCase()
        .includes(search.trim().toLocaleLowerCase()) &&
      (status === "all" || run.status === status),
  );
  const open = (run: V2Run) => {
    if (!isGallery && group === run.group_name && date === run.run_date) return;
    rememberScroll();
    updateWorkspaceQuery({
      group: run.group_name,
      date: run.run_date,
      panel: "preview",
      collectionDate,
      view: "workspace",
    });
  };
  useEffect(() => {
    if (view !== "workspace" || group || runs.loading || runs.error || !currentRuns.length) return;
    const first = currentRuns[0];
    // Normalize the initial selection without adding a Back-button history step.
    updateWorkspaceQuery({ group: first.group_name, date: first.run_date, collectionDate, panel: "preview" }, true);
  }, [view, group, runs.loading, runs.error, runs.data, collectionDate]);
  const changeView = (next: string) => {
    if (next === view) return;
    updateWorkspaceQuery({
      view: next, group: null, panel: null, date: collectionDate, collectionDate: null,
    });
  };
  return (
    <div className="studio-gallery ai-images-page">
      <PageHeader
        title={isGallery ? "作品画廊" : "日报作品"}
        description={isGallery ? "按图片浏览历史日报，打开作品即可进入处理工作区。" : "在左侧切换日报，直接预览内容、编辑提示词和查看运行情况。"}
        actions={
          <>
          <Button tone="ghost" onClick={() => changeView(isGallery ? "workspace" : "gallery")}><SquaresFour size={17} />{isGallery ? "返回日报工作区" : "浏览画廊"}</Button>
          <Button tone="secondary" onClick={runs.reload} busy={runs.loading}>
            <ArrowsClockwise size={17} />
            刷新作品
          </Button>
          </>
        }
      />
      <div
        className="studio-collection-nav"
        role="tablist"
        aria-label="日报作品区域"
      >
        {[
          ["workspace", "日报工作区", ImageSquare],
          ["styles", "图片风格", Palette],
          ["templates", "排行榜模板", TextT],
        ].map(([key, label, Icon]) => {
          const Glyph = Icon as typeof Palette;
          return (
            <button
              key={String(key)}
              type="button"
              role="tab"
              aria-selected={view === key}
              className={view === key ? "active" : ""}
              onClick={() => changeView(String(key))}
            >
              <Glyph size={20} />
              <span>{String(label)}</span>
            </button>
          );
        })}
      </div>
      {view === "styles" ? (
        <Styles />
      ) : view === "templates" ? (
        <TemplateEditor kind="ranking" />
      ) : (
        <>
          <div className="studio-gallery-toolbar">
            <label className="studio-date">
              <span>运行日期</span>
              <input
                type="date"
                aria-label="运行日期"
                value={collectionDate}
                onChange={(event) =>
                  updateWorkspaceQuery({
                    date: event.target.value,
                    collectionDate: null,
                    group: null,
                    panel: null,
                  })
                }
              />
            </label>
            <Button
              tone="ghost"
              onClick={() =>
                updateWorkspaceQuery({ date: "", collectionDate: null, group: null, panel: null })
              }
            >
              所有日期
            </Button>
            <div className="studio-search">
              <MagnifyingGlass size={18} />
              <input
                aria-label="搜索作品群名"
                placeholder="搜索群名…"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
            </div>
            <select
              aria-label="作品状态"
              value={status}
              onChange={(event) => setStatus(event.target.value)}
            >
              <option value="all">全部状态</option>
              <option value="READY_TO_SEND">待发送</option>
              <option value="SENT">已发送</option>
              <option value="FAILED">失败</option>
            </select>
            <span>{filtered.length} 份作品</span>
          </div>
          {runs.error && (
            <div className="studio-alert" role="alert">
              {runs.error}
              <Button onClick={runs.reload}>重新加载</Button>
            </div>
          )}
          <div
            className={`studio-reports-layout ${!isGallery ? "with-workspace" : ""}`}
          >
            <div
              className={isGallery ? "studio-gallery-grid" : "studio-report-list"}
            >
              {runs.loading ? (
                <LoadingState label="正在整理日报作品…" />
              ) : !filtered.length ? (
                <EmptyState
                  title="这里还没有匹配的作品"
                  description="尝试切换日期或搜索其他群名。"
                />
              ) : (
                filtered.map((run) => (
                  <article
                    className={`gallery-card ${group === run.group_name && date === run.run_date ? "selected" : ""}`}
                    key={`${run.group_name}:${run.run_date}`}
                  >
                    <button
                      type="button"
                      onClick={() => open(run)}
                      aria-label={`打开日报 ${run.group_name}`}
                    >
                      {isGallery && (
                        <div className="gallery-cover">
                          <Cover run={run} />
                          <span>{run.run_date}</span>
                        </div>
                      )}
                      <div className="gallery-card-copy">
                        <h3>
                          {run.group_name}
                          <ArrowUpRight size={18} />
                        </h3>
                        <div>
                          <span>{run.run_date}</span>
                          <StatusPill status={run.status} />
                        </div>
                      </div>
                    </button>
                  </article>
                ))
              )}
            </div>
            {!isGallery && group && (
              <ReportWorkspace
                key={`${date}:${group}`}
                target={{ groupName: group, runDate: date }}
                closable={false}
              />
            )}
          </div>
        </>
      )}
    </div>
  );
}
