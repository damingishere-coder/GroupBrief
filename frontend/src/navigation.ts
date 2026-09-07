import type { Icon } from "@phosphor-icons/react";
import { ChatDots, ChatsCircle, GearSix, HouseLine, ImageSquare, ListChecks } from "@phosphor-icons/react";
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
  { key: "dashboard", label: "今日工作台", icon: HouseLine },
  { key: "images", label: "日报作品", icon: ImageSquare, activePages: ["ranking", "images"] },
  { key: "groups", label: "群聊管理", icon: ChatsCircle },
  { key: "tasks", label: "运行任务", icon: ListChecks },
  { key: "messages", label: "消息归档", icon: ChatDots, activePages: ["messages", "archive"] },
  { key: "settings", label: "设置", icon: GearSix },
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
  return window.location.hash.split("?")[0].replace(/^#\/?/, "").split("/").filter(Boolean);
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

export function navigateToHash(path: string, preserveDate = true, replace = false): void {
  let normalized = path.startsWith("#/") ? path : `#/${path.replace(/^\/+/, "")}`;
  const [base, search] = normalized.split("?");
  const params = new URLSearchParams(search);
  const current = workspaceQuery();
  if (preserveDate && !params.has("date") && current.has("date")) params.set("date", current.get("date") || "");
  normalized = `${base}${params.size ? `?${params}` : ""}`;
  if (window.location.hash === normalized) return;
  if (!allowNavigation(normalized)) return;
  acceptedHash = normalized;
  if (replace) {
    window.history.replaceState({}, "", normalized);
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  } else window.location.hash = normalized;
}

let leaveGuard: (() => boolean) | undefined;
let acceptedHash = window.location.hash;
// Run before component subscriptions so a rejected Back never renders another report.
window.addEventListener("hashchange", () => {
  if (!allowNavigation(window.location.hash)) window.history.replaceState({}, "", acceptedHash);
  else acceptedHash = window.location.hash;
});
function editorIdentity(hash: string) {
  const [path, query] = hash.split("?");
  const params = new URLSearchParams(query);
  return `${path}:${params.get("date") || ""}:${params.get("group") || ""}:${params.get("view") || ""}`;
}
function allowNavigation(next: string) {
  return editorIdentity(next) === editorIdentity(acceptedHash) || !leaveGuard || leaveGuard();
}
export function registerLeaveGuard(guard: () => boolean) {
  leaveGuard = guard;
  acceptedHash = window.location.hash;
  return () => { if (leaveGuard === guard) leaveGuard = undefined; };
}
export function workspaceQuery() {
  return new URLSearchParams(window.location.hash.split("?")[1] || "");
}
export function updateWorkspaceQuery(values: Record<string, string | null>, replace = false) {
  const params = workspaceQuery();
  Object.entries(values).forEach(([key, value]) => value === null ? params.delete(key) : params.set(key, value));
  const query = params.toString();
  navigateToHash(`${window.location.hash.split("?")[0] || "#/dashboard"}${query ? `?${query}` : ""}`, false, replace);
}
export function useWorkspaceQuery() {
  const [query, setQuery] = useState(workspaceQuery);
  useEffect(() => {
    const sync = () => setQuery(workspaceQuery());
    window.addEventListener("hashchange", sync);
    return () => window.removeEventListener("hashchange", sync);
  }, []);
  return query;
}

export function usePageNavigation() {
  const [route, setRoute] = useState<AppRoute>(routeFromLocation);

  useEffect(() => {
    acceptedHash = window.location.hash;
    const sync = () => setRoute(routeFromLocation());
    window.addEventListener("hashchange", sync);
    if (!window.location.hash || !PAGE_KEYS.has(routeFromLocation().page)) {
      window.history.replaceState({}, "", "#/dashboard");
      sync();
    }
    return () => {
      window.removeEventListener("hashchange", sync);
    };
  }, []);

  const navigate = useCallback((next: PageKey | string) => {
    navigateToHash(next);
  }, []);

  return { page: route.page, route, navigate };
}
