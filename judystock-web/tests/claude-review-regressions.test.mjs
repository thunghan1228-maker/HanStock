import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import vm from 'node:vm';
import ts from 'typescript';
import {renderToStaticMarkup} from 'react-dom/server';
import {rankTdccWeeklyChanges} from '../lib/tdcc-weekly-score.ts';

const require = createRequire(import.meta.url);
const read = file => readFileSync(new URL(`../${file}`, import.meta.url), 'utf8');
function moduleWithMocks(source, mocks) {
  const module = {exports: {}};
  const js = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022}}).outputText;
  vm.runInNewContext(js, {module, exports: module.exports, require: name => mocks[name] ?? require(name)});
  return module.exports;
}

test('TDCC card renders pending instead of crashing when the weekly ranking has no entry', () => {
  for (const weeklyChangePp of [null, 0, 1]) {
    const payload = {rows: [{code: '2330', largeHolderPct: 72, previousPct: null, weeklyChangePp}]};
    const {TdccLargeHolderCard} = moduleWithMocks(read('app/TdccLargeHolderCard.tsx'), {
      react: {useState: () => [payload, () => {}], useEffect() {}},
      '../lib/tdcc-weekly-score': {rankTdccWeeklyChanges},
    });
    const html = renderToStaticMarkup(TdccLargeHolderCard({code: '2330'}));
    assert.match(html, weeklyChangePp === null ? /待資料/ : /0\.0/);
    assert.match(html, /72\.00%/);
  }
});

function payloadUpdater(next) {
  const source = read('app/useIntradayForceHomework.ts');
  const ast = ts.createSourceFile('homework.ts', source, ts.ScriptTarget.Latest, true);
  let updater;
  function visit(node) {
    if (ts.isCallExpression(node) && node.expression.getText(ast) === 'setPayload') updater = node.arguments[0].getText(ast);
    ts.forEachChild(node, visit);
  }
  visit(ast);
  assert.ok(updater);
  return vm.runInNewContext(`(${updater})`, {next, rows: next.rows, Date, Map});
}

test('homework first response without a trade date accepts a null previous payload safely', () => {
  const next = {ok: true, rows: []};
  assert.equal(payloadUpdater(next)(null), next);
  assert.equal(payloadUpdater(next)({rows: null}), next);
});

test('homework null guard preserves same-day merging, expiry and next-response precedence', () => {
  const now = Date.now(), next = {tradeDate: '2026-09-07', rows: [{ticker: '2330', barTs: now, force: 2}]};
  const result = payloadUpdater(next)({tradeDate: next.tradeDate, rows: [
    {ticker: '2330', barTs: now, force: 1}, {ticker: '2303', barTs: now}, {ticker: '2408', barTs: now - 6 * 60_000},
  ]});
  assert.deepEqual(Array.from(result.rows, row => row.ticker), ['2330', '2303']);
  assert.equal(result.rows[0].force, 2);
  assert.equal(payloadUpdater(next)({tradeDate: '2026-09-04', rows: []}), next);
});

test('final K-line HTML actually includes all three reviewed bootstraps in the requested order', () => {
  const source = read('app/api/kline-embed/[ticker]/route.ts');
  const ast = ts.createSourceFile('route.ts', source, ts.ScriptTarget.Latest, true);
  let template;
  function visit(node) {
    if (ts.isArrowFunction(node) && ts.isTemplateExpression(node.body) && node.body.getText(ast).includes('/api/kline-runtime?rev=')) template = node.body;
    ts.forEachChild(node, visit);
  }
  visit(ast);
  assert.ok(template);
  const context = {ticker: '2330', KLINE_RUNTIME_REVISION: 'test', assetPath: '/assets/runtime.js', encodeURIComponent};
  // Evaluate the actual final HTML template, not merely the existence of unused declarations.
  for (const span of template.templateSpans) {
    const e = span.expression;
    if (ts.isIdentifier(e) && /Bootstrap$/.test(e.text)) context[e.text] = `<script id="${e.text}"></script>`;
    if (ts.isCallExpression(e) && ts.isIdentifier(e.expression) && /Bootstrap$/.test(e.expression.text)) context[e.expression.text] = () => `<script id="${e.expression.text}"></script>`;
  }
  context.klineLiveQuoteBootstrap = () => '<script id="klineLiveQuoteBootstrap"></script>';
  const html = vm.runInNewContext(template.getText(ast), context);
  for (const [before, target, after] of [
    ['signalLayoutBootstrap', 'signalTransformSyncBootstrap', 'periodAwareCrosshairBridgeBootstrap'],
    ['referenceDockBootstrap', 'dailyChangeOhlcBootstrap', 'preopenTrialQuoteBootstrap'],
    ['fixedSearchActionsBootstrap', 'searchResultActionsBootstrap', 'refineSearchActionsBootstrap'],
  ]) {
    assert.equal(html.split(`id="${target}"`).length - 1, 1);
    assert.ok(html.indexOf(`id="${before}"`) < html.indexOf(`id="${target}"`));
    assert.ok(html.indexOf(`id="${target}"`) < html.indexOf(`id="${after}"`));
  }
  assert.match(html, /id="candleReadyBridgeBootstrap"><\/script><script id="vwapVisibilityBridgeBootstrap"/);
});

test('search actions handle adjacent ticker/name spans and continue past already enhanced rows', () => {
  const source = read('app/api/kline-embed/[ticker]/route.ts');
  const ast = ts.createSourceFile('route.ts', source, ts.ScriptTarget.Latest, true);
  let initializer;
  function visit(node) {
    if (ts.isVariableDeclaration(node) && node.name.getText(ast) === 'searchResultActionsBootstrap') initializer = node.initializer.getText(ast);
    ts.forEachChild(node, visit);
  }
  visit(ast);
  const script = vm.runInNewContext(initializer).match(/<script[^>]*>([\s\S]*?)<\/script>/)[1];
  const frames = [], listeners = {}, messages = [], rows = [];
  function row(code, name) {
    return {textContent: code + name + '另開視窗', children: [{}, {}], dataset: {},
      matches: () => true, closest: () => null,
      querySelectorAll: () => [code, name, '另開視窗'].map(textContent => ({textContent, children: []})),
      insertAdjacentElement(_where, actions) {this.actions = actions;},
    };
  }
  const already = row('2303', '聯電'); already.dataset.hanstockSearchActions = 'true';
  rows.push(already, row('2330', '台積電'), row('2337', '旺宏'));
  const document = {documentElement: {}, addEventListener(name, fn) {listeners[name] = fn;}, querySelectorAll: () => rows,
    createElement() {return {querySelector(selector) {return {addEventListener(_event, fn) {this.click = fn; buttons[selector] = this;}};}};},
  };
  const buttons = {}, window = {}, parent = {postMessage: message => messages.push(message)};
  vm.runInNewContext(script, {document, window: {...window, addEventListener() {}}, parent, location: {origin: 'https://test.local'}, requestAnimationFrame: fn => frames.push(fn), MutationObserver: class {observe() {}}});
  frames.shift()();
  assert.ok(rows[1].actions, 'first new row enhanced after an old row');
  assert.ok(rows[2].actions, 'all new rows enhanced, not only the first');
  buttons['.hanstock-search-open'].click({preventDefault() {}, stopPropagation() {}});
  assert.equal(messages[0].ticker, '2337');
  assert.equal(messages[0].name, '旺宏');
  const actions = rows[1].actions;
  listeners.DOMContentLoaded(); frames.shift()();
  assert.equal(rows[1].actions, actions, 'repeated observation does not duplicate buttons');
});
