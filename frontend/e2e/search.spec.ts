import { expect, test } from '@playwright/test';

for (const width of [1440,390]) {
  test(`群聊搜索到原文证据 ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    const message = { id: 42, sender_name: '甲', content: '🐮 推荐苹果手机 <script>alert(1)</script>', sent_at: '2026-09-01T04:00:00Z', validation_state: 'valid' };
    const requests: string[] = [];
    await page.route('**/api/**', async route => {
      const url = new URL(route.request().url()); requests.push(url.pathname);
      const json = (body: unknown) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
      if (url.pathname === '/api/system/ready') return json({ ready: true, checks: {} });
      if (url.pathname === '/api/groups') return json([{ id: 1, display_name: '测试群' }]);
      if (url.pathname === '/api/v2/search') {
        expect(url.searchParams.get('q')).toBe('手机');
        return json({ items: [{ type: 'message', ...message, group_id: 1, snippet: [{ text: '🐮 推荐苹果', match: false }, { text: '手机', match: true }, { text: ' <script>alert(1)</script>', match: false }] }], next_cursor: null, warnings: [], index_version: 2, ai_calls: 0 });
      }
      if (url.pathname === '/api/v2/messages/42/context') return json({ before: [], message, after: [] });
      if (url.pathname === '/api/v2/messages/42/sources') return json({ items: [{ batch_id: 1, row_ordinal: 0, source_locator: '群/日期/messages.json', raw_record: { content: message.content } }], next_offset: null });
      throw new Error(`未拦截 API ${url.pathname}`);
    });
    await page.goto('/#/search');
    await page.getByRole('textbox', { name: '搜索关键词' }).fill('手机');
    await page.getByRole('button', { name: '搜索', exact: true }).click();
    await expect(page.locator('mark')).toHaveText('手机');
    await expect(page.getByText(' <script>alert(1)</script>', { exact: true })).toBeVisible();
    await page.getByRole('button', { name: '查看来源 #42' }).click();
    const drawer = page.getByRole('complementary', { name: '来源消息' });
    await expect(drawer.getByText(message.content, { exact: true })).toBeVisible();
    await page.reload();
    await expect(drawer).toBeVisible();
    await drawer.getByRole('button', { name: '关闭来源' }).click();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    expect(requests.some(p => /generate|send|ai\//.test(p))).toBe(false);
  });
}
