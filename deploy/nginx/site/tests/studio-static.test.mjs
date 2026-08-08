import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";

const html = fs.readFileSync(new URL("../studio/index.html", import.meta.url), "utf8");
const javascript = fs.readFileSync(new URL("../assets/studio.js", import.meta.url), "utf8");

test("studio script only references existing, unique DOM ids", () => {
  const declared = [...html.matchAll(/id="([^"]+)"/g)].map((match) => match[1]);
  assert.equal(new Set(declared).size, declared.length, "duplicate HTML id");
  const ids = new Set(declared);
  const referenced = [...javascript.matchAll(/element\("#([^"]+)"\)/g)].map((match) => match[1]);
  assert.deepEqual([...new Set(referenced.filter((id) => !ids.has(id)))], []);
});

test("vertical workflow exposes all frozen cloud contracts", () => {
  [
    "/auth/login",
    "/account/summary",
    "/cloud/voices",
    "/cloud/quotes",
    "/cloud/jobs",
    "/model-pool/status",
    "/model-pool/v1/chat/completions",
    "/image-pool/generate",
    "/image-pool/query",
  ].forEach((path) => assert.ok(javascript.includes(path), `missing ${path}`));
  ["script-panel", "storyboard-panel", "voice-panel", "settings-panel", "task-panel", "compose-panel"]
    .forEach((id) => assert.ok(html.includes(`id="${id}"`), `missing workflow panel ${id}`));
});

test("browser bundle contains no upstream credential fields", () => {
  assert.doesNotMatch(javascript, /RUNNINGHUB_API_KEY|GEMINI_API_KEY|OPENAI_API_KEY|api[_-]?key\s*[:=]/i);
  assert.match(javascript, /MediaRecorder/);
  assert.match(javascript, /captureStream/);
});

test("image submissions share a stable batch-scoped idempotency key", () => {
  assert.match(javascript, /generateAllImages\(clientJobId\)/);
  assert.match(javascript, /`\$\{batchId\}-image-\$\{item\.scene\.index\}`/);
  assert.match(javascript, /headers:\s*\{\s*"Idempotency-Key":\s*clientJobId\s*\}/);
  assert.match(javascript, /JSON\.stringify\(\{\s*clientJobId,\s*prompt:/);
  assert.doesNotMatch(javascript, /web-image-\$\{Date\.now\(\)\}/);
});
