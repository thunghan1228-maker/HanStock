import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';
import ts from 'typescript';
import React from 'react';
import { renderToString } from 'react-dom/server';
import { mergeForceHistory } from '../lib/kline-force.ts';
import * as dailyForce from '../lib/kline-daily-force.ts';

const require = createRequire(import.meta.url);

function renderPage(state = {}) {
  const source = readFileSync(new URL('../app/kline/page.tsx', import.meta.url), 'utf8');
  const compiled = ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
  }, transformers: { before: [context => root => ts.visitNode(root, function visit(node) {
    if (ts.isVariableDeclaration(node) && ts.isArrayBindingPattern(node.name)
      && ts.isCallExpression(node.initializer) && node.initializer.expression.getText() === 'useState') {
      const key = node.name.elements[0].name.getText();
      if (Object.hasOwn(state, key)) return ts.factory.updateVariableDeclaration(node, node.name, node.exclamationToken, node.type,
        ts.factory.updateCallExpression(node.initializer, node.initializer.expression, node.initializer.typeArguments,
          [ts.factory.createPropertyAccessExpression(ts.factory.createIdentifier('__testState'), key)]));
    }
    return ts.visitEachChild(node, visit, context);
  })] } }).outputText;
  const mod = { exports: {} };
  vm.runInNewContext(compiled, {
    exports: mod.exports,
    require: name => name.includes('StockTradingBadges') ? { default: () => null }
      : name.includes('lib/kline-force') ? { mergeForceHistory }
      : name.includes('lib/kline-daily-force') ? dailyForce : require(name),
    URLSearchParams, URL, console, __testState: state,
  });
  return renderToString(React.createElement(mod.exports.default));
}

test('opening the K-line page can render on the server without browser globals', () => {
  const html = renderPage();
  assert.ok(html.includes('original-kline-panel'));
  assert.ok(html.includes('重新整理'));
  assert.ok(!html.includes('Worker threw exception'));
});

test('daily chart renders net bars and a cumulative line at matching K-candle positions', () => {
  const dailyCandleSlots = ['2026-09-01', '2026-09-02', '2026-09-03', '2026-09-04']
    .map((date, index) => ({ date, x: 700 + index * 20, width: 8 }));
  const dailyForcePoints = [{ date: '2026-09-02', net: 20 }, { date: '2026-09-03', net: -10 }, { date: '2026-09-04', net: 0 }];
  const html = renderPage({ period: 'day', dailyCandleSlots, dailyForcePoints });
  const svg = html.match(/<svg[^>]*aria-label="依日 K 日期對齊的主力淨量與累積線"[\s\S]*?<\/svg>/)?.[0];
  assert.ok(svg);
  assert.equal((svg.match(/<rect /g) || []).length, 3);
  assert.equal((svg.match(/<polyline /g) || []).length, 2);
  assert.ok(svg.includes('x="716" width="8"'));
  assert.ok(svg.includes('x="736" width="8"'));
  assert.ok(svg.includes('points="720,12 740,60"'));
  assert.ok(!svg.includes('x="696"'), 'missing history stays blank under its candle');
  assert.ok(html.includes('累積 +10,000 張'));
  const paused = renderPage({ period: 'day', dailyCandleSlots, dailyForcePoints, showDailyForce: false });
  assert.ok(!paused.includes('aria-label="依日 K 日期對齊的主力淨量與累積線"'));
});

test('daily missing history is an explicit dashed connector and no invented net bars', () => {
  const dates = ['2026-08-31','2026-09-01','2026-09-02','2026-09-03','2026-09-04'];
  const html = renderPage({period:'day', dailyCandleSlots:dates.map((date, i)=>({date,x:600+i*20,width:8})),
    dailyForcePoints:[{date:dates[0],net:-20},{date:dates[4],net:10}]});
  const svg = html.match(/<svg[^>]*aria-label="依日 K 日期對齊的主力淨量與累積線"[\s\S]*?<\/svg>/)?.[0];
  assert.equal((svg.match(/<rect /g)||[]).length,2);
  assert.equal((svg.match(/<polyline /g)||[]).length,1);
  assert.ok(svg.includes('stroke-dasharray="6 5"'));
  assert.ok(svg.includes('points="600,108 680,60"'));
  assert.ok(html.includes('缺資料：09-01、09-02、09-03；柱狀留白'));
});
