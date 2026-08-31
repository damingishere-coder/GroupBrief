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
  const groups = [
    { id: 7, display_name: "测试群", enabled: true, image_theme: "ai_free", image_theme_custom: "" },
    { id: 9, display_name: "停用群", enabled: false, image_theme: "random_preset", image_theme_custom: "" },
    { id: 10, display_name: "失败群", enabled: true, image_theme: "ai_free", image_theme_custom: "" },
  ].map((group) => ({
    ...group,
    wechat_group_id: `wx-${group.id}`,
    wechat_group_name: group.display_name,
    provider_preference: "default",
    created_at: "",
    updated_at: "",
    schedule_rule: "daily",
    send_time: "08:30",
    summary_provider: "",
    prompt_provider: "",
    summary_model: "default",
    prompt_model: "default",
    image_enabled: true,
    send_target: group.display_name,
    effective_send_target: group.display_name,
    send_target_mode: "manual" as const,
    ranking_template: "default",
    ranking_count_policy: "all_messages" as const,
    sender_name_policy: "resolved" as const,
    image_prompt_template: "default",
    has_image_prompt_override: false,
    wechat_send_enabled: false,
  }));
  let batchAttempt = 0;
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    calls.push(`${request.method()} ${path}`);

    if (path === "/api/groups") {
      return json(route, groups);
    }
    if (path === "/api/groups/batch/image-theme") {
      batchAttempt += 1;
      const body = request.postDataJSON() as { group_ids: number[]; image_theme: string; image_theme_custom: string };
      const failedIds = batchAttempt === 1 && body.group_ids.includes(10) ? [10] : [];
      const successIds = body.group_ids.filter((groupId) => !failedIds.includes(groupId));
      for (const group of groups) {
        if (successIds.includes(group.id)) {
          group.image_theme = body.image_theme;
          group.image_theme_custom = body.image_theme === "custom" ? body.image_theme_custom : "";
        }
      }
      return json(route, {
        status: failedIds.length ? (successIds.length ? "partial" : "failed") : "success",
        requested_count: body.group_ids.length,
        success: successIds.map((groupId) => ({ group_id: groupId, group_name: groups.find((group) => group.id === groupId)?.display_name || `群 ${groupId}` })),
        failed: failedIds.map((groupId) => ({ group_id: groupId, code: "DATABASE_SAVE_FAILED", reason: "数据库保存失败，请重试" })),
      });
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
      calls.push(`RUN_QUERY ${url.search}`);
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

  await page.getByPlaceholder("搜索群名", { exact: true }).fill("不存在");
  await expect(page.getByText("显示 0 / 2 条")).toBeVisible();
  expect(calls).toEqual(expect.arrayContaining([
    "GET /api/groups",
    "GET /api/v2/image-themes",
    "GET /api/v2/templates/image_prompt/default",
    "GET /api/v2/runs",
  ]));
});

test("AI 图片默认筛选上海当天，清空日期后请求全部历史", async ({ page }) => {
  const calls = await installFakeApi(page);
  const today = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
  await page.goto("/#/images");

  await expect.poll(() => calls.filter((call) => call.startsWith("RUN_QUERY ")).length).toBeGreaterThan(0);
  expect(calls.find((call) => call.startsWith("RUN_QUERY "))).toBe(`RUN_QUERY ?run_date=${today}`);

  const before = calls.filter((call) => call.startsWith("RUN_QUERY ")).length;
  await page.getByLabel("运行日期").fill("");
  await expect.poll(() => calls.filter((call) => call.startsWith("RUN_QUERY ")).length).toBeGreaterThan(before);
  expect(calls.filter((call) => call.startsWith("RUN_QUERY ")).at(-1)).toBe("RUN_QUERY ");
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

  const triggerCopy = page.locator(".image-theme-picker-trigger .image-theme-trigger-copy").first();
  await expect(triggerCopy).toHaveCSS("flex-direction", "column");
  const titleBox = await triggerCopy.locator("b").boundingBox();
  const subtitleBox = await triggerCopy.locator("small").boundingBox();
  expect(titleBox).not.toBeNull();
  expect(subtitleBox).not.toBeNull();
  expect(subtitleBox!.y).toBeGreaterThan(titleBox!.y);
});

test("多群风格支持全选清空、部分失败并只重试失败群", async ({ page }) => {
  const calls = await installFakeApi(page);
  await page.goto("/#/images");

  await page.getByRole("button", { name: /AI 自由发挥/ }).first().click();
  const dialog = page.getByRole("dialog", { name: "风格中心" });
  await dialog.getByRole("tab", { name: /预设风格/ }).click();
  await dialog.getByRole("button", { name: /分层纸艺插画/ }).first().click();
  await dialog.getByRole("button", { name: "使用这个风格" }).click();
  await expect(page.getByText("已确认：分层纸艺插画")).toBeVisible();

  await page.getByRole("button", { name: "全选当前列表" }).click();
  await expect(page.getByText("已选 3 个")).toBeVisible();
  await page.getByRole("button", { name: "清空" }).click();
  await expect(page.getByText("已选 0 个")).toBeVisible();

  await page.getByRole("checkbox", { name: /测试群/ }).check();
  await page.getByRole("checkbox", { name: /停用群/ }).check();
  await page.getByRole("checkbox", { name: /失败群/ }).check();
  await page.getByRole("button", { name: "应用到 3 个群" }).click();

  await expect(page.getByText("部分群保存成功")).toBeVisible();
  await expect(page.getByText("失败群已保留为选中状态")).toBeVisible();
  await expect(page.getByRole("checkbox", { name: /测试群/ })).not.toBeChecked();
  await expect(page.getByRole("checkbox", { name: /停用群/ })).not.toBeChecked();
  await expect(page.getByRole("checkbox", { name: /失败群/ })).toBeChecked();
  expect(calls.filter((call) => call === "PUT /api/groups/batch/image-theme")).toHaveLength(1);

  await page.getByRole("button", { name: "重试失败群" }).click();
  await expect(page.getByText("全部保存成功")).toBeVisible();
  await expect(page.getByText("已选 0 个")).toBeVisible();
  expect(calls.filter((call) => call === "PUT /api/groups/batch/image-theme")).toHaveLength(2);
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

test("风格中心桌面尺寸保持在 960×720 内", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await installFakeApi(page);
  await page.goto("/#/images");

  await page.getByRole("button", { name: /AI 自由发挥/ }).first().click();
  const box = await page.getByRole("dialog", { name: "风格中心" }).boundingBox();
  expect(box).not.toBeNull();
  expect(box!.width).toBeLessThanOrEqual(960);
  expect(box!.height).toBeLessThanOrEqual(720);
});
