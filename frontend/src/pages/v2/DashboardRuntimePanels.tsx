import { useEffect, useRef } from "react";
import { ArrowsClockwise, Pause, Play } from "@phosphor-icons/react";

import type {
  DashboardRuntime,
  RuntimeLogsResponse,
  RuntimeNodeStatus,
} from "../../api";
import { Button, StatusBadge } from "../../components/common";
import {
  formatRuntimeTime,
  RUNTIME_NODE_META,
} from "./dashboardRuntime";

const SOURCE_LABELS = {
  all: "全部来源",
  scheduler: "调度",
  app: "应用",
  provider: "数据读取",
  ai: "AI",
} as const;

const LEVEL_LABELS = {
  all: "全部级别",
  DEBUG: "调试",
  INFO: "正常",
  WARNING: "警告",
  ERROR: "错误",
  CRITICAL: "严重错误",
} as const;

function NodeBadge({ status }: { status: RuntimeNodeStatus }) {
  const meta = RUNTIME_NODE_META[status];
  return <StatusBadge tone={meta.tone}>{meta.label}</StatusBadge>;
}

function TaskNodes({ runtime }: { runtime: DashboardRuntime }) {
  return (
    <section className="dashboard-panel dashboard-runtime-panel" aria-label="任务节点">
      <div className="dashboard-panel-heading dashboard-panel-heading-with-action">
        <div>
          <h2>任务节点</h2>
          <p>
            下次生成 {formatRuntimeTime(runtime.scheduler.next_generate_at)}
            {` · 下次发送批次 ${formatRuntimeTime(runtime.scheduler.next_send_at)}`}
            {runtime.scheduler.generation_started_at
              ? ` · 启动 ${formatRuntimeTime(runtime.scheduler.generation_started_at)}`
              : " · 等待后台任务启动"}
            {runtime.scheduler.generation_completed_at
              ? ` · 完成 ${formatRuntimeTime(runtime.scheduler.generation_completed_at)}`
              : ""}
          </p>
        </div>
        <span className="dashboard-runtime-updated">更新 {formatRuntimeTime(runtime.updated_at)}</span>
      </div>

      <div className="dashboard-runtime-nodes" role="list" aria-label="每日运行阶段">
        {runtime.nodes.map((node, index) => (
          <div className={`dashboard-runtime-node is-${node.status}`} role="listitem" key={node.id}>
            <div className="dashboard-runtime-node-index" aria-hidden="true">{index + 1}</div>
            <div className="dashboard-runtime-node-main">
              <strong>{node.label}</strong>
              <span>
                {node.id === "scheduler"
                  ? node.status === "pending" ? "尚未启动" : "调度已启动"
                  : `${node.completed_groups} / ${node.total_groups} 个群完成`}
              </span>
            </div>
            <NodeBadge status={node.status} />
          </div>
        ))}
      </div>

      <div className="dashboard-runtime-groups" aria-label="各群任务进度">
        {runtime.groups.length === 0 ? (
          <p className="dashboard-runtime-empty">暂无启用群任务。</p>
        ) : runtime.groups.map((group) => (
          <details className="dashboard-runtime-group" key={`${group.group_id}-${group.group_name}`}>
            <summary>
              <span>
                <strong>{group.group_name}</strong>
                <small>当前：{group.current_node_label} · {formatRuntimeTime(group.updated_at)}</small>
              </span>
              <NodeBadge status={group.node_status} />
            </summary>
            <div className="dashboard-runtime-group-nodes">
              {group.nodes.map((node) => (
                <span className={`is-${node.status}`} key={node.id} title={`${node.label}：${RUNTIME_NODE_META[node.status].label}`}>
                  <i aria-hidden="true" />{node.label}
                </span>
              ))}
            </div>
            {(group.last_error_summary || group.last_error_type) && (
              <p className="dashboard-runtime-group-error">
                {group.last_error_type && <b>{group.last_error_type}</b>}
                <span>{group.last_error_summary || "任务需要人工检查"}</span>
              </p>
            )}
          </details>
        ))}
      </div>
    </section>
  );
}

