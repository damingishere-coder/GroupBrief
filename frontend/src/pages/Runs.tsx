import { get, type Run } from "../api";
import { useFetch } from "../components/ui";

function statusBadge(status: string) {
  if (status === "success") return <span className="badge badge-ok">成功</span>;
  if (status === "running") return <span className="badge">进行中</span>;
  if (status === "partial") return <span className="badge badge-warn">部分成功</span>;
  return <span className="badge badge-bad">失败</span>;
}

export default function Runs() {
  const { data } = useFetch(() => get<Run[]>("/runs"));

  return (
    <div>
      <div className="page-header">
        <div className="page-title">执行记录</div>
        <div className="page-sub">每次自动或手动生成的历史记录</div>
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
                <tr key={r.id}>
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
    </div>
  );
}
