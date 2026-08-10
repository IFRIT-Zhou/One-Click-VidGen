import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";

const html = fs.readFileSync(new URL("../index.html", import.meta.url), "utf8");
const css = fs.readFileSync(new URL("../assets/site.css", import.meta.url), "utf8");
const javascript = fs.readFileSync(new URL("../assets/site.js", import.meta.url), "utf8");
const assets = [
  "home-cloud-voice-v1.webp",
  "home-ai-storyboard-v1.webp",
  "home-video-publish-v1.webp",
];
const sectionAssets = [
  "home-studio-hero-2k-v3.webp",
  "home-studio-services-2k-v3.webp",
  "home-studio-flow-2k-v3.webp",
];

test("home page headlines use clean two-line copy", () => {
  assert.match(html, /<h1>把繁重的配音计算<em>交给云端 GPU<\/em><\/h1>/);
  assert.match(html, /<h2>从一段文案<br \/>到可以发布的视频<\/h2>/);
  assert.doesNotMatch(html, /把繁重的配音计算，|从一段文案，/);
});

test("home page removes the requested supporting copy", () => {
  assert.doesNotMatch(html, /HTTPS 安全连接|按实际用量计费|结果自动回传/);
  assert.doesNotMatch(html, /云端并行完成配音与画面生成/);
  assert.doesNotMatch(html, /弹性调度计算资源|自然中文语音合成|素材预览、字幕与本地导出/);
  assert.doesNotMatch(html, /proof-strip|proof-grid/);
  assert.doesNotMatch(css, /proof-strip|proof-grid/);
  assert.doesNotMatch(javascript, /proof-grid/);
});

test("home service titles link to their matching modules", () => {
  assert.match(html, /<h3><a href="\/services\/#gpu">云端 GPU 配音加速<\/a><\/h3>/);
  assert.match(html, /<h3><a href="\/studio\/#storyboard-panel">AI 分镜与画面生成<\/a><\/h3>/);
  assert.match(html, /<h3><a href="\/studio\/#compose-panel">视频生成与任务管理<\/a><\/h3>/);
});

test("home service focus follows the active card", () => {
  assert.match(css, /\.feature-card:hover,\.feature-card:focus-within/);
  assert.match(css, /\.feature-grid:has\(\.feature-card:hover\)/);
  assert.match(css, /\.feature-icon[^}]*font-size:\s*18px/);
  assert.match(css, /\.feature-card:hover \.feature-media img,\.feature-card:focus-within \.feature-media img/);
});

test("home page exposes the five-step creation flow", () => {
  assert.match(html, /<h2>开启创作流程<\/h2>/);
  assert.doesNotMatch(html, /四步连接云端创作服务|无需改变原有创作习惯/);
  assert.deepEqual(
    [...html.matchAll(/class="creation-title"[^>]*>([^<]+)<\/a><\/h3>/g)].map((match) => match[1]),
    ["创建账户", "上传文案", "选择音色", "提交任务", "下载结果"],
  );
});

test("creation flow links enforce login and route to studio modules", () => {
  const targets = [...html.matchAll(/data-creation-target="([^"]+)"/g)].map((match) => match[1]);
  assert.deepEqual(targets, [
    "account", "account", "script-panel", "script-panel", "voice-panel",
    "voice-panel", "task-panel", "task-panel", "compose-panel", "compose-panel",
  ]);
  assert.match(javascript, /sessionStorage\.getItem\("ocvg-cloud-session"\)/);
  assert.match(javascript, /if \(isLoggedIn\(\)\)/);
  assert.match(javascript, /target === "account"/);
  assert.match(javascript, /showLoggedIn\(\)/);
  assert.match(javascript, /window\.location\.assign\(href\)/);
  assert.match(javascript, /\/auth\/login/);
  assert.match(html, /id="home-auth-dialog"/);
  assert.match(html, /<h2>您已登录<\/h2>/);
});

test("creation flow adapts from horizontal rail to mobile stepper", () => {
  assert.match(css, /\.creation-flow[^}]*grid-template-columns:\s*repeat\(5/);
  assert.match(css, /@keyframes creationFlowSweep/);
  assert.match(css, /@media \(max-width:\s*760px\)[\s\S]*\.creation-flow[^}]*grid-template-columns:\s*1fr/);
  assert.match(css, /@keyframes creationFlowSweepY/);
});

test("home page presents the three generated video workflow visuals", () => {
  assets.forEach((asset) => {
    assert.match(html, new RegExp(asset.replace(".", "\\.")));
    assert.ok(fs.statSync(new URL(`../assets/${asset}`, import.meta.url)).size > 50_000);
  });
  assert.equal((html.match(/class="feature-media"/g) || []).length, 3);
});

test("home uses three dedicated high resolution section backgrounds", () => {
  sectionAssets.forEach((asset) => {
    assert.match(html, new RegExp(asset.replace(".", "\\.")));
    assert.ok(fs.statSync(new URL(`../assets/${asset}`, import.meta.url)).size > 75_000);
  });
  assert.equal((html.match(/class="home-hero-frame"/g) || []).length, 1);
  assert.equal((html.match(/class="home-section-backdrop"/g) || []).length, 2);
});

test("home backgrounds stay static while dedicated ambient layers animate", () => {
  assert.match(css, /\.home-hero-frame[^}]*animation:\s*none[^}]*transform:\s*none/);
  assert.match(css, /\.home-section-backdrop[^}]*animation:\s*none[^}]*transform:\s*none/);
  assert.doesNotMatch(css, /homeHeroCycle|homeHeroIndicator|home-hero-progress/);
  assert.equal((html.match(/class="home-motion /g) || []).length, 3);
  assert.match(css, /@keyframes homeSignalTravel/);
  assert.match(css, /@keyframes homeSignalPulse/);
  assert.match(css, /@keyframes homeAmbientBreath/);
  assert.match(css, /prefers-reduced-motion/);
});

test("home scenes crossfade vertically instead of meeting at hard section edges", () => {
  assert.match(css, /--home-scene-overlap:\s*clamp\(140px,12vw,190px\)/);
  assert.match(css, /margin-top:\s*calc\(-1 \* var\(--home-scene-overlap\)\)/);
  assert.match(css, /home-services-section \.home-section-backdrop[^}]*radial-gradient\(ellipse 108% var\(--home-scene-overlap\)/);
  assert.match(css, /workflow-section \.home-section-backdrop[^}]*radial-gradient\(ellipse 108% var\(--home-scene-overlap\)/);
  assert.match(css, /home-services-section \.home-section-backdrop[^}]*mask-composite:\s*intersect/);
  assert.match(css, /home-services-section[^}]*background:\s*transparent/);
  assert.match(css, /workflow-section[^}]*background:\s*transparent/);
  assert.match(css, /home-services-section::after,\.workflow-section::after[^}]*backdrop-filter:\s*blur\(22px\)/);
});
