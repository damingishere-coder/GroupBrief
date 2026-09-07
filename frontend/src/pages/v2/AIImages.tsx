import { useReportScroll } from "../../components/useReportScroll";
import { useState } from "react";
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
      : "gallery");
  const group = query.get("group");
  const rememberScroll = useReportScroll(group);
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("all");
  const runs = useFetch(
    () => getRuns(date || undefined, { includeFiles: true }),
    [date],
  );
  const filtered = (runs.data?.runs || []).filter(
    (run) =>
      run.group_name
        .toLocaleLowerCase()
        .includes(search.trim().toLocaleLowerCase()) &&
      (status === "all" || run.status === status),
  );
  const open = (run: V2Run) => {
    rememberScroll();
    updateWorkspaceQuery({
      group: run.group_name,
      date: run.run_date,
      panel: "preview",
      collectionDate: query.get("collectionDate") ?? date,
    });
  };
  return (
    <div className="studio-gallery ai-images-page">
      <PageHeader
        title="把每一天的精彩，收进作品集"
        description="浏览群聊日报、打磨图片风格，让灵感拥有自己的样子。"
        actions={
          <Button tone="secondary" onClick={runs.reload} busy={runs.loading}>
            <ArrowsClockwise size={17} />
            刷新作品
          </Button>
        }
      />
      <div
        className="studio-collection-nav"
        role="tablist"
        aria-label="日报作品区域"
      >
        {[
          ["gallery", "日报画廊", SquaresFour],
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
              onClick={() =>
                updateWorkspaceQuery({
                  view: String(key),
                  group: null,
                  panel: null,
                })
              }
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
                value={date}
                onChange={(event) =>
                  updateWorkspaceQuery({
                    date: event.target.value,
                    group: null,
                    panel: null,
                  })
                }
              />
            </label>
            <Button
              tone="ghost"
              onClick={() =>
                updateWorkspaceQuery({ date: "", group: null, panel: null })
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
            className={`studio-reports-layout ${group ? "with-workspace" : ""}`}
          >
            <div
              className={group ? "studio-report-list" : "studio-gallery-grid"}
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
                    className={`gallery-card ${group === run.group_name ? "selected" : ""}`}
                    key={`${run.group_name}:${run.run_date}`}
                  >
                    <button
                      type="button"
                      onClick={() => open(run)}
                      aria-label={`打开日报 ${run.group_name}`}
                    >
                      {!group && (
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
            {group && (
              <ReportWorkspace
                key={`${date}:${group}`}
                target={{ groupName: group, runDate: date }}
              />
            )}
          </div>
        </>
      )}
    </div>
  );
}
