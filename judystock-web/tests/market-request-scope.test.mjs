import assert from 'node:assert/strict';
import test from 'node:test';
import { AsyncLocalStorage } from 'node:async_hooks';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

function modules() {
  const scopeModule = load('market-request-scope', {'node:async_hooks': {AsyncLocalStorage}});
  return {
    ...scopeModule,
    ...load('timed-single-flight', {'./market-request-scope.ts': scopeModule}),
    ...load('bounded-market-data', {'./market-request-scope.ts': scopeModule}),
  };
}
function load(name, dependencies) {
  const source = readFileSync(new URL(`../lib/${name}.ts`, import.meta.url), 'utf8');
  const compiled = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}}).outputText;
  const mod = {exports: {}};
  vm.runInNewContext(compiled, {exports: mod.exports, require: name => dependencies[name], URL, TextDecoder, fetch: (...args) => globalThis.fetch(...args)});
  return mod.exports;
}

test('an abandoned request cannot strand another request behind its quote limiter', async (t) => {
  const {withMarketRequestScope, fetchBoundedMarketJson} = modules();
  let blockedFetches = 0;
  const never = new Promise(() => {});
  t.mock.method(globalThis, 'fetch', async url => {
    if (String(url).includes('abandoned')) { blockedFetches++; return never; }
    return Response.json({close: 125});
  });
  withMarketRequestScope(() => {
    void fetchBoundedMarketJson('https://quote.test/abandoned/1', {});
    void fetchBoundedMarketJson('https://quote.test/abandoned/2', {});
    void fetchBoundedMarketJson('https://quote.test/abandoned/queued', {});
  });
  const result = await withMarketRequestScope(() => fetchBoundedMarketJson('https://quote.test/current', {}));
  assert.equal(result.close, 125);
  assert.equal(blockedFetches, 2);
});

test('each request still fetches at most two market bodies concurrently', async (t) => {
  const {withMarketRequestScope, fetchBoundedMarketJson} = modules();
  let active = 0, peak = 0;
  t.mock.method(globalThis, 'fetch', async () => {
    peak = Math.max(peak, ++active);
    await new Promise(resolve => setTimeout(resolve, 2));
    active--;
    return Response.json({ok: true});
  });
  await withMarketRequestScope(() => Promise.all(Array.from({length: 9}, (_, i) => fetchBoundedMarketJson(`https://quote.test/${i}`, {}))));
  assert.equal(peak, 2);
});

test('a queued fetch creates its timeout only when its network slot opens', async (t) => {
  const {withMarketRequestScope, fetchBoundedMarketJson} = modules();
  let release, created = 0;
  const gate = new Promise(resolve => { release = resolve; });
  t.mock.method(globalThis, 'fetch', async url => { if (String(url).includes('hold')) await gate; return Response.json({ok:true}); });
  await withMarketRequestScope(async () => {
    const a=fetchBoundedMarketJson('https://quote.test/hold1', {});
    const b=fetchBoundedMarketJson('https://quote.test/hold2', {});
    const c=fetchBoundedMarketJson('https://quote.test/queued', () => {created++;return {signal:AbortSignal.timeout(1000)};});
    assert.equal(created,0);release();await Promise.all([a,b,c]);assert.equal(created,1);
  });
});

test('single-flight shares same-request work but never waits on another request', async () => {
  const {withMarketRequestScope, timedSingleFlight} = modules();
  let calls = 0;
  const load = timedSingleFlight(1000, () => ++calls === 1 ? new Promise(() => {}) : Promise.resolve({signals: [1, 2]}));
  withMarketRequestScope(() => { const a = load(); assert.equal(a, load()); });
  const recovered = await withMarketRequestScope(() => load());
  assert.equal(recovered.signals.length, 2);
  assert.equal(calls, 2);
  assert.equal(await withMarketRequestScope(() => load()), recovered);
  assert.equal(calls, 2);
});

test('oversized responses release their same-request slot so remaining quotes finish', async (t) => {
  const {withMarketRequestScope, fetchBoundedMarketJson} = modules();
  t.mock.method(globalThis, 'fetch', async url => String(url).includes('large')
    ? new Response('x'.repeat(4 * 1024 * 1024 + 1)) : Response.json({close: 88}));
  const results = await withMarketRequestScope(() => Promise.allSettled([
    fetchBoundedMarketJson('https://quote.test/large', {}),
    fetchBoundedMarketJson('https://quote.test/small', {}),
    fetchBoundedMarketJson('https://quote.test/next', {}),
  ]));
  assert.equal(results[0].status, 'rejected');
  assert.equal(results[0].reason.message, 'market-data-payload-too-large');
  assert.equal(results[2].value.close, 88);
});
