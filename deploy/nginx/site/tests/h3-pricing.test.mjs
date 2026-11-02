import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const source = fs.readFileSync(new URL('../assets/h3-pricing.js', import.meta.url), 'utf8');
const context = vm.createContext({}); vm.runInContext(source, context);
const render = context.OCVGH3Pricing.text;
const price = {pricing: {currency:'CNY',duration_basis:'requested',rounding:'ceil_cent',version:'new',threshold_seconds:10,first_10_seconds_rate:'0.08',additional_seconds_rate:'0.12',credits_per_yuan:'1'},examples:[{seconds:12,credits:'1.04'}]};
test('public display uses server rates and examples, including changed prices', () => {
  const result=render(price);
  assert.match(result.rate,/0.08 元\/秒/);assert.match(result.rate,/0.12 元\/秒/);
  assert.match(result.examples,/12 秒 1.04 积分/);assert.match(result.terms,/整分不加价/);
});
test('invalid or missing price does not silently display a hardcoded or free price', () => {
  for (const value of [{}, {...price,pricing:{...price.pricing,first_10_seconds_rate:'NaN'}}, {...price,examples:[{seconds:12,credits:0}]}]) assert.throws(()=>render(value));
});
