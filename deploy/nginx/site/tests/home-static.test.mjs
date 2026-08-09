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

test("home page presents the three generated video workflow visuals", () => {
  assets.forEach((asset) => {
    assert.match(html, new RegExp(asset.replace(".", "\\.")));
    assert.ok(fs.statSync(new URL(`../assets/${asset}`, import.meta.url)).size > 50_000);
  });
  assert.equal((html.match(/class="home-visual-frame"/g) || []).length, 3);
});

test("home visuals crossfade without moving the bitmap", () => {
  assert.match(css, /@keyframes homeVisualCycle/);
  assert.match(css, /\.home-visual-frame[^}]*animation: homeVisualCycle[^}]*transform: none/);
  assert.match(css, /prefers-reduced-motion[\s\S]*\.home-visual-frame:first-child/);
  assert.doesNotMatch(css, /@keyframes homeVisualCycle[^}]*translate|@keyframes homeVisualCycle[^}]*scale/);
});
