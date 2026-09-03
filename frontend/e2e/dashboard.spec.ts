import { expect, test, type Page, type Route } from "@playwright/test";

const runDate = "2026-08-25";

const dashboard = {
  today: runDate,
  should_run: true,
  period_start: "2026-08-24 00:00:00",
  period_end: "2026-08-24 23:59:59",
  enabled_groups: 1,
  counts: { pending: 0, generated: 1, sent: 0, failed: 0, held: 0 },
  next_send: "08:30（测试群）",
  daily_status: { overall_status: "partial", summary: {}, updated_at: "2026-08-25T08:00:00+08:00" },
  runtime: {
    schema_version: 2,
    run_date: runDate,
    run_id: "groupbrief:2026-08-25:test",
    updated_at: "2026-08-25T08:00:00+08:00",
    overall_status: "partial",
    scheduler: {
      scheduled_at: "2026-08-25T00:15:00+08:00",
      generation_started_at: "2026-08-25T00:15:01+08:00",
      generation_completed_at: "2026-08-25T00:20:00+08:00",
      generation_status: "success",
    },
    summary: { configured_group_count: 1 },
    nodes: [
      { id: "scheduler", label: "调度启动", status: "success", completed_groups: 1, total_groups: 1 },
      { id: "data", label: "读取群消息", status: "success", completed_groups: 1, total_groups: 1 },
      { id: "ranking", label: "生成排行榜", status: "success", completed_groups: 1, total_groups: 1 },
      { id: "prompt", label: "摘要与提示词", status: "success", completed_groups: 1, total_groups: 1 },
      { id: "image", label: "生成图片", status: "success", completed_groups: 1, total_groups: 1 },
      { id: "send", label: "等待发送 / 发送完成", status: "pending", completed_groups: 0, total_groups: 1 },
    ],
    groups: [
      {
        group_id: "7",
        group_name: "测试群",
        run_status: "READY_TO_SEND",
        current_node: "send",
        current_node_label: "等待发送 / 发送完成",
        node_status: "pending",
        nodes: [
          { id: "scheduler", label: "调度启动", status: "success" },
          { id: "data", label: "读取群消息", status: "success" },
          { id: "ranking", label: "生成排行榜", status: "success" },
          { id: "prompt", label: "摘要与提示词", status: "success" },
          { id: "image", label: "生成图片", status: "success" },
          { id: "send", label: "等待发送 / 发送完成", status: "pending" },
        ],
        last_error_type: "",
        last_error_summary: "",
        updated_at: "2026-08-25T08:00:00+08:00",
      },
    ],
  },
  cards: [
    {
      group_id: 7,
      group_name: "测试群",
      send_time: "08:30",
      schedule_rule: "daily",
      image_enabled: false,
      ranking_template: "default",
      ranking_count_policy: "all_messages",
      image_prompt_template: "default",
      status: "READY_TO_SEND",
      period_start: "2026-08-24 00:00:00",
      period_end: "2026-08-24 23:59:59",
      message_count: 12,
      speaker_count: 3,
      image_url: "",
      image_status: "",
      image_fallback_level: 0,
      image_fallback_reason: "",
      image_variant: "normal",
      image_delivery_eligible: true,
      ranking_preview: [{ rank: 1, name: "成员甲", count: 8 }],
      ranking_error: "",
      error: "",
      sent_at: "",
      wechat_send_enabled: true,
      send_hold: false,
      send_state: "ready",
      send_hold_reason: "",
      send_error: "",
      send_error_type: "",
      send_unknown_at: "",
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

async function installFakeApi(page: Page, held = false, diagnostic = false) {
  const calls: { path: string; search: string; body: unknown }[] = [];
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const body = request.postDataJSON?.() ?? null;
    calls.push({ path, search: url.search, body });

    if (path === "/api/v2/dashboard") {
      if (diagnostic) {
        return json(route, {
          ...dashboard,
          counts: { ...dashboard.counts, generated: 0, sent: 1 },
          next_send: "",
          cards: dashboard.cards.map((card) => ({
            ...card,
            image_enabled: true,
            status: "SENT",
            sent_at: "2026-08-25T08:36:00+08:00",
            image_url: "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
            image_status: "failed",
            image_fallback_level: 3,
            image_fallback_reason: "PROMPT_FAILED",
            image_variant: "pillow",
            image_delivery_eligible: false,
            error: "Prompt 连续校验失败",
          })),
        });
      }
      return json(route, held ? {
        ...dashboard,
        counts: { ...dashboard.counts, generated: 0, held: 1 },
        cards: dashboard.cards.map((card) => ({ ...card, send_hold: true, send_state: "unknown", send_hold_reason: "SEND_RESULT_UNKNOWN", error: "发送结果需要人工核对" })),
      } : dashboard);
    }
    if (path === "/api/v2/runtime/logs") {
      return json(route, {
        run_date: runDate,
        updated_at: "2026-08-25T08:00:03+08:00",
        truncated: false,
        items: [
          { timestamp: "2026-08-25T00:15:01+08:00", level: "INFO", source: "scheduler", message: "00:15 每日任务已启动" },
          { timestamp: "2026-08-25T00:15:03+08:00", level: "WARNING", source: "provider", message: "测试群数据读取稍慢" },
        ],
      });
    }
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
    if (path === "/api/v2/pipeline/resolve-manual-send") {
      return json(route, { result: { status: "resolved", group_name: "测试群", detail: "已写入人工核对结论；本次操作没有调用微信发送器" } });
    }
    throw new Error(`E2E 出现未拦截 API：${request.method()} ${path}`);
  });
  return calls;
}

test("Dashboard 生成与发送确认只命中 Fake API", async ({ page }) => {
  const calls = await installFakeApi(page);
  await page.goto("/#/dashboard");

  await expect(page.getByRole("heading", { name: "运行总览" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "任务节点" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "运行日志" })).toBeVisible();
  await expect(page.getByText("00:15 每日任务已启动")).toBeVisible();
  await expect(page.getByText("测试群", { exact: true }).first()).toBeVisible();
  await page.getByLabel("运行日期").fill(runDate);

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

test("Dashboard 日志筛选和窄屏布局只使用只读 Fake API", async ({ page }) => {
  const calls = await installFakeApi(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/#/dashboard");

  await expect(page.getByRole("heading", { name: "任务节点" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "运行日志" })).toBeVisible();
  await page.getByLabel("日志来源").selectOption("provider");
  await page.getByLabel("日志级别").selectOption("WARNING");
  await page.getByRole("button", { name: "暂停滚动" }).click();
  await expect(page.getByRole("button", { name: "继续滚动" })).toBeVisible();
  await expect.poll(() => calls.filter((call) => call.path === "/api/v2/runtime/logs").length).toBeGreaterThanOrEqual(3);
  expect(calls.some((call) => call.search.includes("sources=provider") && call.search.includes("levels=WARNING"))).toBe(true);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  expect(calls.some((call) => call.path.startsWith("/api/v2/pipeline/"))).toBe(false);
});

test("Dashboard 人工核对只写状态，不调用发送接口", async ({ page }) => {
  const calls = await installFakeApi(page, true);
  await page.goto("/#/dashboard");
  await page.getByLabel("运行日期").fill(runDate);

  const resolveButton = page.getByRole("button", { name: "人工核对" });
  await resolveButton.click();
  const dialog = page.getByRole("dialog", { name: /核对.*测试群.*发送结果/ });
  await expect(dialog).toContainText("不会再次发送任何内容");
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(resolveButton).toBeFocused();
  await resolveButton.click();
  await dialog.getByRole("button", { name: "确认核对结果" }).click();
  await expect(page.getByText("已写入人工核对结论；本次操作没有调用微信发送器")).toBeVisible();

  const resolved = calls.find((call) => call.path === "/api/v2/pipeline/resolve-manual-send");
  expect(resolved?.body).toEqual({
    group_id: 7,
    run_date: runDate,
    resolution: "all_sent",
    expected_updated_at: "2026-08-25 08:00:00",
  });
  expect(calls.some((call) => call.path === "/api/v2/pipeline/send")).toBe(false);
});

test("Dashboard 对历史已发送诊断图同时显示失败事实且不提供重发", async ({ page }) => {
  const calls = await installFakeApi(page, false, true);
  await page.goto("/#/dashboard");

  await expect(page.getByText("已发送", { exact: true })).toBeVisible();
  await expect(page.getByText("图片生成失败（已发送）", { exact: true })).toBeVisible();
  await expect(page.getByText("诊断图不可发送", { exact: true })).toBeVisible();
  const image = page.getByAltText("测试群 不可发送诊断图");
  await expect(image).toBeVisible();
  expect(await image.evaluate((element) => getComputedStyle(element).objectFit)).toBe("contain");
  await expect(page.getByRole("button", { name: "立即发送" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "人工核对" })).toHaveCount(0);
  expect(calls.some((call) => call.path === "/api/v2/pipeline/send")).toBe(false);
});

test.describe("减少动态效果", () => {
  test.use({ reducedMotion: "reduce" });
  test("页面转场降级后仍到达最终可访问状态", async ({ page }) => {
    await installFakeApi(page);
    await page.goto("/#/dashboard");
    await expect(page.getByRole("heading", { name: "运行总览" })).toBeVisible();
    await expect(page.getByText("成员甲")).toBeVisible();
  });
});
