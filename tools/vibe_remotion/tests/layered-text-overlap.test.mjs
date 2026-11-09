import test from 'node:test';
import assert from 'node:assert/strict';
import {collectLayeredTextRects,findLayeredTextOverlaps,isStaticLayeredPlan} from '../lib/layered-text-overlap.mjs';

const rect = (left, top, width, height) => ({left, top, right: left + width, bottom: top + height});
const entry = (id, ...rects) => ({id, rects});
const collision = (...entries) => findLayeredTextOverlaps(entries);

test('same-group number and unit are checked for real overlap', () => {
  const result = collision(entry('g:result:number', rect(100, 100, 200, 180)),
    entry('g:result:unit', rect(240, 160, 300, 80)));
  assert.deepEqual(result, [{first: 'g:result:number', second: 'g:result:unit', width: 60, height: 80}]);
});

test('adjacent mixed-size number and unit remain valid', () => {
  assert.deepEqual(collision(entry('number', rect(100, 100, 200, 180)),
    entry('unit', rect(308, 160, 300, 80))), []);
});

test('two colored title fragments may touch at their metric edges', () => {
  assert.deepEqual(collision(entry('title-white', rect(100, 10, 700, 120)),
    entry('title-cyan', rect(798, 10, 300, 120))), []);
});

test('separated lines with slight font-metric contact are not collisions', () => {
  assert.deepEqual(collision(entry('line-1', rect(100, 100, 400, 60)),
    entry('line-2', rect(100, 157, 400, 60))), []);
});

test('multiline labels compare every actual line, not a union box', () => {
  const lines = entry('multiline', rect(10, 10, 100, 40), rect(10, 80, 100, 40));
  assert.deepEqual(collision(lines, entry('in-gap', rect(20, 53, 80, 20))), []);
  assert.equal(collision(lines, entry('colliding', rect(20, 90, 80, 20))).length, 1);
});

test('a label is not compared against itself and pairs are reported once', () => {
  const a = entry('same', rect(0, 0, 100, 40), rect(0, 30, 100, 40));
  assert.deepEqual(collision(a, {...a}), []);
  assert.equal(collision(a, entry('other', rect(0, 0, 100, 100))).length, 1);
});

test('invalid, zero-area and hidden rectangles do not report collisions', () => {
  const a = entry('a', rect(0, 0, 100, 40));
  assert.deepEqual(collision(a, {id: 'hidden', visible: false, rects: a.rects},
    entry('bad', rect(NaN, 0, 100, 40), rect(0, 0, 0, 40))), []);
});

test('an overlap of a whole CJK glyph is detected even in a long sentence', () => {
  assert.equal(collision(entry('a', rect(0, 0, 800, 50)),
    entry('b', rect(760, 0, 800, 50))).length, 1);
});

test('ink bounds distinguish mixed-size font whitespace from painted collisions', () => {
  const large = {...entry('large', rect(100, 30, 140, 180)), ink_rects:[rect(104, 85, 132, 100)]};
  const above = {...entry('above', rect(130, 20, 40, 60)), ink_rects:[rect(132, 30, 36, 42)]};
  const below = {...entry('below', rect(130, 180, 40, 60)), ink_rects:[rect(132, 198, 36, 42)]};
  assert.deepEqual(collision(large, above, below), []);
  assert.equal(collision(large, {...below, ink_rects:[rect(132, 160, 36, 42)]}).length, 1);
});

test('unavailable ink measurements retain conservative collision detection', () => {
  const a = {...entry('a', rect(0, 0, 100, 40)), ink_rects:null};
  assert.equal(collision(a, entry('b', rect(20, 0, 100, 40))).length, 1);
});

test('static detector requires explicit frozen timing for every layer', () => {
  const layer = {motion: 'none', at: 0, enter: 0, exit: 0};
  assert.equal(isStaticLayeredPlan({elements: [layer, {...layer}]}), true);
  for (const change of [{motion: 'fade'}, {at: 1}, {enter: .6}, {exit: .3}, {at: undefined}]) {
    assert.equal(isStaticLayeredPlan({elements: [layer, {...layer, ...change}]}), false);
  }
  assert.equal(isStaticLayeredPlan({elements: []}), false);
  assert.equal(isStaticLayeredPlan(null), false);
});

function mockDocument() {
  return {
    defaultView: {getComputedStyle: node => ({display: 'block', visibility: 'visible', opacity: '1', ...node.style})},
    createRange() {
      let selected;
      return {selectNodeContents(node) { selected = node; }, getClientRects() { return selected.rangeRects; }};
    },
  };
}

test('collector uses range glyph-line rects rather than wide container boxes', () => {
  const doc = mockDocument();
  const make = (id, left) => ({ownerDocument: doc, textContent: '居中文字', dataset: {layeredText: id},
    parentElement: null, style: {}, rangeRects: [rect(left, 10, 100, 40)],
    getBoundingClientRect() { throw new Error('layout boxes must not be compared'); }});
  const root = {querySelectorAll: selector => {
    assert.equal(selector, '[data-layered-text]');
    return [make('a', 100), make('b', 300)];
  }};
  assert.deepEqual(collision(...collectLayeredTextRects(root)), []);
});

test('collector excludes ancestor opacity, hidden nodes, and whitespace-only labels', () => {
  const doc = mockDocument();
  const make = (id, extra = {}) => ({ownerDocument: doc, textContent: '文字', dataset: {layeredText: id},
    parentElement: null, style: {}, rangeRects: [rect(0, 0, 100, 40)], ...extra});
  const nodes = [make('visible'), make('transparent', {parentElement: {style: {opacity: '0'}, parentElement: null}}),
    make('hidden', {style: {visibility: 'hidden'}}), make('blank', {textContent: ' \n '})];
  assert.deepEqual(collectLayeredTextRects({querySelectorAll: () => nodes}).map(item => item.id), ['visible']);
});
