import { beforeEach, describe, expect, it } from "vitest";

import { NAVIGATION, navigateToHash, routeFromLocation, registerLeaveGuard, updateWorkspaceQuery } from "./navigation";

describe("hash navigation", () => {
  beforeEach(() => {
    window.location.hash = "";
  });

  it("defaults unknown paths to dashboard", () => {
    window.location.hash = "#/does-not-exist";
    expect(routeFromLocation()).toEqual({ page: "dashboard" });
  });

  it("keeps valid group details and rejects invalid ids", () => {
    window.location.hash = "#/groups/42";
    expect(routeFromLocation()).toEqual({ page: "groups", groupMode: "detail", groupId: 42 });

    window.location.hash = "#/groups/not-a-number";
    expect(routeFromLocation()).toEqual({
      page: "groups",
      groupMode: "detail",
      invalidGroupId: "not-a-number",
    });
  });

  it("normalizes legacy aliases and navigation targets", () => {
    window.location.hash = "#/history";
    expect(routeFromLocation()).toEqual({ page: "archive" });

    navigateToHash("/images");
    expect(window.location.hash).toBe("#/images");
  });

  it("preserves explicit dates including an all-dates selection across pages", () => {
    window.location.hash = "#/dashboard?date=2026-09-06&group=test";
    navigateToHash("/tasks");
    expect(window.location.hash).toBe("#/tasks?date=2026-09-06");
    window.location.hash = "#/images?date=";
    navigateToHash("/messages");
    expect(window.location.hash).toBe("#/messages?date=");
  });

  it("protects the report identity while allowing another panel of the same draft", () => {
    window.location.hash = "#/dashboard?date=2026-09-06&group=test&panel=edit";
    const release = registerLeaveGuard(() => false);
    try {
      updateWorkspaceQuery({ panel: "preview" });
      expect(window.location.hash).toContain("panel=preview");
      updateWorkspaceQuery({ group: "another" });
      expect(window.location.hash).toContain("group=test");
      navigateToHash("/groups");
      expect(window.location.hash).toContain("#/dashboard");
    } finally {
      release();
    }
  });

  it("resolves old template and system URLs even with query parameters", () => {
    window.location.hash = "#/templates?date=2026-09-06";
    expect(routeFromLocation()).toEqual({ page: "ranking" });
    window.location.hash = "#/system?section=health";
    expect(routeFromLocation()).toEqual({ page: "settings" });
  });

  it("can normalize the initial report without an extra history entry", () => {
    window.history.replaceState({}, "", "#/images?date=2026-09-06");
    const length = window.history.length;
    updateWorkspaceQuery({ group: "test", panel: "preview" }, true);
    expect(window.history.length).toBe(length);
    expect(window.location.hash).toContain("group=test");
  });

  it("keeps the product navigation in the requested order", () => {
    expect(NAVIGATION.map((item) => item.label)).toEqual([
      "今日工作台",
      "日报作品",
      "群聊管理",
      "运行任务",
      "消息归档",
      "设置",
    ]);
  });
});
