import { afterEach, describe, expect, it, vi } from 'vitest';
import { searchApi } from './searchApi';

describe('search API boundary', () => {
  afterEach(() => vi.unstubAllGlobals());
  it('uses read-only search and encodes query operators as data', async () => {
    const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ items: [] }) });
    vi.stubGlobal('fetch', fetcher);
    await searchApi.query(new URLSearchParams({ q: '手机 "Claude Code"', object_type: 'message' }));
    const url = new URL(fetcher.mock.calls[0][0], 'http://localhost');
    expect(url.searchParams.get('q')).toBe('手机 "Claude Code"');
    expect(fetcher.mock.calls[0][1].method).toBeUndefined();
  });
});
