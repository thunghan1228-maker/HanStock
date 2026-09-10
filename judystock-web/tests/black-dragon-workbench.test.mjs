import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';
import ts from 'typescript';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import * as blackDragon from '../lib/black-dragon.ts';
const require=createRequire(import.meta.url);

function component(file, state={}) {
  const compiled=ts.transpileModule(readFileSync(new URL(file,import.meta.url),'utf8'),{compilerOptions:{
    module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX,
  },transformers:{before:[context=>root=>ts.visitNode(root,function visit(node){
    if(ts.isVariableDeclaration(node)&&ts.isArrayBindingPattern(node.name)&&ts.isCallExpression(node.initializer)
      &&node.initializer.expression.getText()==='useState'){
      const key=node.name.elements[0].name.getText();
      if(Object.hasOwn(state,key))return ts.factory.updateVariableDeclaration(node,node.name,node.exclamationToken,node.type,
        ts.factory.updateCallExpression(node.initializer,node.initializer.expression,node.initializer.typeArguments,
          [ts.factory.createPropertyAccessExpression(ts.factory.createIdentifier('__state'),key)]));
    }return ts.visitEachChild(node,visit,context);
  })]}}).outputText;
  const mod={exports:{}};
  vm.runInNewContext(compiled,{exports:mod.exports,__state:state,console,
    require:name=>name.includes('StockTradingBadges')?{default:()=>null}:name.includes('BlackDragonEvidence')
      ?{default:component('../app/stock-screener/BlackDragonEvidence.tsx')}:name.includes('lib/black-dragon')?blackDragon:require(name),
  });
  return mod.exports.default;
}

test('a historical black candle explains entry next to a later positive quote',()=>{
  const match={code:'1815',name:'富喬',market:'上櫃',groupName:'小電組',date:'2026/09/03',
    open:132,high:139.5,close:120,maScore:14,referenceHighs:{5:139},newHighPeriods:[5,10,20]};
  const Component=component('../app/stock-screener/StrategyWorkbench.tsx',{
    ran:true,enabled:new Set(['blackDragon']),blackDragonRows:{1815:match},blackDragonDataState:'ready',
  });
  const html=renderToStaticMarkup(React.createElement(Component,{rows:[{code:'1815',name:'富喬',group:'舊族群',
    market:'上櫃',price:130,changePct:9.7,today:95.4,threeDay:58.2,fiveDay:47.2,surge:103.2,
    strongDays:1,trustDays:1,combined:76.1,judgement:'多方雙強'}],loading:false,dataDate:'2026/09/07',quoteDataDate:'2026/09/07',updatedAt:''}));
  assert.ok(html.includes('符合日 2026/09/03'));
  assert.ok(html.includes('最高 139.5 ＞ 前五日高點 139'));
  assert.ok(html.includes('開 132 → 收 120（黑 K）'));
  assert.ok(html.includes('行情價')&&html.includes('130.00')&&html.includes('+9.70%'));
  assert.ok(html.includes('14/15')&&html.includes('符合日 K 棒'));
  assert.ok(html.includes('小電組'));
  assert.ok(html.includes('並非每檔今天都創高或收黑'));
});
