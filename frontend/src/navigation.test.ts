import { beforeEach, describe, expect, it } from "vitest";

import { NAVIGATION, navigateToHash, routeFromLocation } from "./navigation";

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

  it("keeps the product navigation in the requested order", () => {
    expect(NAVIGATION.map((item) => item.label)).toEqual([
      "总览",
      "当日群报",
      "群聊与任务",
      "记录与归档",
      "设置中心",
    ]);
  });
});
