import { expect, test } from "@playwright/test";

for (const width of [1920, 1280, 820, 390]) {
  test(`${width}px 群聊列表支持筛选、展开信息和取消回收且不溢出`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1066 });
    const writes: string[] = [];
    await page.route("**/api/**", async route => {
      const request = route.request();
      const path = new URL(request.url()).pathname;
      if (request.method() !== "GET") writes.push(`${request.method()} ${path}`);
      const body = path === "/api/groups" ? [
        { id: 7, display_name: "长群名测试：一起分享工作与生活中的灵感", wechat_group_name: "长群名测试：一起分享工作与生活中的灵感", wechat_group_id: "test@chatroom", enabled: true, image_enabled: true, schedule_rule: "workdays_daily_monday_weekly", send_time: "08:30", effective_send_target: "长群名测试：一起分享工作与生活中的灵感", updated_at: "2026-09-07T08:00:00" },
        { id: 8, display_name: "停用测试群", wechat_group_id: "disabled@chatroom", enabled: false, image_enabled: false, schedule_rule: "daily_previous_day", send_time: "09:00", updated_at: "2026-09-07T08:00:00" },
      ] : path === "/api/system/ready" ? { ready: true, checks: {} } : {};
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
    });

    await page.goto("/#/groups");
    const cards = page.locator(".studio-group-card");
    await expect(cards).toHaveCount(2);
    await expect(cards.first().getByText("08:30", { exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.getByRole("tab", { name: "已停用", exact: true }).click();
    await expect(cards).toHaveCount(1);
    await expect(cards.getByRole("heading")).toHaveText("停用测试群");
    await page.getByRole("tab", { name: "全部", exact: true }).click();
    await page.getByRole("textbox", { name: "搜索群聊", exact: true }).fill("test@chatroom");
    await expect(cards).toHaveCount(1);
    await cards.locator("summary").click();
    await expect(cards.getByText("微信 ID：test@chatroom", { exact: true })).toBeVisible();
    await cards.getByRole("button", { name: /^删除 / }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await page.getByRole("button", { name: "取消", exact: true }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    expect(writes).toEqual([]);
  });
}
