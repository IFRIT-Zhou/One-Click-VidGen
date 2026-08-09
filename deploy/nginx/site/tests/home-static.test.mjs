import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";

const html = fs.readFileSync(new URL("../index.html", import.meta.url), "utf8");
const css = fs.readFileSync(new URL("../assets/site.css", import.meta.url), "utf8");
const assets = [
  "home-cloud-voice-v1.webp",
  "home-ai-storyboard-v1.webp",
  "home-video-publish-v1.webp",
];
const heroAssets = [
  "home-hero-compute-city-v2-4k.webp",
  "home-hero-production-stage-v2-4k.webp",
  "home-hero-release-horizon-v2-4k.webp",
];

test("home page presents the three generated video workflow visuals", () => {
  assets.forEach((asset) => {
    assert.match(html, new RegExp(asset.replace(".", "\\.")));
    assert.ok(fs.statSync(new URL(`../assets/${asset}`, import.meta.url)).size > 50_000);
  });
  assert.equal((html.match(/class="feature-media"/g) || []).length, 3);
});

test("home hero uses separate blurred backgrounds", () => {
  heroAssets.forEach((asset) => {
    assert.match(html, new RegExp(asset.replace(".", "\\.")));
    assert.ok(fs.statSync(new URL(`../assets/${asset}`, import.meta.url)).size > 400_000);
  });
  assert.equal((html.match(/class="home-hero-frame"/g) || []).length, 3);
  assert.match(css, /\.home-hero-frame[^}]*filter:\s*blur\(1px\)/);
});

test("home hero backgrounds only crossfade", () => {
  assert.match(css, /@keyframes homeHeroCycle/);
  assert.match(css, /\.home-hero-frame[^}]*animation: homeHeroCycle/);
  assert.match(css, /prefers-reduced-motion[\s\S]*\.home-hero-frame:first-child/);
  assert.doesNotMatch(css, /@keyframes homeHeroCycle[^}]*translate|@keyframes homeHeroCycle[^}]*scale/);
});
