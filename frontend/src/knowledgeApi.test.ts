import { afterEach, describe, expect, it, vi } from 'vitest';
import { knowledgeApi } from './knowledgeApi';

describe('knowledge evidence API', () => {
  afterEach(() => vi.unstubAllGlobals());
  it('keeps backfill preview and execution separate with the preview version', async () => {
    const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ version: 'hash', filters: { group_id: 7 } }) });
    vi.stubGlobal('fetch', fetcher);
    const preview = await knowledgeApi.preview(7);
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0][0]).toBe('/api/v2/knowledge/backfill/preview');
    await knowledgeApi.backfill(preview);
    expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({ group_id: 7, expected_version: 'hash' });
  });
  it('reports unavailable evidence instead of a false empty result', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, text: async () => 'KNOWLEDGE_UNAVAILABLE' }));
    await expect(knowledgeApi.context(42)).rejects.toThrow('KNOWLEDGE_UNAVAILABLE');
  });
});
