import { describe, expect, it } from "vitest";

import { runtimeRefreshDelay } from "./dashboardRuntime";

describe("Dashboard runtime refresh policy", () => {
  it("polls active work every three seconds", () => {
    expect(runtimeRefreshDelay("running", { isToday: true, visible: true })).toBe(3_000);
    expect(runtimeRefreshDelay("retry_pending", { isToday: true, visible: true })).toBe(3_000);
  });

  it("checks waiting work every thirty seconds and wakes at 00:15", () => {
    const now = Date.parse("2026-08-29T00:14:55+08:00");
    expect(runtimeRefreshDelay("not_started", {
      isToday: true,
      visible: true,
      scheduledAt: "2026-08-29T00:15:00+08:00",
      now,
    })).toBe(5_000);
    expect(runtimeRefreshDelay("not_started", { isToday: true, visible: true, now })).toBe(30_000);
  });

  it("stops for history and hidden pages", () => {
    expect(runtimeRefreshDelay("running", { isToday: false, visible: true })).toBeNull();
    expect(runtimeRefreshDelay("running", { isToday: true, visible: false })).toBeNull();
  });

  it("keeps observing delivery after generation and manual recovery after failures", () => {
    for (const status of ["partial", "complete", "blocked", "failed", "needs_attention"] as const) {
      expect(runtimeRefreshDelay(status, { isToday: true, visible: true })).toBe(30_000);
    }
  });
});
