import { useEffect, useState } from "react";
import { V2Run, getRunDetail, getRuns, getV2File } from "../../api";
import { useToast } from "../../components/ui";

const STATUS_META: Record<string, string> = {
  SENT: "badge",
  READY_TO_SEND: "badge-ok",
  IMAGE_READY: "badge-ok",
  PROMPT_READY: "badge-ok",
  RANKING_READY: "badge-warn",
  DATA_READY: "badge-warn",
  PENDING: "badge-warn",
  FAILED: "badge-bad",
};

interface Detail {
  run: V2Run;
  files: string[];
}

export default function History() {
  const { msg, toast } = useToast();
  const [runs, setRuns] = useState<V2Run[]>([]);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [rankingText, setRankingText] = useState("");
  const [promptText, setPromptText] = useState("");

  const load = () => {
    getRuns()
      .then((d) => setRuns(d.runs))
      .catch((e) => toast(String(e)));
  };
  useEffect(load, []);

  const openDetail = (r: V2Run) => {
    getRunDetail(r.group_name, r.run_date)
      .then((d) => {
        setDetail(d);
        setRankingText("");
        setPromptText("");
        if (d.files.includes("ranking.txt")) {
          fetch(getV2File(r.group_name, r.run_date, "ranking.txt"))
            .then((res) => res.text())
            .then(setRankingText)
            .catch(() => {});
        }
        if (d.files.includes("image_prompt.txt")) {
          fetch(getV2File(r.group_name, r.run_date, "image_prompt.txt"))
            .then((res) => res.text())
            .then(setPromptText)
            .catch(() => {});
        }
      })
      .catch((e) => toast(String(e)));
  };

  return (
    <div>
      <div className="page-header">
        <div>
          <div className="page-title">历史日报</div>
          <div className="page-sub">按 群 → 日期 回看每日日报内容与状态</div>
        </div>
        <button className="btn btn-secondary btn-sm" onClick={load}>
          刷新
        </button>
      </div>

      <div className="hist-layout">
        <div className="card hist-list">
          <div className="card-title">运行记录（{runs.length}）</div>
          {runs.length === 0 && <div className="empty-state">暂无运行记录，先到「今日概览」触发生成。</div>}
          {runs.map((r) => (
            <button key={`${r.group_name}-${r.run_date}`} className="hist-item" onClick={() => openDetail(r)}>
              <span className="hist-name">{r.group_name}</span>
              <span className="muted">{r.run_date}</span>
              <span className={`badge ${STATUS_META[r.status] || "badge-warn"}`}>{r.status}</span>
            </button>
          ))}
        </div>

        {detail && (
          <div className="card hist-detail">
            <div className="card-title">
              {detail.run.group_name} · {detail.run.run_date}
              <span className={`badge ${STATUS_META[detail.run.status] || "badge-warn"}`}>{detail.run.status}</span>
            </div>
            <div className="muted hist-period">
              周期 {detail.run.period_start || "—"} ~ {detail.run.period_end || "—"}
              {detail.run.sent_at ? ` · 已发送 ${String(detail.run.sent_at)}` : ""}
            </div>
            {typeof detail.run.error === "string" && detail.run.error && (
              <div className="group-card-error">{detail.run.error}</div>
            )}

            {detail.files.includes("daily_image.png") && (
              <div className="hist-img">
                <img src={getV2File(detail.run.group_name, detail.run.run_date, "daily_image.png")} alt="日报图片" />
              </div>
            )}

            {rankingText && (
              <div className="hist-block">
                <div className="hist-block-title">排行榜</div>
                <pre>{rankingText}</pre>
              </div>
            )}
            {promptText && (
              <div className="hist-block">
                <div className="hist-block-title">生图 Prompt</div>
                <pre>{promptText.slice(0, 3000)}</pre>
              </div>
            )}
            {!rankingText && !promptText && (
              <div className="muted">
                文件：{detail.files.length ? detail.files.join("、") : "（无）"}
              </div>
            )}
          </div>
        )}
      </div>
      {msg && <div className="toast">{msg}</div>}
    </div>
  );
}
