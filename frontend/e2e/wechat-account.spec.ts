import { expect, test } from "@playwright/test";

test("微信账号必须预览选择并显式绑定，核验不发送消息", async ({ page }) => {
  let bound = false;
  const requests: string[] = [];
  const avatar = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg==";
  await page.route("**/api/**", async (route) => {
    const req = route.request(), path = new URL(req.url()).pathname;
    let body: unknown = {};
    if (req.method() !== "GET") requests.push(path);
    if (path.endsWith("/account/candidates")) body = { candidates: [{ label: "微信窗口 1", ok: true, candidate_id: "first", avatar }, { label: "微信窗口 2", ok: true, candidate_id: "second", avatar }] };
    else if (path.endsWith("/account/binding")) {
      expect(req.postDataJSON()).toEqual({ candidate_id: "second", account_name: "大明同学" });
      bound = true; body = { bound, account_name: "大明同学", avatar, detail: "已保存标准头像" };
    } else if (path.endsWith("/account/verify")) body = { ok: false, detail: "标准头像必须唯一匹配，当前匹配 2 个微信窗口" };
    else if (path.endsWith("/account")) body = { bound, detail: "尚未绑定账号" };
    else if (path.includes("health")) body = { checks: {}, warnings: [] };
    else if (path.includes("startup")) body = { checks: [] };
    else if (path.includes("recovery")) body = { incomplete: [], output_checks: [] };
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(body) });
  });
  await page.goto("/#/settings");
  const card = page.getByRole("article", { name: "微信发送账号" });
  await expect(card.getByText("自动发送已阻止")).toBeVisible();
  await card.getByRole("button", { name: "扫描微信头像" }).click();
  await expect(card.getByRole("button", { name: "确认绑定所选头像" })).toBeDisabled();
  await card.getByRole("radio").nth(1).check();
  await card.getByRole("button", { name: "确认绑定所选头像" }).click();
  await expect(card.getByText("标准头像已保存")).toBeVisible();
  await card.getByRole("button", { name: "仅核验账号" }).click();
  await expect(card.getByRole("status")).toContainText("当前匹配 2 个");
  expect(requests).toEqual(["/api/wechat-sender/account/candidates", "/api/wechat-sender/account/binding", "/api/wechat-sender/account/verify"]);
});
