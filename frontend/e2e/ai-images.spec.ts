import { expect, test, type Page, type Route } from "@playwright/test";

const runDate = "2026-08-25";
const secondaryGroup = "第二测试群";

function topicSelection(prefix: string) {
  const candidates = Array.from({ length: 4 }, (_, index) => {
    const rank = index + 1;
    return {
      topic_id: `${prefix}-topic-${rank}`,
      rank,
      title: `${prefix}候选 ${rank}`,
      summary: `${prefix}候选摘要 ${rank}`,
      evidence_message_count: 8 - index,
      participant_count: 4,
      duration_minutes: 12,
      score_reason: `${prefix}评分原因 ${rank}`,
      selected: rank <= 2,
      scores: {
        discussion: 8,
        participation: 7,
        comedy: 9,
        group_recognition: 8,
        visual: 7,
        continuity: 6,
        total: 92 - index,
      },
    };
  });
  return {
    topic_selection_version: "5.0",
    candidate_count: candidates.length,
    selected_count: 2,
    selected_topic_ids: candidates.slice(0, 2).map((candidate) => candidate.topic_id),
    candidates,
  };
}

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
      return json(route, { themes: [
        { key: "ai_free", label: "AI 自由发挥", description: "不注入预设风格", kind: "mode", category: "模式", swatches: [], variation_count: 1, preview_url: "" },
        { key: "random_preset", label: "每日随机", description: "每天稳定随机", kind: "mode", category: "模式", swatches: [], variation_count: 352, preview_url: "" },
        { key: "custom", label: "指定风格", description: "自定义描述", kind: "mode", category: "模式", swatches: [], variation_count: 1, preview_url: "" },
        { key: "paper_cut_layered", label: "分层纸艺插画", description: "纤维纸与柔和投影", kind: "preset", category: "立体与手作", swatches: ["#63B3ED", "#F6C453", "#E34D3B"], variation_count: 16, preview_url: "/assets/image-theme-previews/paper_cut_layered.webp" },
      ] });
    }
    if (path === "/api/v2/image-themes/resolve") {
      const body = request.postDataJSON() as { image_theme?: string; image_theme_custom?: string };
      return json(route, { requested_key: body.image_theme, display_name: body.image_theme === "custom" ? body.image_theme_custom : "分层纸艺插画", theme_text: "测试风格约束", prompt: "测试 Prompt", style_signature: "test", style_seed: "test" });
    }
    if (path === "/api/v2/templates/image_prompt/default") {
      return json(route, { name: "default", content: "{{group_name}} {{image_theme}}" });
    }
    if (path === "/api/v2/runs") {
      return json(route, { runs: [
        { group_name: "测试群", group_id: 7, run_date: runDate, status: "PROMPT_READY", updated_at: `${runDate}T08:00:00`, image_regen_status: "idle" },
        { group_name: secondaryGroup, group_id: 8, run_date: runDate, status: "PROMPT_READY", updated_at: `${runDate}T07:30:00`, image_regen_status: "idle" },
      ], total: 2 });
    }
    const runPath = path.match(/^\/api\/v2\/runs\/([^/]+)\/(\d{4}-\d{2}-\d{2})(\/prompt)?$/);
    if (runPath) {
      const groupName = decodeURIComponent(runPath[1]);
      const groupId = groupName === secondaryGroup ? 8 : 7;
      if (runPath[3]) {
        return json(route, { group_name: groupName, run_date: runDate, content: groupName === "测试群" ? "测试 Prompt" : "第二测试 Prompt", revision: "r1", has_original: true, image_theme: "ai_free", image_theme_custom: "", prompt_edited_at: "", topic_selection: topicSelection(groupName) });
      }
      return json(route, { run: { group_name: groupName, group_id: groupId, run_date: runDate, status: "PROMPT_READY", updated_at: `${runDate}T08:00:00`, image_regen_status: "idle" }, files: [] });
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
  await expect(page.getByText("显示 2 / 2 条")).toBeVisible();

  await page.getByPlaceholder("搜索群名").fill("不存在");
  await expect(page.getByText("显示 0 / 2 条")).toBeVisible();
  expect(calls).toEqual(expect.arrayContaining([
    "GET /api/groups",
    "GET /api/v2/image-themes",
    "GET /api/v2/templates/image_prompt/default",
    "GET /api/v2/runs",
  ]));
});

