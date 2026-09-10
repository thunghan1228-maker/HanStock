import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import test from 'node:test';
import ts from 'typescript';
const context = { exports: {} };
runInNewContext(ts.transpileModule(readFileSync(new URL('../lib/intraday-center-signals.ts', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, context);
const accepts = context.exports.isActiveIntradayCenterSignal;
test('retired daily strategies and all daily triangle stages are excluded', () => {
  for (const kind of ['triangleNearBreakout', 'triangleBreakoutPendingVolume', 'triangleVolumeBreakout']) assert.equal(accepts({kind}), false);
  assert.equal(accepts({kind:'riverBull', riverSignalType:'daily-strategy'}), false);
  assert.equal(accepts({kind:'riverBear', riverSignalType:'daily-strategy'}), false);
});
test('five minute, large order and explicitly retained black dragon remain', () => {
  for (const kind of ['fiveMinuteTwelveShort', 'fiveMinuteOnePlusTwoLong', 'instantLargeBuy', 'mainForceStrongBullish']) assert.equal(accepts({kind}), true);
  assert.equal(accepts({kind:'riverBear', strategyKind:'blackDragon'}), true);
});
