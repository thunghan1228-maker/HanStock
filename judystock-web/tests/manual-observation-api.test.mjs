import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';
import ts from 'typescript';
import * as observations from '../lib/manual-observation-homework.ts';

const require = createRequire(import.meta.url);
function moduleAt(path, dependencies) {
  const code = ts.transpileModule(readFileSync(new URL(path, import.meta.url), 'utf8'), { compilerOptions: {
    module:ts.ModuleKind.CommonJS, target:ts.ScriptTarget.ES2022, jsx:ts.JsxEmit.ReactJSX,
  } }).outputText;
  const exports = {};
  vm.runInNewContext(code, { exports, Response, require: name => {
    const key = Object.keys(dependencies).find(key => name.endsWith(key));
    return key ? dependencies[key] : require(name);
  } });
  return exports;
}
test('public API returns the existing lists when the database contains no manually saved records', async () => {
  const api = moduleAt('../app/api/manual-observation-homework/route.ts', {
    'db/manual-observation-homework': {readManualObservationHomework:async()=>[]},
    'lib/manual-observation-homework': observations,
  });
  const response = await api.GET(); const payload = await response.json();
  assert.equal(response.status, 200); assert.equal(payload.ok, true); assert.equal(payload.records.length, 2);
  assert.deepEqual(payload.records.map(row => row.tradeDate), ['2026-09-07','2026-09-06']);
  assert.equal(response.headers.get('Cache-Control'), 'no-store');
});
