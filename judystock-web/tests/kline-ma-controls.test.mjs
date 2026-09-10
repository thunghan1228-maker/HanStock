import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

test('watchlist MA controls open and close native settings without hiding them or duplicating buttons', () => {
  const source = readFileSync(new URL('../app/api/kline-embed/[ticker]/route.ts', import.meta.url), 'utf8');
  const ast = ts.createSourceFile('route.ts', source, ts.ScriptTarget.Latest, true);
  let html;
  function visit(node) {
    if (ts.isVariableDeclaration(node) && node.name.getText(ast) === 'watchlistChartBootstrap') html = node.initializer.whenFalse.text;
    ts.forEachChild(node, visit);
  }
  visit(ast);
  const script = html.match(/<script[^>]*>([\s\S]*?)<\/script>/)[1];
  new vm.Script(script); // Validate cooked template escapes, exactly as sent to the browser.
  const nodes = [];
  class Element {
    constructor(tag = 'div', loc = '') {
      this.tag = tag; this.loc = loc; this.children = []; this.dataset = {}; this.attrs = {}; this.classes = new Set(); nodes.push(this);
      this.classList = {contains: n => this.classes.has(n), toggle: (n, on) => on ? this.classes.add(n) : this.classes.delete(n), [Symbol.iterator]: () => this.classes.values()};
    }
    appendChild(child) { this.children.push(child); child.parentElement = this; return child; }
    prepend(child) { this.children.unshift(child); child.parentElement = this; }
    matches(selector) { return selector.split(',').some(s => s.startsWith('#') ? this.id === s.slice(1) : s === this.tag || this.loc && s.includes(':' + this.loc + '"')); }
    querySelector(selector) { for (const child of this.children) { if (child.matches(selector)) return child; const found = child.querySelector(selector); if (found) return found; } return null; }
    closest(selector) { return this.matches(selector) ? this : this.parentElement?.closest(selector); }
    setAttribute(k, v) { this.attrs[k] = v; }
    getAttribute(k) { return this.attrs[k] ?? null; }
    click() { this.onclick?.(); }
  }
  const body = new Element();
  const dialog = body.appendChild(new Element());
  const header = dialog.appendChild(new Element());
  const chart = dialog.appendChild(new Element('div', '1986'));
  const queue = [];
  const panels = new Map();
  for (const [label, loc] of [['MA設定', '1899'], ['指標參數', '1843'], ['MACD', ''], ['KD', '']]) {
    const button = header.appendChild(new Element('button')); button.textContent = '  ' + label + '  ';
    button.onclick = () => {
      if (panels.has(label)) { const panel = panels.get(label); dialog.children.splice(dialog.children.indexOf(panel), 1); panels.delete(label); button.classes.clear(); }
      else { panels.set(label, dialog.appendChild(new Element('div', loc))); button.classes.add('bg-primary/15'); }
    };
  }
  const document = {body, documentElement: body, querySelector: s => body.querySelector(s), querySelectorAll: () => nodes.filter(n => n.tag === 'button'), getElementById: id => nodes.find(n => n.id === id), createElement: tag => new Element(tag), addEventListener() {}};
  let observeChanges;
  vm.runInNewContext(script, {document, Element, window: {addEventListener() {}}, requestAnimationFrame: fn => queue.push(fn), MutationObserver: class {constructor(fn) {observeChanges = fn;} observe() {}}});
  const flush = () => { while (queue.length) queue.shift()(); };
  flush();
  const controls = document.getElementById('hanstock-watchlist-indicator-controls');
  assert.equal(controls.children.length, 4);
  const ma = controls.children.find(n => n.dataset.indicator === 'MA設定');
  assert.equal(ma.disabled, false);
  ma.onclick({stopPropagation() {}}); flush();
  assert.equal(panels.get('MA設定').classList.contains('hanstock-mini-hidden'), false);
  assert.equal(ma.getAttribute('aria-pressed'), 'true');
  assert.equal(header.classList.contains('hanstock-mini-hidden'), true);
  const params = controls.children.find(n => n.dataset.indicator === '指標參數');
  params.onclick({stopPropagation() {}}); flush();
  assert.equal(panels.get('指標參數').classList.contains('hanstock-mini-hidden'), false);
  assert.equal(panels.get('MA設定').classList.contains('hanstock-mini-hidden'), false);
  ma.onclick({stopPropagation() {}}); flush();
  assert.equal(panels.has('MA設定'), false);
  assert.equal(ma.getAttribute('aria-pressed'), 'false');
  assert.equal(controls.children.length, 4);
  observeChanges([{type: 'attributes', target: header}]);
  observeChanges([{type: 'attributes', target: ma}]);
  const svg = chart.appendChild(new Element('svg'));
  observeChanges([{type: 'childList', target: svg}]);
  assert.equal(queue.length, 0, 'unrelated decoration and chart updates do not schedule layout work');
  const native = header.children.find(n => n.textContent.includes('MA設定'));
  native.classes.add('bg-primary/15');
  observeChanges([{type: 'attributes', target: native}]);
  assert.equal(queue.length, 1, 'native indicator state changes still update the proxy');
  flush();
  assert.equal(ma.getAttribute('aria-pressed'), 'true');
});
