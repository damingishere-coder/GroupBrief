import { expect, test } from '@playwright/test';

for (const width of [1440, 390]) {
  test(`本地建库预览与原消息证据 ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    let imports = 0;
    const message = { id: 42, sender_name: '测试成员', content: '<script>消息原文</script>', sent_at: '2026-09-01T04:00:00Z', validation_state: 'valid' };
    await page.route('**/api/**', async route => {
      const path = new URL(route.request().url()).pathname;
      const reply = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
      if (path === '/api/system/ready') return reply({ ready: true, checks: {} });
      if (path === '/api/v2/knowledge/status') return reply({ available: true, worker_enabled: true, message_count: 1, source_count: 2, coverage: { unverified: 2 } });
      if (path === '/api/v2/knowledge/jobs') return reply({ items: [] });
      if (path === '/api/v2/messages') return reply({ items: [message], next_cursor: null });
      if (path === '/api/v2/messages/42/context') return reply({ before: [], message, after: [] });
      if (path === '/api/v2/messages/42/sources') return reply({ items: [{ batch_id: 1, row_ordinal: 6, source_locator: '测试群/2026-09-02/messages.json', coverage_state: 'unverified', artifact_sha256: 'a'.repeat(64), raw_record: { content: message.content } }], next_offset: null });
      if (path === '/api/v2/knowledge/backfill/preview') return reply({ version: 'a'.repeat(64), filters: {}, items: [{ locator: '测试群/2026-09-02/messages.json', group_id: 1, coverage_state: 'unverified' }], record_count: 1, ai_calls: 0, errors: [] });
      if (path === '/api/v2/knowledge/backfill') { imports++; return reply({ job_id: 1 }, 202); }
      throw new Error(`未拦截 API：${path}`);
    });
    await page.goto('/#/knowledge');
    await page.getByRole('button', { name: '预览历史导入' }).click();
    await expect(page.getByText('找到 1 份归档、1 条来源记录；AI 调用 0 次。')).toBeVisible();
    expect(imports).toBe(0);
    await page.getByRole('button', { name: '确认本地导入' }).click();
    await expect.poll(() => imports).toBe(1);
    await page.getByRole('button', { name: '查看来源 #42' }).click();
    const drawer = page.getByRole('complementary', { name: '来源消息' });
    await expect(drawer.getByText(message.content, { exact: true })).toBeVisible();
    await drawer.getByText('测试群/2026-09-02/messages.json · 第 7 条').click();
    await expect(drawer.locator('pre')).toContainText(message.content);
    expect(await drawer.locator('script').count()).toBe(0);
    const bounds = await drawer.boundingBox();
    expect(bounds!.x).toBeGreaterThanOrEqual(0);
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(width);
    await page.reload();
    await expect(drawer).toBeVisible();
    await drawer.getByRole('button', { name: '关闭来源' }).click();
    await expect(drawer).not.toBeVisible();
  });
}

test('未迁移时知识页给出明确状态，旧任务入口仍可访问', async ({ page }) => {
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    const value = path === '/api/v2/knowledge/status' ? { available: false, reason: '知识库尚未迁移' } : { ready: true, checks: {} };
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(value) });
  });
  await page.goto('/#/knowledge');
  await expect(page.getByRole('heading', { name: '知识库尚未就绪' })).toBeVisible();
  await expect(page.getByRole('tab', { name: '群报任务' })).toBeVisible();
  await expect(page.getByRole('button', { name: '确认本地导入' })).not.toBeVisible();
});
