import { expect, test, type Page, type Route } from "@playwright/test";

const runDate = "2026-08-25";

const dashboard = {
  today: runDate,
  should_run: true,
  period_start: "2026-08-24 00:00:00",
  period_end: "2026-08-24 23:59:59",
  enabled_groups: 1,
  counts: { pending: 0, generated: 1, sent: 0, failed: 0 },
  next_send: "08:30（测试群）",
  cards: [
    {
      group_id: 7,
      group_name: "测试群",
      send_time: "08:30",
      schedule_rule: "daily",
      image_enabled: false,
      ranking_template: "default",
      image_prompt_template: "default",
      status: "READY_TO_SEND",
      period_start: "2026-08-24 00:00:00",
      period_end: "2026-08-24 23:59:59",
      message_count: 12,
      speaker_count: 3,
      image_url: "",
      error: "",
      sent_at: "",
      updated_at: "2026-08-25 08:00:00",
    },
  ],
};

async function json(route: Route, body: unknown) {
  await route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

async function installFakeApi(page: Page) {
  const calls: { path: string; body: unknown }[] = [];
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const body = request.postDataJSON?.() ?? null;
    calls.push({ path, body });

    if (path === "/api/v2/dashboard") return json(route, dashboard);
    if (path === "/api/v2/system/health") {
      return json(route, {
        checks: {
          wechat_sender: { ok: true, status: "OK", detail: "Fake sender，仅用于 E2E" },
        },
      });
    }
    if (path === "/api/v2/pipeline/generate") {
      return json(route, {
        results: [{ status: "ready_to_send", group_name: "测试群", detail: "Fake 生成完成" }],
      });
    }
    if (path === "/api/v2/pipeline/send") {
      return json(route, { result: { status: "sent", group_name: "测试群" } });
    }
    throw new Error(`E2E 出现未拦截 API：${request.method()} ${path}`);
  });
  return calls;
}

test("Dashboard 生成与发送确认只命中 Fake API", async ({ page }) => {
  const calls = await installFakeApi(page);
  await page.goto("/#/dashboard");

  await expect(page.getByRole("heading", { name: "运行总览" })).toBeVisible();
  await expect(page.getByText("测试群", { exact: true }).first()).toBeVisible();
  await page.getByLabel("生成日期").fill(runDate);

  await page.getByRole("button", { name: "立即生成" }).click();
  await expect(page.getByText("生成完成：Fake 生成完成")).toBeVisible();
  const generate = calls.find((call) => call.path === "/api/v2/pipeline/generate");
  expect(generate?.body).toEqual({ group_id: 7, force: true, run_date: runDate });

  await page.getByRole("button", { name: "立即发送" }).click();
  const dialog = page.getByRole("dialog", { name: "确认立即发送" });
  await expect(dialog).toContainText("测试群");
  expect(calls.some((call) => call.path === "/api/v2/pipeline/send")).toBe(false);

  await dialog.getByRole("button", { name: "确认发送" }).click();
  await expect(page.getByText("「测试群」已发送")).toBeVisible();
  const send = calls.find((call) => call.path === "/api/v2/pipeline/send");
  expect(send?.body).toEqual({ group_id: 7, run_date: runDate });
});
