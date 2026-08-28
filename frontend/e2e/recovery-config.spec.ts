import { expect, test, type Route } from "@playwright/test";

async function json(route: Route, body: unknown) {
  await route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

test("390px 窄屏可核对 48 小时外恢复清单且确认接口不包含发送参数", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  let confirmBody: Record<string, unknown> | null = null;
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname === "/api/system/ready") {
      return json(route, { ready: true, checks: { database: { ok: true } } });
    }
    if (url.pathname === "/api/v2/runs") {
      return json(route, { runs: [], total: 0 });
    }
    if (url.pathname === "/api/v2/system/recovery") {
      return json(route, { incomplete: [], integrity: [] });
    }
    if (url.pathname === "/api/v2/recovery/backlog" && request.method() === "GET") {
      return json(route, {
        generated_at: "2026-08-27T09:00:00+08:00",
        automatic_recovery_dates: ["2026-08-26", "2026-08-27"],
        lookback_days: 30,
        version: "b".repeat(64),
        items: [{
          run_date: "2026-08-20",
          group_id: 7,
          group_name: "低风险测试群",
          status: "FAILED",
          execution_state: "WAIT_RETRY",
          reason: "IMAGE_GENERATION_FAILED",
          safe_stage: "generation_only",
          recoverable: true,
          manifest_source: "recorded",
          estimated_summary_calls: 1,
          estimated_image_calls: 1,
          updated_at: "2026-08-20T09:00:00+08:00",
        }],
      });
    }
    if (url.pathname === "/api/v2/recovery/confirm" && request.method() === "POST") {
      confirmBody = request.postDataJSON();
      return json(route, {
        status: "success",
        generation_only: true,
        send_invoked: false,
        results: [{ group_name: "低风险测试群", status: "ready_to_send" }],
      });
    }
    throw new Error(`E2E 出现未拦截 API：${request.method()} ${url.pathname}`);
  });

  await page.goto("/#/tasks");
  await expect(page.getByRole("heading", { name: "48 小时外恢复待核对" })).toBeVisible();
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "确认恢复生成（1）" }).click();
  await expect(page.getByText("绝不会发送历史微信", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "确认，仅恢复生成" }).click();
  await expect.poll(() => confirmBody).not.toBeNull();
  expect(confirmBody).toEqual({
    expected_version: "b".repeat(64),
    tasks: [{ run_date: "2026-08-20", group_id: 7 }],
  });
  expect(JSON.stringify(confirmBody)).not.toContain("send");
});

test("群配置只展示后端白名单并支持两种统计规则", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/groups") {
      return json(route, [{
        id: 7,
        display_name: "低风险测试群",
        wechat_group_id: "test@chatroom",
        wechat_group_name: "低风险测试群",
        enabled: true,
        provider_preference: "wechat_data_analysis",
        schedule_rule: "daily_previous_day",
        send_time: "08:30",
        summary_provider: "codex",
        summary_model: "gpt-5.6-sol",
        prompt_provider: "codex",
        prompt_model: "gpt-5.6-sol",
        image_enabled: true,
        send_target: "",
        ranking_template: "default",
        image_prompt_template: "default",
        wechat_send_enabled: false,
      }]);
    }
    if (url.pathname === "/api/v2/templates/ranking") {
      return json(route, { templates: ["default"], previews: {} });
    }
    if (url.pathname === "/api/v2/templates/image_prompt") {
      return json(route, { templates: ["default"] });
    }
    if (url.pathname === "/api/system/providers") {
      return json(route, {
        catalog: {
          history: [
            { provider: "wechat_data_analysis", label: "WeChatDataAnalysis", available: true, capabilities: ["history"] },
            { provider: "wechat_cli", label: "wechat-cli", available: false, capabilities: ["history"] },
          ],
          ai: [
            { provider: "codex", label: "Codex GPT", available: true, models: ["gpt-5.6-sol"], capabilities: ["summary", "prompt"] },
            { provider: "deepseek", label: "DeepSeek", available: false, models: ["deepseek-v4-flash"], capabilities: ["summary", "prompt"] },
          ],
        },
      });
    }
    throw new Error(`E2E 出现未拦截 API：${route.request().method()} ${url.pathname}`);
  });

  await page.goto("/#/groups/7");
  await expect(page.getByRole("heading", { name: "群配置详情" })).toBeVisible();
  await expect(page.getByLabel("统计周期规则")).toHaveValue("daily_previous_day");
  await page.getByLabel("统计周期规则").selectOption("weekday_default");
  await expect(page.getByLabel("统计周期规则")).toHaveValue("weekday_default");
  await expect(page.getByLabel("摘要 Provider").locator("option[value=deepseek]")).toBeDisabled();
  await expect(page.getByLabel("日报 Prompt Provider").locator("option[value=deepseek]")).toBeDisabled();
});
