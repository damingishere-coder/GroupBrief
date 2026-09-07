import { useEffect, useState } from "react";
import { getDashboard, getRuntimeLogs } from "../../api";
import { useFetch } from "../../components/ui";
import { DashboardRuntimePanels } from "./DashboardRuntimePanels";
import { runtimeRefreshDelay } from "./dashboardRuntime";
import { shanghaiDateInputValue } from "../../date";

export function RuntimeOverview({ date }: { date: string }) {
  const [source, setSource] = useState<
    "all" | "scheduler" | "app" | "provider" | "ai"
  >("all");
  const [level, setLevel] = useState<
    "all" | "DEBUG" | "INFO" | "WARNING" | "ERROR" | "CRITICAL"
  >("all");
  const [paused, setPaused] = useState(false);
  const dashboard = useFetch(() => getDashboard(date), [date]);
  const logs = useFetch(
    () =>
      getRuntimeLogs(date, {
        sources: source === "all" ? undefined : source,
        levels: level === "all" ? undefined : level,
      }),
    [date, source, level],
  );
  useEffect(() => {
    const delay = runtimeRefreshDelay(
      dashboard.data?.runtime?.overall_status || "not_started",
      {
        isToday: date === shanghaiDateInputValue(),
        visible: document.visibilityState !== "hidden",
        scheduledAt: dashboard.data?.runtime?.scheduler?.scheduled_at,
      },
    );
    if (delay === null || paused) return;
    const timer = window.setTimeout(() => {
      dashboard.reload();
      logs.reload();
    }, delay);
    return () => window.clearTimeout(timer);
  }, [dashboard.data, dashboard.reload, logs.reload, date, paused]);
  return (
    <div className="studio-runtime">
      {dashboard.error && <p role="alert">{dashboard.error}</p>}
      {dashboard.data?.runtime && (
        <DashboardRuntimePanels
          runtime={dashboard.data.runtime}
          logs={logs.data}
          loading={logs.loading}
          error={logs.error}
          source={source}
          level={level}
          paused={paused}
          onSourceChange={setSource}
          onLevelChange={setLevel}
          onPausedChange={setPaused}
          onRefresh={() => {
            dashboard.reload();
            logs.reload();
          }}
        />
      )}
    </div>
  );
}
