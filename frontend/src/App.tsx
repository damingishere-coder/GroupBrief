import { useState } from "react";
import Dashboard from "./pages/Dashboard";
import Groups from "./pages/Groups";
import Runs from "./pages/Runs";
import SettingsPage from "./pages/Settings";
import About from "./pages/About";

type Page = "dashboard" | "groups" | "runs" | "settings" | "about";

const NAV: { key: Page; label: string }[] = [
  { key: "dashboard", label: "仪表盘" },
  { key: "groups", label: "群聊管理" },
  { key: "runs", label: "执行记录" },
  { key: "settings", label: "配置设置" },
  { key: "about", label: "关于" },
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
            <div className="brand-sub">v1.0.0</div>
          </div>
        </div>
        {NAV.map((item) => (
          <button
            key={item.key}
            className={`nav-item ${page === item.key ? "active" : ""}`}
            onClick={() => setPage(item.key)}
          >
            {item.label}
          </button>
        ))}
      </aside>
      <main className="main">
        {page === "dashboard" && <Dashboard onNav={(p) => setPage(p as Page)} />}
        {page === "groups" && <Groups />}
        {page === "runs" && <Runs />}
        {page === "settings" && <SettingsPage />}
        {page === "about" && <About />}
      </main>
    </div>
  );
}
