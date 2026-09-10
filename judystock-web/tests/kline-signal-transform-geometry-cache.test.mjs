import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const route = fileURLToPath(new URL('../app/api/kline-embed/[ticker]/route.ts', import.meta.url));

 test('reuses one CTM per candle line for signal transform geometry', async () => {
  const source = await readFile(route, 'utf8');
  assert.match(source, /const base=baseCenter\(shape\),x1=Number\(line\.getAttribute\("x1"\)\),y1=Number\(line\.getAttribute\("y1"\)\),y2=Number\(line\.getAttribute\("y2"\)\),matrix=line\.getCTM\?\.\(\),top=mapPoint\(line\.ownerSVGElement,matrix,x1,y1\),bottom=mapPoint\(line\.ownerSVGElement,matrix,x1,y2\)/);
  assert.match(source, /mapPoint=\(svg,matrix,x,y\)=>/);
  assert.match(source, /point\.matrixTransform\(matrix\)/);
 });

test('keeps signal transform fallback for unavailable SVG matrices', async () => {
  const source = await readFile(route, 'utf8');
  assert.match(source, /return\{x,y\}/);
  assert.match(source, /Number\.isFinite\(mapped\.x\)&&Number\.isFinite\(mapped\.y\)/);
});


test('reuses one Intl date formatter for signal timestamp formatting', async () => {
  const source = await readFile(route, 'utf8');
  assert.match(source, /dateFormatter=new Intl\.DateTimeFormat\("zh-TW"/);
  assert.match(source, /stamp=value=>\{const parts=dateFormatter\.formatToParts/);
  assert.doesNotMatch(source, /stamp=value=>\{const parts=new Intl\.DateTimeFormat/);
});
