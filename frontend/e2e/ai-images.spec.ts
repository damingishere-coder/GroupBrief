import { expect, test, type Page, type Route } from "@playwright/test";

const runDate = "2026-08-25";

async function json(route: Route, body: unknown) {
  await route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

async function installFakeApi(page: Page) {
  const calls: string[] = [];
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    calls.push(`${request.method()} ${path}`);

    if (path === "/api/groups") {
      return json(route, [{
        id: 7,
        display_name: "测试群",
        wechat_group_id: "wx-test",
        wechat_group_name: "测试群",
        enabled: true,
        provider_preference: "default",
        created_at: "",
        updated_at: "",
        schedule_rule: "daily",
        send_time: "08:30",
        summary_model: "default",
        prompt_model: "default",
        image_enabled: true,
        send_target: "测试群",
        effective_send_target: "测试群",
        send_target_mode: "manual",
        ranking_template: "default",
        image_prompt_template: "default",
        image_theme: "ai_free",
        image_theme_custom: "",
        has_image_prompt_override: false,
        wechat_send_enabled: false,
      }]);
    }
    if (path === "/api/v2/image-themes") {
      return json(route, { themes: [{ key: "ai_free", label: "AI 自由发挥", description: "", kind: "mode", category: "mode", swatches: [], variation_count: 1 }] });
    }
    if (path === "/api/v2/templates/image_prompt/default") {
      return json(route, { name: "default", content: "{{group_name}} {{image_theme}}" });
    }
    if (path === "/api/v2/runs") {
      return json(route, { runs: [{ group_name: "测试群", group_id: 7, run_date: runDate, status: "PROMPT_READY", updated_at: `${runDate}T08:00:00`, image_regen_status: "idle" }], total: 1 });
    }
    if (path === `/api/v2/runs/%E6%B5%8B%E8%AF%95%E7%BE%A4/${runDate}` || path === `/api/v2/runs/测试群/${runDate}`) {
      return json(route, { run: { group_name: "测试群", group_id: 7, run_date: runDate, status: "PROMPT_READY", updated_at: `${runDate}T08:00:00`, image_regen_status: "idle" }, files: [] });
    }
    if (path === `/api/v2/runs/%E6%B5%8B%E8%AF%95%E7%BE%A4/${runDate}/prompt` || path === `/api/v2/runs/测试群/${runDate}/prompt`) {
      return json(route, { group_name: "测试群", run_date: runDate, content: "测试 Prompt", revision: "r1", has_original: true, image_theme: "ai_free", image_theme_custom: "", prompt_edited_at: "" });
    }
    throw new Error(`E2E 出现未拦截 API：${request.method()} ${path}`);
  });
  return calls;
}

test("AI 图片工作台通过 Fake API 加载目录、运行与详情", async ({ page }) => {
  const calls = await installFakeApi(page);
  await page.goto("/#/images");

  await expect(page.getByRole("heading", { name: "设置群聊生图风格" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "运行记录" })).toBeVisible();
  await expect(page.getByRole("heading", { name: `测试群 · ${runDate}` })).toBeVisible();
  await expect(page.getByText("测试 Prompt", { exact: true })).toBeVisible();
  await expect(page.getByText("显示 1 / 1 条")).toBeVisible();

  await page.getByPlaceholder("搜索群名").fill("不存在");
  await expect(page.getByText("显示 0 / 1 条")).toBeVisible();
  expect(calls).toEqual(expect.arrayContaining([
    "GET /api/groups",
    "GET /api/v2/image-themes",
    "GET /api/v2/templates/image_prompt/default",
    "GET /api/v2/runs",
  ]));
});
