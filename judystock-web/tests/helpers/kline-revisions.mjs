import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';

export function runtimeRevision(source) {
  const value = source.match(/const KLINE_RUNTIME_REVISION = "([^"]+)"/)?.[1];
  assert.ok(value, 'Runtime must declare a cache revision');
  return value;
}

export function assertKlineRevisionConsistency() {
  const read = file => readFileSync(new URL(`../../${file}`, import.meta.url), 'utf8');
  const runtime = runtimeRevision(read('app/api/kline-runtime/route.ts'));
  assert.equal(runtimeRevision(read('app/api/kline-embed/[ticker]/route.ts')), runtime);
  const layout = read('app/layout.tsx');
  const preload = layout.match(/rel="modulepreload"[\s\S]*?href="([^"]+)"/)?.[1];
  assert.ok(preload, 'Runtime preload must be wired');
  const url = new URL(preload, 'https://site.test');
  assert.equal(url.pathname, '/api/kline-runtime');
  assert.equal(url.searchParams.get('rev'), runtime, 'Preload and executed runtime must use the same revision');
  const embed = read('app/api/kline-embed/[ticker]/route.ts');
  const asset = embed.match(/const KLINE_RUNTIME_ASSET = "([^"]+)"/)?.[1];
  assert.ok(asset, 'K-line shell must declare its runtime asset');
  assert.equal(url.searchParams.get('asset'), asset, 'Preload asset must match the shell');
  const uiRevisions = ['app/kline/page.tsx', 'app/kline-grid/page.tsx', 'app/page.tsx'].map(file => {
    const values = [...read(file).matchAll(/uiRev=([\w-]+)/g)].map(match => match[1]);
    assert.ok(values.length, `${file} must version the embed URL`);
    assert.equal(new Set(values).size, 1, `${file} contains inconsistent UI revisions`);
    return values[0];
  });
  assert.equal(new Set(uiRevisions).size, 1, 'All K-line entry points must use the same UI revision');
}
