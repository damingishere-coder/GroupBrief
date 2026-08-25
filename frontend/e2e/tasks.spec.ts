import { expect, test, type Page, type Route } from "@playwright/test";

const runDate = "2026-08-25";

async function json(route: Route, body: unknown) {
  await route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

test("任务中心使用批量 files 且不再逐条请求运行详情", async ({ page }) => {
  const calls: string[] = [];
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    calls.push(`${request.method()} ${url.pathname}${url.search}`);

    if (url.pathname === "/api/system/ready") {
      return json(route, {
        ready: true,
        scheduler_owner: "windows",
        scheduler_active: false,
        checks: { database: { ok: true, status: "OK", detail: "Fake DB" } },
      });
    }
    if (url.pathname === "/api/v2/runs") {
      expect(url.searchParams.get("include_files")).toBe("true");
      return json(route, {
        runs: [{
          group_name: "测试群",
          group_id: 7,
          run_date: runDate,
          status: "PROMPT_READY",
          updated_at: `${runDate}T08:00:00`,
          files: ["messages.json", "run.json"],
        }],
        total: 1,
      });
    }
    if (url.pathname === "/api/v2/system/recovery") {
      return json(route, {
        incomplete: [],
        integrity: [{ group_name: "测试群", run_date: runDate, status: "PROMPT_READY", missing: [], ok: true }],
      });
    }
    throw new Error(`E2E 出现未拦截 API：${request.method()} ${url.pathname}`);
  });

  await page.goto("/#/tasks");

  await expect(page.getByRole("heading", { name: "任务中心" })).toBeVisible();
  await expect(page.getByText("messages.json", { exact: true })).toBeVisible();
  expect(calls.filter((call) => call.startsWith("GET /api/v2/runs/"))).toEqual([]);
  expect(calls.some((call) => call.startsWith("GET /api/v2/system/health"))).toBe(false);
});

test("聊天记录使用批量 files 且只读取选中的消息文件", async ({ page }) => {
  const calls: string[] = [];
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    calls.push(`${request.method()} ${url.pathname}${url.search}`);

    if (url.pathname === "/api/v2/runs") {
      expect(url.searchParams.get("include_files")).toBe("true");
      return json(route, {
        runs: [{
          group_name: "测试群",
          run_date: runDate,
          status: "SENT",
          files: ["messages.json", "run.json"],
        }],
        total: 1,
      });
    }
    if (url.pathname.endsWith(`/2026-08-25/messages.json`)) {
      return json(route, [{
        message_id: "m1",
        group_id: "wx-test",
        group_name: "测试群",
        sender_id: "u1",
        sender_name: "小明",
        timestamp: `${runDate}T08:00:00`,
        message_type: "text",
        content: "Fake 归档消息",
      }]);
    }
    throw new Error(`E2E 出现未拦截 API：${request.method()} ${url.pathname}`);
  });

  await page.goto("/#/messages");

  await expect(page.getByRole("heading", { name: "聊天记录" })).toBeVisible();
  await expect(page.getByText("Fake 归档消息", { exact: true })).toBeVisible();
  expect(calls.filter((call) => call.startsWith("GET /api/v2/runs/"))).toEqual([]);
  expect(calls.filter((call) => call.endsWith("/messages.json"))).toHaveLength(1);
});
