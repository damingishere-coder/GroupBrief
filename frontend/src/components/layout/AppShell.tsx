import {
  Bell,
  BookOpen,
  CalendarBlank,
  CaretDown,
  List,
  X,
} from "@phosphor-icons/react";
import { useEffect, useState, type ReactNode } from "react";
import { NAVIGATION, type PageKey } from "../../navigation";

interface AppShellProps {
  activePage: PageKey;
  onNavigate: (page: PageKey) => void;
  children: ReactNode;
}

function localDateLabel() {
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "long",
    day: "numeric",
    weekday: "long",
  }).format(new Date());
}

export default function AppShell({ activePage, onNavigate, children }: AppShellProps) {
  const [navOpen, setNavOpen] = useState(false);
  const [accountOpen, setAccountOpen] = useState(false);

  useEffect(() => {
    setNavOpen(false);
  }, [activePage]);

  return (
    <div className="app-canvas">
      <div className="app-shell">
        <aside className={`app-sidebar ${navOpen ? "is-open" : ""}`} aria-label="主导航">
          <div className="app-brand">
            <img className="app-brand-logo" src="/assets/groupbrief-logo.png" alt="GroupBrief" />
            <div className="app-brand-copy">
              <strong>GroupBrief 群报</strong>
              <span>本地自动化工作台</span>
            </div>
            <button
              className="icon-button sidebar-close"
              type="button"
              aria-label="关闭导航"
              onClick={() => setNavOpen(false)}
            >
              <X size={20} />
            </button>
          </div>

          <nav className="app-nav">
            {NAVIGATION.map(({ key, label, icon: NavIcon, activePages, children }) => {
              const parentActive = (activePages || [key]).includes(activePage);
              return (
                <div className={`app-nav-group ${parentActive ? "is-active" : ""}`} key={key}>
                  <button
                    className={`app-nav-item ${parentActive ? "active" : ""}`}
                    type="button"
                    aria-current={parentActive && !children ? "page" : undefined}
                    onClick={() => onNavigate(key)}
                  >
                    <NavIcon size={21} weight={parentActive ? "fill" : "regular"} />
                    <span>{label}</span>
                  </button>
                  {children && (
                    <div className="app-nav-children" aria-label={`${label}子栏目`}>
                      {children.map(({ key: childKey, label: childLabel, icon: ChildIcon }) => (
                        <button
                          key={childKey}
                          type="button"
                          className={`app-nav-child ${activePage === childKey ? "active" : ""}`}
                          aria-current={activePage === childKey ? "page" : undefined}
                          onClick={() => onNavigate(childKey)}
                        >
                          <ChildIcon size={17} weight={activePage === childKey ? "fill" : "regular"} />
                          <span>{childLabel}</span>
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </nav>

          <button className="app-help" type="button" onClick={() => onNavigate("system")}>
            <BookOpen size={20} />
            <span>帮助与系统检查</span>
          </button>
        </aside>

        {navOpen && <button className="sidebar-scrim" type="button" aria-label="关闭导航" onClick={() => setNavOpen(false)} />}

        <section className="app-workspace">
          <header className="app-topbar">
            <div className="topbar-date">
              <button className="icon-button mobile-menu" type="button" aria-label="打开导航" onClick={() => setNavOpen(true)}>
                <List size={22} />
              </button>
              <CalendarBlank size={22} />
              <span>{localDateLabel()}</span>
            </div>
            <div className="topbar-actions">
              <button className="icon-button" type="button" aria-label="通知">
                <Bell size={21} />
              </button>
              <span className="topbar-divider" aria-hidden="true" />
              <div className="local-account">
                <button
                  className="local-account-trigger"
                  type="button"
                  aria-expanded={accountOpen}
                  onClick={() => setAccountOpen((value) => !value)}
                >
                  <span className="local-account-avatar" aria-hidden="true">GB</span>
                  <span>本机管理</span>
                  <CaretDown size={16} />
                </button>
                {accountOpen && (
                  <div className="local-account-menu" role="menu">
                    <strong>本地模式</strong>
                    <span>数据与配置仅保存在这台电脑</span>
                    <button type="button" onClick={() => { onNavigate("system"); setAccountOpen(false); }}>
                      打开系统状态
                    </button>
                  </div>
                )}
              </div>
            </div>
          </header>
          <main className="app-content">{children}</main>
        </section>
      </div>
    </div>
  );
}
