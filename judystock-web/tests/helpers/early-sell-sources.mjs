import { readFileSync } from 'node:fs';

// Source assertions cover the same feature after its data logic was extracted.
// Keep page first so existing UI source slices still start in the component.
export function readEarlySellSources() {
  return [
    '../../app/page.tsx',
    '../../app/hooks/useEarlySellSignals.ts',
    '../../lib/early-sell-signals.ts',
  ].map(path => readFileSync(new URL(path, import.meta.url), 'utf8')).join('\n');
}
