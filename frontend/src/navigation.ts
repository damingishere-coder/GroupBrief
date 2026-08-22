import type { Icon } from "@phosphor-icons/react";
import { ChartBar, ChatDots, ChatsCircle, GearSix, HouseLine, ImageSquare } from "@phosphor-icons/react";
import { useCallback, useEffect, useState } from "react";

export type PageKey =
  | "dashboard"
  | "groups"
  | "ranking"
  | "images"
  | "messages"
  | "tasks"
  | "archive"
  | "settings"
  | "history"
  | "system";

export interface NavigationItem {
  key: PageKey;
  label: string;
  icon: Icon;
  activePages?: PageKey[];
  children?: NavigationItem[];
}

export const NAVIGATION: NavigationItem[] = [
  { key: "dashboard", label: "总览", icon: HouseLine },
  { key: "groups", label: "群聊与任务", icon: ChatsCircle, activePages: ["groups", "tasks"] },
  { key: "messages", label: "记录与归档", icon: ChatDots, activePages: ["messages", "archive"] },
  {
    key: "ranking",
    label: "当日群报",
    icon: ChartBar,
    activePages: ["ranking", "images"],
    children: [
      { key: "ranking", label: "排行榜", icon: ChartBar },
      { key: "images", label: "AI 图片", icon: ImageSquare },
    ],
  },
  { key: "settings", label: "设置中心", icon: GearSix },
];

const PAGE_KEYS = new Set<PageKey>([
  "dashboard", "groups", "tasks", "messages", "archive", "ranking", "images", "settings",
]);

export interface AppRoute {
  page: PageKey;
  groupMode?: "list" | "new" | "detail";
  groupId?: number;
  invalidGroupId?: string;
}

function hashSegments(): string[] {
  return window.location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
}

export function routeFromLocation(): AppRoute {
  const segments = hashSegments();
  const rawPage = segments[0];
  const pageValue = (
    rawPage === "templates"
      ? "ranking"
      : rawPage === "history"
        ? "archive"
        : rawPage === "system"
          ? "settings"
          : rawPage
  ) as PageKey | undefined;
  const page = pageValue && PAGE_KEYS.has(pageValue) ? pageValue : "dashboard";

  if (page !== "groups") return { page };

  const groupSegment = segments[1];
  if (!groupSegment) return { page, groupMode: "list" };
  if (groupSegment === "new") return { page, groupMode: "new" };

  const groupId = Number(groupSegment);
  if (Number.isInteger(groupId) && groupId > 0) {
    return { page, groupMode: "detail", groupId };
  }
  return { page, groupMode: "detail", invalidGroupId: groupSegment };
}

export function navigateToHash(path: string): void {
  const normalized = path.startsWith("#/") ? path : `#/${path.replace(/^\/+/, "")}`;
  if (window.location.hash === normalized) return;
  window.location.hash = normalized;
}

export function usePageNavigation() {
  const [route, setRoute] = useState<AppRoute>(routeFromLocation);

  useEffect(() => {
    const sync = () => setRoute(routeFromLocation());
    window.addEventListener("hashchange", sync);
    window.addEventListener("popstate", sync);
    if (!window.location.hash || !PAGE_KEYS.has(routeFromLocation().page)) {
      window.history.replaceState({}, "", "#/dashboard");
      sync();
    }
    return () => {
      window.removeEventListener("hashchange", sync);
      window.removeEventListener("popstate", sync);
    };
  }, []);

  const navigate = useCallback((next: PageKey | string) => {
    navigateToHash(next);
  }, []);

  return { page: route.page, route, navigate };
}