interface RuntimeLogPanelProps {
  logs: RuntimeLogsResponse | null;
  loading: boolean;
  error: string;
  source: keyof typeof SOURCE_LABELS;
  level: keyof typeof LEVEL_LABELS;
  paused: boolean;
  onSourceChange: (value: keyof typeof SOURCE_LABELS) => void;
  onLevelChange: (value: keyof typeof LEVEL_LABELS) => void;
  onPausedChange: (value: boolean) => void;
  onRefresh: () => void;
}

function RuntimeLogPanel({
  logs,
  loading,
  error,
  source,
  level,
  paused,
  onSourceChange,
  onLevelChange,
  onPausedChange,
  onRefresh,
}: RuntimeLogPanelProps) {
  const logListRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!paused && logListRef.current) {
      logListRef.current.scrollTop = logListRef.current.scrollHeight;
    }
  }, [logs?.updated_at, paused]);

  return (
    <section className="dashboard-panel dashboard-runtime-panel dashboard-log-panel" aria-label="运行日志">
      <div className="dashboard-panel-heading dashboard-log-heading">
        <div>
          <h2>运行日志</h2>
          <p>只读展示当天最近日志，敏感内容已过滤。</p>
        </div>
        <Button tone="ghost" className="ui-button-compact" onClick={onRefresh} busy={loading}>
          <ArrowsClockwise size={15} aria-hidden="true" />刷新
        </Button>
      </div>

      <div className="dashboard-log-controls">
        <label>
          <span>来源</span>
          <select aria-label="日志来源" value={source} onChange={(event) => onSourceChange(event.target.value as keyof typeof SOURCE_LABELS)}>
            {Object.entries(SOURCE_LABELS).map(([value, label]) => <option value={value} key={value}>{label}</option>)}
          </select>
        </label>
        <label>
          <span>级别</span>
          <select aria-label="日志级别" value={level} onChange={(event) => onLevelChange(event.target.value as keyof typeof LEVEL_LABELS)}>
            {Object.entries(LEVEL_LABELS).map(([value, label]) => <option value={value} key={value}>{label}</option>)}
          </select>
        </label>
        <Button tone="ghost" className="ui-button-compact" onClick={() => onPausedChange(!paused)}>
          {paused ? <Play size={15} aria-hidden="true" /> : <Pause size={15} aria-hidden="true" />}
          {paused ? "继续滚动" : "暂停滚动"}
        </Button>
      </div>

      <div className="dashboard-log-list" ref={logListRef} role="log" aria-live={paused ? "off" : "polite"}>
        {error ? (
          <div className="dashboard-log-state is-error">日志加载失败：{error}</div>
        ) : !logs?.items.length ? (
          <div className="dashboard-log-state">当前筛选条件下暂无日志。</div>
        ) : logs.items.map((item, index) => (
          <article className={`dashboard-log-line is-${item.level.toLowerCase()}`} key={`${item.timestamp}-${item.source}-${index}`}>
            <header>
              <time>{formatRuntimeTime(item.timestamp).slice(11)}</time>
              <span className="dashboard-log-source">{SOURCE_LABELS[item.source]}</span>
              <span className="dashboard-log-level">{item.level}</span>
            </header>
            <pre>{item.message}</pre>
          </article>
        ))}
      </div>
      {logs?.truncated && <p className="dashboard-log-note">仅显示最近记录；部分敏感或过长内容已隐藏。</p>}
    </section>
  );
}

interface DashboardRuntimePanelsProps extends RuntimeLogPanelProps {
  runtime: DashboardRuntime;
}

export function DashboardRuntimePanels({ runtime, ...logProps }: DashboardRuntimePanelsProps) {
  return (
    <div className="dashboard-runtime-grid">
      <TaskNodes runtime={runtime} />
      <RuntimeLogPanel {...logProps} />
    </div>
  );
}
