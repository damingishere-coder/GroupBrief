import { expect, test } from "@playwright/test";

test("首页可保存发送时间，失败保留输入且暂停日期不变", async ({ page }) => {
  let time = "08:30";
  let fail = true;
  const writes: unknown[] = [];
  await page.route("**/api/**", async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    let body: unknown = {};
    if (path === "/api/settings") {
      if (req.method() === "PUT") {
        writes.push(req.postDataJSON());
        if (fail) return route.fulfill({ status: 500, body: "failed" });
        time = req.postDataJSON().values.schedule_send_time;
        body = { ok: true };
      } else body = { schedule_send_time: time, schedule_send_skip_dates: "2026-09-23" };
    } else if (path === "/api/v2/dashboard") {
      body = { cards: [], counts: { pending: 0, generated: 0, sent: 0, failed: 0, held: 0 }, should_run: true };
    } else if (path.includes("health")) body = { checks: {}, warnings: [] };
    else if (path.includes("ready")) body = { ready: true, checks: {} };
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(body) });
  });
  await page.goto("/#/");
  await page.getByRole("button", { name: "发送时间", exact: true }).click();
  await expect(page.getByLabel("每日发送时间")).toHaveValue("08:30");
  await page.getByLabel("每日发送时间").fill("10:00");
  await page.getByRole("button", { name: "保存发送时间", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("保存失败");
  await expect(page.getByLabel("每日发送时间")).toHaveValue("10:00");
  fail = false;
  await page.getByRole("button", { name: "保存发送时间", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(writes).toEqual([{ values: { schedule_send_time: "10:00" } }, { values: { schedule_send_time: "10:00" } }]);
  await page.getByRole("button", { name: "发送时间", exact: true }).click();
  await expect(page.getByLabel("每日发送时间")).toHaveValue("10:00");
  await expect(page.getByText("已暂停发送的日报日期：", { exact: false })).toContainText("2026-09-23");
});
