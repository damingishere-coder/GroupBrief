import { expect, test, type Page, type Route } from "@playwright/test";

function shanghaiToday(): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
}

async function installRunsApi(page: Page) {
  const urls: string[] = [];
  await page.route("**/api/v2/runs**", async (route: Route) => {
    urls.push(route.request().url());
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ runs: [] }),
    });
  });
  return urls;
}

for (const [name, path] of [
  ["聊天记录", "/#/messages"],
  ["排行榜", "/#/ranking"],
] as const) {
  test(`${name}默认筛选上海当天，并可清空查看历史`, async ({ page }) => {
    const requests = await installRunsApi(page);
    await page.goto(path);

    const dateInput = page.getByLabel("运行日期");
    const expected = shanghaiToday();
    await expect(dateInput).toHaveValue(expected);
    await expect.poll(() => requests.some((url) => new URL(url).searchParams.get("run_date") === expected)).toBe(true);

    await dateInput.fill("");
    await expect.poll(() => {
      const latest = requests.at(-1);
      return latest ? new URL(latest).searchParams.has("run_date") : true;
    }).toBe(false);
  });
}
