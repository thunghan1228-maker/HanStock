import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import * as jsxRuntime from 'react/jsx-runtime';
import {renderToStaticMarkup} from 'react-dom/server';
const source=readFileSync(new URL('../app/page.tsx',import.meta.url),'utf8');
const ast=ts.createSourceFile('page.tsx',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
const functions=ast.statements.filter(n=>ts.isFunctionDeclaration(n)&&['ChipIntradayForceBadge','formatSigned','normalizedTaipeiDate'].includes(n.name?.text)).map(n=>n.getText(ast)).join('\n');
const compiled=ts.transpileModule(functions+'\nexports.Badge=ChipIntradayForceBadge; exports.normalize=normalizedTaipeiDate;', {compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2022}}).outputText;
const exports={};vm.runInNewContext(compiled,{exports,require:()=>jsxRuntime});
for(const [value,expected,tone] of [[12.3,'+12.3%','positive'],[-8.4,'-8.4%','negative'],[0,'0.0%','neutral'],[null,'待補','neutral']])test('chip force badge displays '+expected,()=>{const html=renderToStaticMarkup(exports.Badge({value:{forcePct:value,tradeDate:'2026-09-04'},tradeDate:'2026-09-04'}));assert.ok(html.includes(expected));assert.ok(html.includes('chip-intraday-force '+tone));assert.ok(html.includes('2026-09-04 盤中大戶力'));});
test('chip force badge rejects a different trading day',()=>{const html=renderToStaticMarkup(exports.Badge({value:{forcePct:12.3,tradeDate:'2026-09-03'},tradeDate:'2026-09-04'}));assert.ok(html.includes('待補'));assert.ok(!html.includes('+12.3%'));});

test('ranking slash date requests and displays the same force trading day',()=>{
  let expression;
  function visit(node){if(ts.isVariableDeclaration(node)&&node.name.getText(ast)==='chipForceDate')expression=node.initializer.getText(ast);ts.forEachChild(node,visit)}visit(ast);
  for(const date of ['2026/09/04','2026-09-04']){
    const tradeDate=vm.runInNewContext(expression,{officialChipData:{dataDate:date},normalizedTaipeiDate:exports.normalize});
    assert.equal(tradeDate,'2026-09-04');
    const html=renderToStaticMarkup(exports.Badge({tradeDate,value:{tradeDate:'2026-09-04',forcePct:42.092035422373726}}));
    assert.ok(html.includes('+42.1%'));
    assert.ok(!html.includes('待補'));
  }
});
