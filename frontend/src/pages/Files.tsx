import { useState } from "react";
import { get } from "../api";
import { useFetch } from "../components/ui";

interface HandoffGroup {
  date: string;
  directory: string;
  path: string;
  handoff: {
    group_id?: string;
    group_name?: string;
    ranking_file?: string;
    prompt_file?: string;
    poster_file?: string | null;
    status?: string;
  };
  files: string[];
}

export default function Files() {
  const { data: dates } = useFetch(() => get<string[]>("/files/dates"));
  const [selected, setSelected] = useState<string | null>(null);
  const [groups, setGroups] = useState<HandoffGroup[]>([]);

  const load = async (date: string) => {
    setSelected(date);
    const list = await get<HandoffGroup[]>(`/files/${date}`);
    setGroups(list);
  };

  return (
    <div>
      <div className="page-header">
        <div className="page-title">文件管理</div>
        <div className="page-sub">
          输出目录（output/）与 V2 Handoff 交接接口
        </div>
      </div>

      <div className="tabs" style={{ marginBottom: 16 }}>
        {(dates ?? []).map((d) => (
          <button
            key={d}
            className={`tab ${selected === d ? "active" : ""}`}
            onClick={() => load(d)}
          >
            {d}
          </button>
        ))}
      </div>

      {!selected ? (
        <div className="empty-state">
          <div className="big">暂无输出文件</div>
          <div>生成群报后，这里会按日期列出每个群的输出</div>
        </div>
      ) : (
        groups.map((g) => (
          <div className="card" key={g.directory}>
            <div className="row">
              <div>
                <strong>{g.handoff.group_name || g.directory}</strong>
                <div className="muted" style={{ fontSize: 13 }}>
                  ID: {g.handoff.group_id} · 状态:
                  <span
                    className={`badge ${
                      g.handoff.status === "prompt_ready" ? "badge-ok" : "badge-warn"
                    }`}
                    style={{ marginLeft: 6 }}
                  >
                    {g.handoff.status ?? "unknown"}
                  </span>
                </div>
              </div>
              <div className="spacer" />
              <span className="muted" style={{ fontSize: 12 }}>
                {g.path}
              </span>
            </div>
            <div className="row" style={{ marginTop: 12, gap: 8 }}>
              {g.files.map((f) => (
                <a
                  key={f}
                  className="btn btn-sm btn-ghost"
                  href={`/api/files/${g.date}/${g.directory}/raw/${f}`}
                  target="_blank"
                  rel="noreferrer"
                >
                  {f}
                </a>
              ))}
            </div>
          </div>
        ))
      )}
    </div>
  );
}
