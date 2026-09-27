import { expect, test } from "@playwright/test";

for (const scenario of ["resting", "one-off", "unavailable"]) {
  test(`China workday schedule explains ${scenario}`, async ({ page }) => {
    const resting = scenario === "resting";
    const blocked = scenario === "unavailable";
    await page.route("**/api/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      let body: unknown = {};
      if (path === "/api/v2/dashboard") {
        body = {
          today: "2026-09-28", should_run: !resting && !blocked,
          period_start: "2026-09-25 00:00:00", period_end: "2026-09-27 23:59:59",
          enabled_groups: 0, cards: [], counts: {pending: 0, generated: 0, sent: 0, failed: 0, held: 0},
          calendar_error: blocked ? "缺少 2027 年中国工作日日历" : "",
          calendar: {year: 2026, source: "holiday-cn（国务院公告结构化副本）"},
          schedule_override_id: scenario === "one-off" ? "mid-autumn-20260928" : "",
          daily_status: {overall_status: resting ? "resting" : blocked ? "blocked" : "not_started", summary: {}},
          runtime: {overall_status: resting ? "resting" : blocked ? "blocked" : "not_started", nodes: [], groups: [], summary: {},
            scheduler: {next_generate_at: "2026-09-28T00:15:00+08:00", next_send_at: "2026-09-28T10:00:00+08:00"}},
        };
      } else if (path.includes("health")) body = {checks: {}, warnings: []};
      else if (path.includes("ready")) body = {ready: true, checks: {}};
      await route.fulfill({contentType: "application/json", body: JSON.stringify(body)});
    });
    await page.goto("/#/");
    if (resting) {
      await expect(page.locator(".studio-hero-copy").getByText("休息，下一工作日运行", {exact: false})).toBeVisible();
      await expect(page.getByText("下次生成：", {exact: false})).toContainText("2026-09-28 10:00");
    } else if (blocked) {
      await expect(page.getByText("日历不可用：", {exact: false})).toContainText("缺少 2027");
    } else {
      await expect(page.getByText("本次临时安排", {exact: false})).toContainText("2026-09-25 — 2026-09-27");
    }
  });
}