test("选题评分默认显示前两项，可展开收起并在切换运行时重置", async ({ page }) => {
  await installFakeApi(page);
  await page.goto("/#/images");

  const scoreCard = page.getByRole("region", { name: "选题评分" });
  await expect(scoreCard.locator(".ai-images-topic-score-item")).toHaveCount(2);
  await expect(scoreCard.getByText("测试群候选 1", { exact: true })).toBeVisible();
  await expect(scoreCard.getByText("测试群候选 3", { exact: true })).toHaveCount(0);

  const expandButton = scoreCard.getByRole("button", { name: "展开其余 2 个" });
  await expect(expandButton).toHaveAttribute("aria-expanded", "false");
  await expect(expandButton).toHaveAttribute("aria-controls", /.+/);
  await expandButton.click();
  await expect(scoreCard.locator(".ai-images-topic-score-item")).toHaveCount(4);
  await expect(scoreCard.getByRole("button", { name: "收起至 2 个" })).toHaveAttribute("aria-expanded", "true");

  await scoreCard.getByRole("button", { name: "收起至 2 个" }).click();
  await expect(scoreCard.locator(".ai-images-topic-score-item")).toHaveCount(2);

  await scoreCard.getByRole("button", { name: "展开其余 2 个" }).click();
  await page.locator(".ai-images-run-item").filter({ hasText: secondaryGroup }).click();
  await expect(page.getByRole("heading", { name: `${secondaryGroup} · ${runDate}` })).toBeVisible();
  await expect(scoreCard.locator(".ai-images-topic-score-item")).toHaveCount(2);
  await expect(scoreCard.getByText(`${secondaryGroup}候选 1`, { exact: true })).toBeVisible();
  await expect(scoreCard.getByRole("button", { name: "展开其余 2 个" })).toHaveAttribute("aria-expanded", "false");
});

test("风格中心保留草稿，取消不应用，确认后一次提交", async ({ page }) => {
  const calls = await installFakeApi(page);
  await page.goto("/#/images");

  await page.getByRole("button", { name: /AI 自由发挥/ }).first().click();
  const dialog = page.getByRole("dialog", { name: "风格中心" });
  await expect(dialog.getByRole("tab")).toHaveCount(4);
  await dialog.getByRole("tab", { name: "预设风格" }).click();
  await dialog.getByRole("button", { name: /分层纸艺插画/ }).click();
  await expect(dialog.getByRole("img", { name: /分层纸艺插画/ })).toBeVisible();
  await dialog.getByRole("button", { name: "取消" }).click();
  await expect(page.getByRole("button", { name: /AI 自由发挥/ }).first()).toBeVisible();

  await page.getByRole("button", { name: /AI 自由发挥/ }).first().click();
  await dialog.getByRole("tab", { name: "自定义描述" }).click();
  await dialog.getByPlaceholder(/低饱和黏土摄影/).fill("低饱和黏土摄影");
  await dialog.getByRole("button", { name: "使用这个风格" }).click();
  await expect(page.getByRole("button", { name: /指定风格/ }).first()).toBeVisible();
  expect(calls.filter((call) => call === "POST /api/v2/image-themes/resolve")).toHaveLength(1);
});

test("风格中心在窄屏保持可滚动且操作按钮可达", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await installFakeApi(page);
  await page.goto("/#/images");

  await page.getByRole("button", { name: /AI 自由发挥/ }).first().click();
  const dialog = page.getByRole("dialog", { name: "风格中心" });
  await dialog.getByRole("tab", { name: "预设风格" }).click();
  await expect(dialog.getByRole("img", { name: /分层纸艺插画/ })).toBeVisible();
  await expect(dialog.getByRole("button", { name: "取消" })).toBeVisible();
  await expect(dialog.getByRole("button", { name: "使用这个风格" })).toBeVisible();

  const box = await dialog.boundingBox();
  expect(box).not.toBeNull();
  expect(box!.x).toBeGreaterThanOrEqual(0);
  expect(box!.width).toBeLessThanOrEqual(390);
  expect(box!.height).toBeLessThanOrEqual(844);
});
