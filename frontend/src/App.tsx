import Dashboard from "./pages/v2/Dashboard";
import Groups from "./pages/v2/Groups";
import GroupDetail from "./pages/v2/GroupDetail";
import Ranking from "./pages/v2/Ranking";
import AIImages from "./pages/v2/AIImages";
import ChatRecords from "./pages/v2/ChatRecords";
import Tasks from "./pages/v2/Tasks";
import Archive from "./pages/v2/Archive";
import Settings from "./pages/v2/Settings";
import AppShell from "./components/layout/AppShell";
import { usePageNavigation } from "./navigation";

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
          {tab.label}
        </button>
      ))}
    </div>
  );
}

export default function App() {
  const { page, route, navigate } = usePageNavigation();

  return (
    <AppShell activePage={page} onNavigate={navigate}>
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
    </AppShell>
  );
}
