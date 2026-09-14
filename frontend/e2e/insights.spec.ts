import { expect, test } from '@playwright/test';

test('周度洞察从作品页打开并追溯统计来源', async ({ page }) => {
  const summary = { id: 9, group_id: 1, kind: 'weekly', period_start: '2026-08-30T16:00:00Z', period_end: '2026-09-06T16:00:00Z', revision: 2, status: 'PARTIAL' };
  const message = { id: 42, sender_name: '甲', content: '推荐显示器', sent_at: '2026-09-01T04:00:00Z', validation_state: 'valid' };
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    const json = (body: unknown) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/system/ready') return json({ ready: true, checks: {} });
    if (path === '/api/groups') return json([{ id: 1, display_name: '测试群' }]);
    if (path === '/api/v2/runs') return json({ runs: [], total: 0 });
    if (path === '/api/v2/insights') return json({ items: [summary] });
    if (path === '/api/v2/insights/9') return json({ ...summary, metrics_version: 'period-1',
      coverage: { current: { complete: false }, previous: { complete: false } },
      metrics: { message_count: 10, speaker_count: 2, count_policy: 'all_messages', comparison: {}, members: [], daily_counts: [{ date: '2026-09-01', count: 10, complete: false }], champion: null, inactive_previous_members: [] } });
    if (path === '/api/v2/insights/9/messages') return json({ items: [message], next_offset: null });
    if (path === '/api/v2/messages/42/context') return json({ before: [], message, after: [] });
    if (path === '/api/v2/messages/42/sources') return json({ items: [], next_offset: null });
    throw new Error(`未拦截 API ${path}`);
  });
  await page.goto('/#/images?view=weekly');
  await page.getByRole('button', { name: '查看周报 #9 · v2' }).click();
  await expect(page.getByRole('heading', { name: '本周概况 · v2' })).toBeVisible();
  await expect(page.getByText('数据不足，暂不可比较').first()).toBeVisible();
  await page.getByRole('button', { name: '查看统计来源', exact: true }).click();
  await page.getByRole('button', { name: '查看来源 #42', exact: true }).click();
  await expect(page.getByRole('complementary', { name: '来源消息' }).getByText('推荐显示器', { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole('heading', { name: '本周概况 · v2' })).toBeVisible();
});
