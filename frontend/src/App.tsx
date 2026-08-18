import { useState } from "react";
import Dashboard from "./pages/v2/Dashboard";
import Groups from "./pages/v2/Groups";
import Templates from "./pages/v2/Templates";
import History from "./pages/v2/History";
import System from "./pages/v2/System";

type Page = "dashboard" | "groups" | "templates" | "history" | "system";

const NAV: { key: Page; label: string; icon: string }[] = [
  { key: "dashboard", label: "今日概览", icon: "◐" },
  { key: "groups", label: "群管理", icon: "▤" },
  { key: "templates", label: "模板中心", icon: "❐" },
  { key: "history", label: "历史日报", icon: "▦" },
  { key: "system", label: "系统状态", icon: "✦" },
];

export default function App() {
  const [page, setPage] = useState<Page>("dashboard");

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-logo">报</div>
          <div>
            <div className="brand-name">群报 GroupBrief</div>
            <div className="brand-sub">V2 全自动日报</div>
          </div>
        </div>
        {NAV.map((item) => (
          <button
            key={item.key}
            className={`nav-item ${page === item.key ? "active" : ""}`}
            onClick={() => setPage(item.key)}
          >
            <span className="nav-icon">{item.icon}</span>
            {item.label}
          </button>
        ))}
      </aside>
      <main className="main">
        {page === "dashboard" && <Dashboard />}
        {page === "groups" && <Groups />}
        {page === "templates" && <Templates />}
        {page === "history" && <History />}
        {page === "system" && <System />}
      </main>
    </div>
  );
}
