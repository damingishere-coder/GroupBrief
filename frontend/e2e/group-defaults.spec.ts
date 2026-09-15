import { expect, test } from "@playwright/test";

test("新增群展示继承配置并保留用户显式关闭发送", async ({ page }) => {
  const defaults = {
    display_name: "", wechat_group_id: "", wechat_group_name: "", enabled: true,
    provider_preference: "", schedule_rule: "workdays_daily_monday_weekly", send_time: "09:15",
    summary_provider: "codex", prompt_provider: "codex", summary_model: "gpt-6-astra", prompt_model: "gpt-6-astra",
    strict_image_fact_check: true, image_enabled: true, send_target: "", ranking_template: "default",
    ranking_count_policy: "all_messages", sender_name_policy: "wechat_data_analysis",
    image_prompt_template: "default", image_theme: "ai_free", image_prompt_override: "", wechat_send_enabled: true,
  };
  let saved: Record<string, unknown> | undefined;
  await page.route("**/api/**", async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    let body: unknown;
    if (path === "/api/groups/defaults") body = defaults;
    else if (path === "/api/groups" && request.method() === "POST") {
      saved = request.postDataJSON();
      body = { id: 30 };
    } else if (path === "/api/groups") body = [{ id: 30, ...saved }];
    else if (path.startsWith("/api/v2/templates/")) body = { templates: ["default"] };
    else if (path === "/api/system/providers") body = { catalog: { history: [], ai: [
      { provider: "codex", label: "Codex", available: true, models: ["gpt-6-astra"], capabilities: ["summary", "prompt"] },
    ] } };
    else if (path === "/api/system/ready") body = { ready: true, checks: {} };
    else throw new Error(`Unexpected API: ${request.method()} ${path}`);
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });

  await page.goto("/#/groups/new");
  await expect(page.getByRole("heading", { name: "新增群聊", exact: true })).toBeVisible();
  await expect(page.getByLabel("统计周期规则")).toHaveValue("workdays_daily_monday_weekly");
  await expect(page.getByLabel("允许微信自动发送")).toBeChecked();
  await expect(page.getByLabel("严格核对图片事实")).toBeChecked();
  await expect(page.getByLabel("排行榜名称来源")).toHaveValue("wechat_data_analysis");
  await expect(page.locator("#send-target")).toHaveValue("");
  await page.locator("#display-name").fill("新群");
  await page.locator("#wechat-group-id").fill("new@chatroom");
  await page.getByLabel("允许微信自动发送").uncheck();
  await page.getByRole("button", { name: "保存配置", exact: true }).first().click();
  await expect(page.getByRole("heading", { name: "群配置详情", exact: true })).toBeVisible();
  expect(saved).toMatchObject({
    display_name: "新群", wechat_group_id: "new@chatroom", wechat_group_name: "新群",
    schedule_rule: "workdays_daily_monday_weekly", send_time: "09:15",
    wechat_send_enabled: false, strict_image_fact_check: true, send_target: "",
  });
});

test("新增默认配置加载失败时不能用旧默认值保存", async ({ page }) => {
  await page.route("**/api/**", async route => {
    const path = new URL(route.request().url()).pathname;
    const failed = path === "/api/groups/defaults";
    const body = failed ? { detail: "配置读取失败" } : path === "/api/system/providers"
      ? { catalog: { history: [], ai: [] } } : { templates: [], ready: true, checks: {} };
    await route.fulfill({ status: failed ? 503 : 200, contentType: "application/json", body: JSON.stringify(body) });
  });
  await page.goto("/#/groups/new");
  await expect(page.getByText(/加载新增群默认配置失败/)).toBeVisible();
  await expect(page.getByRole("button", { name: "保存配置", exact: true })).toHaveCount(0);
});
