import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";

const html = fs.readFileSync(new URL("../services/index.html", import.meta.url), "utf8");
const siteJavascript = fs.readFileSync(new URL("../assets/site.js", import.meta.url), "utf8");
const rechargeJavascript = fs.readFileSync(new URL("../assets/recharge.js", import.meta.url), "utf8");
const css = fs.readFileSync(new URL("../assets/site.css", import.meta.url), "utf8");
const preview = new URL("../assets/oneclickvidgen-ai-workflow-hero-v4.webp", import.meta.url);

test("services page exposes the three requested linked modules", () => {
  assert.doesNotMatch(html, /云端 GPU 完成配音与画面生成/);
  assert.match(css, /\.hero-service-flow[^}]*max-width:\s*590px/);
  assert.match(css, /\.flow-node[^}]*font-size:\s*14px/);
  assert.match(css, /@media \(max-width:\s*620px\)[\s\S]*\.hero-service-flow[^}]*max-width:\s*318px[^}]*[\s\S]*\.flow-node[^}]*font-size:\s*12px/);
  assert.match(html, /<h1>从一段文案<br \/>到可以发布的视频<\/h1>/);
  assert.doesNotMatch(html, /可以交付的视频/);
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

test("services hero uses the generated One-Click VidGen workflow asset", () => {
  assert.match(html, /oneclickvidgen-ai-workflow-hero-v4\.webp/);
  assert.match(html, /hero-render-signal/);
  assert.match(css, /\.services-hero-image[^}]*animation:\s*none[^}]*transform:\s*none/);
  assert.doesNotMatch(css, /@keyframes heroPhotoDrift/);
  assert.match(css, /@keyframes heroRenderScan/);
  assert.match(css, /@keyframes heroRenderPlayhead/);
  assert.ok(fs.statSync(preview).size > 10_000, "workflow preview is unexpectedly small");
});
