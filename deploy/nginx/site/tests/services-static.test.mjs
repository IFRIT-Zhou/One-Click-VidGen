import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";

const html = fs.readFileSync(new URL("../services/index.html", import.meta.url), "utf8");
const siteJavascript = fs.readFileSync(new URL("../assets/site.js", import.meta.url), "utf8");
const rechargeJavascript = fs.readFileSync(new URL("../assets/recharge.js", import.meta.url), "utf8");
const preview = new URL("../assets/studio-workflow-preview.webp", import.meta.url);

test("services page exposes the three requested linked modules", () => {
  const links = [...html.matchAll(/data-service-link="([^"]+)"/g)].map((match) => match[1]);
  assert.deepEqual(links, ["gpu", "voices", "video"]);
  ["云端 GPU 配音", "音色选择与管理", "视频生成与管理"].forEach((title) => {
    assert.match(html, new RegExp(`<h2>${title}</h2>`));
  });
  ["gpu", "voices", "video"].forEach((id) => assert.match(html, new RegExp(`id="${id}"`)));
});

test("services pricing contains the seven fixed selectable tiers", () => {
  const ids = [...html.matchAll(/data-product-id="([^"]+)"/g)].map((match) => match[1]);
  assert.deepEqual(ids, [
    "credits_1", "credits_5", "credits_10", "credits_20",
    "credits_30", "credits_50", "credits_100",
  ]);
  assert.match(siteJavascript, /\/api\/v1\/recharge\/products/);
  assert.match(siteJavascript, /selectServicePrice/);
  assert.doesNotMatch(html, /credits_1000|credits_5000|临时测试/);
});

test("selected service package is carried into the recharge page", () => {
  assert.match(siteJavascript, /\/recharge\/\?product=/);
  assert.match(rechargeJavascript, /new URLSearchParams\(window\.location\.search\)\.get\("product"\)/);
  assert.match(rechargeJavascript, /CSS\.escape\(requestedId\)/);
});

test("services hero uses a real workflow preview asset", () => {
  assert.match(html, /studio-workflow-preview\.webp/);
  assert.ok(fs.statSync(preview).size > 10_000, "workflow preview is unexpectedly small");
});
