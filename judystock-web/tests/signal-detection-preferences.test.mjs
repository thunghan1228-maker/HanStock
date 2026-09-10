import { readEarlySellSources } from "./helpers/early-sell-sources.mjs";
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {readSignalDetectionPreferences,signalDetectionEnabled,SIGNAL_DETECTION_LABELS} from '../lib/signal-detection-preferences.ts';
import {isActiveIntradayCenterSignal} from '../lib/intraday-center-signals.ts';
test('defaults on, preserves individual off choices after reload, ignores malformed and unknown settings',()=>{
 for(const mode of Object.keys(SIGNAL_DETECTION_LABELS)) assert.equal(signalDetectionEnabled({},mode),true);
 const prefs=readSignalDetectionPreferences(JSON.stringify({extraLargeBuy:false,extraLargeSell:true,history:false,riverBear:'false'}));
 assert.equal(signalDetectionEnabled(prefs,'extraLargeBuy'),false);
 assert.equal(signalDetectionEnabled(prefs,'extraLargeSell'),true);
 assert.equal(signalDetectionEnabled(prefs,'riverBear'),true);
 assert.equal(signalDetectionEnabled(prefs,'history'),true);
 assert.deepEqual(readSignalDetectionPreferences('broken'),{});
 assert.deepEqual(readSignalDetectionPreferences('null'),{});
});
test('remaining categories stay active without retired river or daily-strategy collectors',()=>{
 const page=readEarlySellSources();
 assert.ok(!page.includes('early-signal-detection-toggle'));
 assert.ok(!page.includes('按此關閉'));
 assert.ok(!page.includes('toggleDetection('));
 assert.ok(page.includes('const combinedQueue = normalizeDisplayedInstantLargeSignals(queue)'));
 assert.ok(!page.includes('/api/river-radar/intraday'));
 assert.ok(!page.includes('setCenterMode("riverBull")'));
 assert.ok(!page.includes('setCenterMode("riverBear")'));
 assert.ok(!page.includes('setCenterMode("dailyStrategies")'));
 assert.ok(page.includes('extraLargeBackfillCompleted.current = payload.completed === true'));
});

test('old pinned signals are excluded while black dragon and the other signal kinds remain',()=>{
 const oldQueue = [
  {kind:'riverBull',riverSignalType:'river'},
  {kind:'riverBear',riverSignalType:'river'},
  {kind:'riverBear',riverSignalType:'daily-strategy',strategyKind:'redSword'},
  {kind:'riverBull'},
  {kind:'riverBear',riverSignalType:'black-dragon',strategyKind:'blackDragon'},
  {kind:'fiveMinuteTwelveShort'},
  {kind:'fiveMinuteOnePlusTwoLong'},
  {kind:'instantLargeBuy'},
  {kind:'intradayLargeForceBuy'},
 ];
 assert.deepEqual(oldQueue.filter(isActiveIntradayCenterSignal),oldQueue.slice(4));
});
