import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";

const html = fs.readFileSync(new URL("../recharge/index.html", import.meta.url), "utf8");
const javascript = fs.readFileSync(new URL("../assets/recharge.js", import.meta.url), "utf8");

test("recharge page exposes the seven fixed public tiers", () => {
  const ids = [...html.matchAll(/name="package" value="([^"]+)"/g)].map((match) => match[1]);
  assert.deepEqual(ids, [
    "credits_1", "credits_5", "credits_10", "credits_20",
    "credits_30", "credits_50", "credits_100",
  ]);
  assert.doesNotMatch(html, /临时测试|credits_test|credits_1000|credits_5000/);
});

test("recharge client uses the server catalog without test-product branches", () => {
  assert.match(javascript, /\/recharge\/products/);
  assert.match(javascript, /credits_20/);
  assert.doesNotMatch(javascript, /test-payment-product|test_product_enabled|credits_test/);
});
