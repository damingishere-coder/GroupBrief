import { lazy, Suspense } from "react";

import AppShell from "./components/layout/AppShell";
import { LoadingState } from "./components/common";
import { usePageNavigation } from "./navigation";
import { m, MotionProvider, PageTransition } from "./components/motion";

const Dashboard = lazy(() => import("./pages/v2/Dashboard"));
const Groups = lazy(() => import("./pages/v2/Groups"));
const GroupDetail = lazy(() => import("./pages/v2/GroupDetail"));
const Ranking = lazy(() => import("./pages/v2/Ranking"));
const AIImages = lazy(() => import("./pages/v2/AIImages"));
const ChatRecords = lazy(() => import("./pages/v2/ChatRecords"));
const Tasks = lazy(() => import("./pages/v2/Tasks"));
const Archive = lazy(() => import("./pages/v2/Archive"));
const Settings = lazy(() => import("./pages/v2/Settings"));

function WorkspaceTabs({
  tabs,
  active,
  onNavigate,
}: {
  tabs: { key: "groups" | "tasks" | "messages" | "archive"; label: string }[];
  active: string;
  onNavigate: (key: string) => void;
}) {
  return (
    <div className="workspace-tabs" role="tablist">
      {tabs.map((tab) => (
        <button
          key={tab.key}
          type="button"
          role="tab"
          aria-selected={active === tab.key}
          className={active === tab.key ? "is-active" : ""}
          onClick={() => onNavigate(tab.key)}
        >
          {active === tab.key && <m.span className="workspace-tab-indicator" layoutId="workspace-tab-indicator" aria-hidden="true" />}
          {tab.label}
        </button>
      ))}
    </div>
  );
}

export default function App() {
  const { page, route, navigate } = usePageNavigation();
  const pageKey = `${page}:${route.groupMode || ""}:${route.groupId || route.invalidGroupId || ""}`;

  return (
    <MotionProvider>
      <AppShell activePage={page} onNavigate={navigate}>
        <PageTransition pageKey={pageKey}>
          <Suspense fallback={<LoadingState label="正在加载页面…" />}>
        {page === "dashboard" && <Dashboard />}
        {page === "groups" && route.groupMode === "list" && (
          <div className="combined-workspace">
            <WorkspaceTabs active="groups" onNavigate={navigate} tabs={[{ key: "groups", label: "群聊配置" }, { key: "tasks", label: "任务中心" }]} />
            <Groups />
          </div>
        )}
        {page === "groups" && route.groupMode !== "list" && (
          <GroupDetail groupId={route.groupId} invalidGroupId={route.invalidGroupId} />
        )}
        {page === "ranking" && <Ranking />}
        {page === "images" && <AIImages />}
        {page === "tasks" && (
          <div className="combined-workspace">
            <WorkspaceTabs active="tasks" onNavigate={navigate} tabs={[{ key: "groups", label: "群聊配置" }, { key: "tasks", label: "任务中心" }]} />
            <Tasks />
          </div>
        )}
        {page === "messages" && (
          <div className="combined-workspace">
            <WorkspaceTabs active="messages" onNavigate={navigate} tabs={[{ key: "messages", label: "聊天记录" }, { key: "archive", label: "归档中心" }]} />
            <ChatRecords />
          </div>
        )}
        {page === "archive" && (
          <div className="combined-workspace">
            <WorkspaceTabs active="archive" onNavigate={navigate} tabs={[{ key: "messages", label: "聊天记录" }, { key: "archive", label: "归档中心" }]} />
            <Archive />
          </div>
        )}
        {page === "settings" && <Settings />}
          </Suspense>
        </PageTransition>
      </AppShell>
    </MotionProvider>
  );
}
