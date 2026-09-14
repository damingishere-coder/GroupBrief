import { describe, expect, it } from 'vitest';
import { comparisonText } from './pages/v2/InsightWorkspace';

describe('period comparisons', () => {
  it('keeps missing coverage separate from an observed zero', () => {
    expect(comparisonText({ current: 10, previous: 0, delta: null, percent: null, state: 'not_comparable' })).toContain('不可比较');
    expect(comparisonText({ current: 10, previous: 0, delta: 10, percent: null, state: 'zero_baseline' })).toBe('从 0 增至 10');
    expect(comparisonText({ current: 10, previous: 5, delta: 5, percent: 100, state: 'comparable' })).toBe('环比 +100%');
  });
});
