import { useState } from "react";
import { get, type Run } from "../api";
import { useFetch } from "../components/ui";

function statusBadge(status: string) {
  if (status === "success") return <span className="badge badge-ok">成功</span>;
  if (status === "running") return <span className="badge">进行中</span>;
  if (status === "partial") return <span className="badge badge-warn">部分成功</span>;
  return <span className="badge badge-bad">失败</span>;
}

interface GroupRunDetail {
  id: number;
  group_id: number;
  group_name: string;
  provider_used: string;
  message_count: number;
  speaker_count: number;
  ranking_status: string;
  prompt_status: string;
  error_message: string;
}

interface RunDetail extends Run {
  group_runs: GroupRunDetail[];
}

export default function Runs() {
  const { data } = useFetch(() => get<Run[]>("/runs"));
  const [expanded, setExpanded] = useState<number | null>(null);
  const [detail, setDetail] = useState<RunDetail | null>(null);

  const toggle = async (runId: number) => {
    if (expanded === runId) {
      setExpanded(null);
      setDetail(null);
      return;
    }
    setExpanded(runId);
    try {
      const d = await get<RunDetail>(`/runs/${runId}`);
      setDetail(d);
    } catch {
      setDetail(null);
    }
  };

  return (
    <div>
      <div className="page-header">
        <div className="page-title">执行记录</div>
        <div className="page-sub">
          每次自动或手动生成的历史记录（点击行展开群详情）
        </div>
      </div>
      <div className="card">
        {!data || data.length === 0 ? (
          <div className="empty-state">
            <div className="big">暂无执行记录</div>
            <div>生成群报后，这里会显示每次任务的详情</div>
          </div>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>ID</th>
                <th>报告日期</th>
                <th>统计范围</th>
                <th>触发</th>
                <th>状态</th>
                <th>时间</th>
              </tr>
            </thead>
            <tbody>
              {data.map((r) => (
                <tr
                  key={r.id}
                  style={{ cursor: "pointer" }}
                  onClick={() => toggle(r.id)}
                >
                  <td>{r.id}</td>
                  <td>{r.report_date}</td>
                  <td className="muted">
                    {r.range_start} ~ {r.range_end}
                  </td>
                  <td>{r.trigger_type === "auto" ? "自动" : "手动"}</td>
                  <td>{statusBadge(r.status)}</td>
                  <td className="muted">
                    {r.finished_at
                      ? new Date(r.finished_at).toLocaleString("zh-CN", {
                          hour12: false,
                        })
                      : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {detail && (
        <div className="card">
          <div className="card-title">
            Run #{detail.id} 群详情
            {detail.error_message ? (
              <span className="muted" style={{ fontSize: 12, marginLeft: 10 }}>
                {detail.error_message}
              </span>
            ) : null}
          </div>
          <table className="table">
            <thead>
              <tr>
                <th>群</th>
                <th>Provider</th>
                <th>消息数</th>
                <th>发言人数</th>
                <th>排行</th>
                <th>Prompt</th>
                <th>错误</th>
              </tr>
            </thead>
            <tbody>
              {detail.group_runs.map((gr) => (
                <tr key={gr.id}>
                  <td>{gr.group_name}</td>
                  <td>{gr.provider_used || "—"}</td>
                  <td>{gr.message_count}</td>
                  <td>{gr.speaker_count}</td>
                  <td>{statusBadge(gr.ranking_status)}</td>
                  <td>{statusBadge(gr.prompt_status)}</td>
                  <td className="muted" style={{ fontSize: 12 }}>
                    {gr.error_message}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
