import {
  ArrowUpRight,
  CaretLeft,
  CaretRight,
  Sparkle,
  Desktop,
  Heartbeat,
} from "@phosphor-icons/react";
import { useEffect, useState, type ReactNode } from "react";
import { NAVIGATION, navigateToHash, routeFromLocation, workspaceQuery, type PageKey } from "../../navigation";
import { listGroups } from '../../api';

async function openSearch() {
  const original = window.location.hash;
  const query = workspaceQuery();
  let group = query.get('groupId') || query.get('insightGroup') || String(routeFromLocation().groupId || '');
  if (!group && query.get('group')) {
    try {
      const value = query.get('group');
      const matches = (await listGroups()).filter(g => String(g.id) === value || g.display_name === value || g.wechat_group_name === value);
      if (matches.length === 1) group = String(matches[0].id);
    } catch { /* Search remains available even if the group list is unavailable. */ }
  }
  if (window.location.hash === original) navigateToHash(`search${group ? `?groupId=${encodeURIComponent(group)}` : ''}`, false);
}

export default function AppShell({
  activePage,
  onNavigate,
  children,
}: {
  activePage: PageKey;
  onNavigate: (page: PageKey) => void;
  children: ReactNode;
}) {
  const [compact, setCompact] = useState(false);
  useEffect(() => {
    const shortcut = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault(); void openSearch();
      }
    };
    window.addEventListener('keydown', shortcut);
    return () => window.removeEventListener('keydown', shortcut);
  }, []);
  const active = NAVIGATION.find((item) =>
    (item.activePages || [item.key]).includes(activePage),
  );
  return (
    <div className={`studio-shell ${compact ? "is-compact" : ""}`}>
      <a
        className="skip-link"
        href="#main-content"
        onClick={(event) => {
          event.preventDefault();
          document.getElementById("main-content")?.focus();
        }}
      >
        跳到主要内容
      </a>
      <aside className="studio-sidebar" aria-label="主导航">
        <button
          className="studio-brand"
          onClick={() => onNavigate("dashboard")}
          aria-label="GroupBrief 今日工作台"
        >
          <span className="studio-mark">
            <Sparkle size={25} weight="fill" />
          </span>
          <span className="studio-brand-text">
            <strong>
              GroupBrief<span>群报</span>
            </strong>
            <small>让每一次讨论，都有回响。</small>
          </span>
        </button>
        <div className="studio-nav-label">
          你的创作空间 <span>WORKSPACE</span>
        </div>
        <nav className="studio-nav">
          {NAVIGATION.map(({ key, label, icon: Icon, activePages }, index) => {
            const selected = (activePages || [key]).includes(activePage);
            return (
              <button
                key={key}
                type="button"
                className={selected ? "active" : ""}
                onClick={() => onNavigate(key)}
                aria-current={selected ? "page" : undefined}
                title={label}
              >
                <Icon size={22} weight={selected ? "fill" : "regular"} />
                <span>{label}</span>
                <small>0{index + 1}</small>
              </button>
            );
          })}
        </nav>
        <div className="studio-sidebar-bottom">
          <div className="studio-side-note">
            <Sparkle size={23} />
            <strong>好内容，值得被看见</strong>
            <p>从群聊中的灵感，到每天的一份精彩。</p>
            <button onClick={() => onNavigate("images")}>
              打开日报作品 <ArrowUpRight size={15} />
            </button>
          </div>
          <button
            className="studio-system"
            onClick={() => navigateToHash("settings?section=health")}
            title="系统健康"
          >
            <Heartbeat size={19} />
            <span>系统健康</span>
            <ArrowUpRight size={15} />
          </button>
          <button
            className="studio-collapse"
            onClick={() => setCompact((value) => !value)}
            aria-label={compact ? "展开侧栏" : "收起侧栏"}
          >
            {compact ? <CaretRight size={17} /> : <CaretLeft size={17} />}
            <span>收起侧栏</span>
          </button>
        </div>
      </aside>
      <section className="studio-main">
        <header className="studio-topbar">
          <div>
            <span className="studio-breadcrumb">创作空间</span>
            <span className="studio-slash">/</span>
            <strong>{active?.label || "工作台"}</strong>
          </div>
          <div className="studio-local">
            <button className="studio-search-entry" onClick={() => void openSearch()}>搜索群聊 <kbd>Ctrl K</kbd></button>
            <Desktop size={16} />
            <span>本地工作空间</span>
          </div>
        </header>
        <main id="main-content" className="studio-content" tabIndex={-1}>
          {children}
        </main>
      </section>
    </div>
  );
}
