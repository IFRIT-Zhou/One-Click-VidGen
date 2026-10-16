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
    "/storyboard/plan-stage",
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

test("document upload parses TXT and DOCX into the script without submitting a job", () => {
  assert.match(html, /id="document-file"[^>]+accept="[^"]*\.txt,[^"]*\.docx/);
  assert.match(html, /id="document-upload-status"[^>]+role="status"[^>]+aria-live="polite"/);
  assert.match(javascript, /api\("\/documents\/parse",\s*\{\s*method:\s*"POST",\s*body:\s*form\s*\}\)/);
  assert.match(javascript, /form\.append\("file", file\)/);
  assert.match(javascript, /result\.text\.length > script\.maxLength/);
  assert.match(javascript, /script\.value = result\.text/);
  assert.match(javascript, /script\.dispatchEvent\(new Event\("input", \{ bubbles: true \}\)\)/);
  assert.doesNotMatch(javascript, /parseDocument[\s\S]{0,1500}submitJob\(/);
});

test("image submissions share a stable batch-scoped idempotency key", () => {
  assert.match(javascript, /generateAllImages\(clientJobId\)/);
  assert.match(javascript, /`\$\{batchId\}-image-\$\{item\.scene\.index\}`/);
  assert.match(javascript, /headers:\s*\{\s*"Idempotency-Key":\s*clientJobId\s*\}/);
  assert.match(javascript, /JSON\.stringify\(\{\s*clientJobId,\s*prompt:/);
  assert.doesNotMatch(javascript, /web-image-\$\{Date\.now\(\)\}/);
});

test("protected studio links require login before entering their target", () => {
  assert.match(javascript, /const protectedTargets = new Set\(\[[^\]]*"script-panel"[^\]]*"compose-panel"[^\]]*\]\)/);
  assert.match(javascript, /guardProtectedTarget\(\)/);
  assert.match(javascript, /pendingProtectedTarget = target/);
  assert.match(javascript, /enterPendingProtectedTarget\(\)/);
  assert.match(javascript, /scrollIntoView\(\{[^}]*block:\s*"start"/);
});

test("one-click video is the primary workflow and keeps the advanced flow", () => {
  assert.match(html, /id="one-click-panel"/);
  assert.match(html, /id="create-video-job"/);
  assert.match(html, /id="video-job"[^>]+aria-live="polite"/);
  assert.match(html, /<details class="advanced-workflow" id="storyboard-panel" data-wizard-step="2" hidden>/);
  assert.match(html, /<details class="advanced-workflow" id="advanced-creation" data-wizard-step="3" hidden>/);
  ["storyboard-panel", "voice-panel", "settings-panel", "task-panel", "compose-panel"]
    .forEach((id) => assert.ok(html.includes(`id="${id}"`), `advanced workflow lost ${id}`));
});

test("one-click video uses the frozen server orchestration contracts", () => {
  assert.match(javascript, /api\("\/video-jobs", \{ method: "POST"/);
  assert.match(javascript, /api\("\/video-jobs\?page=1&page_size=8"\)/);
  assert.match(javascript, /api\(`\/video-jobs\/\$\{encodeURIComponent\(jobId\)\}`\)/);
  assert.match(javascript, /`\/video-jobs\/\$\{encodeURIComponent\(jobId\)\}\/cancel`/);
  assert.match(javascript, /`\/api\/v1\/video-jobs\/\$\{encodeURIComponent\(state\.videoJob\.job_id\)\}\/result`/);
  ["client_job_id", "script", "voice", "aspect_ratio", "resolution", "scene_count", "visual_style", "speed", "pitch", "emotion", "emotion_weight"]
    .forEach((field) => assert.match(javascript, new RegExp(`${field}:`), `missing create field ${field}`));
  assert.match(javascript, /function videoJobPayload\(\)[\s\S]{0,500}state\.selectedVoice\.type === "preset" \? "preset" : "user"/);
  ["storyboard", "tts", "images", "compose", "publish"]
    .forEach((step) => assert.ok(javascript.includes(`\"${step}\"`), `missing progress step ${step}`));
});

test("recent complete video jobs recover across refresh and download MP4", () => {
  assert.match(javascript, /localStorage\.setItem\("ocvg-recent-video-job", job\.job_id\)/);
  assert.match(javascript, /localStorage\.getItem\("ocvg-recent-video-job"\)/);
  assert.match(javascript, /pollVideoJob\(job\.job_id\)/);
  assert.match(javascript, /downloadBlob\(blob, `OneClickVidGen_\$\{state\.videoJob\.job_id\}\.mp4`\)/);
});

test("studio wizard restores browser history and edits one storyboard scene at a time", () => {
  assert.match(html, /id="wizard-prev"/);
  assert.match(html, /id="wizard-next"/);
  assert.match(html, /id="script-panel" data-wizard-step="1"/);
  assert.match(html, /id="one-click-panel" data-wizard-step="4" hidden/);
  assert.match(html, /id="compose-panel" data-wizard-step="5" hidden/);
  assert.match(javascript, /history\.pushState\(historyState/);
  assert.match(javascript, /history\.replaceState\(historyState/);
  assert.match(javascript, /addEventListener\("popstate", guardProtectedTarget\)/);
  assert.match(javascript, /const scene = state\.storyboard\[selected\]/);
  assert.match(javascript, /id="storyboard-scene-select"/);
  assert.match(javascript, /state\.selectedStoryboardIndex = Math\.min\(state\.storyboard\.length - 1, selected \+ 1\)/);
  assert.match(javascript, /action === "merge"/);
  assert.match(javascript, /action === "delete"/);
});

test("video job stages and statuses never expose internal English values", () => {
  [
    ["storyboarding", "生成分镜中"],
    ["generating_assets", "生成素材中"],
    ["composing", "合成视频中"],
    ["publishing", "发布结果中"],
    ["cancel_requested", "正在取消"],
    ["cancelled", "已取消"],
  ].forEach(([status, label]) => assert.ok(javascript.includes(`${status}: \"${label}\"`), `missing status label ${status}`));
  assert.match(javascript, /function videoStageLabel\(stage\)/);
  assert.match(javascript, /function videoStepStatusLabel\(status\)/);
  assert.match(javascript, /function videoJobMessage\(job\)/);
  assert.match(javascript, /videoStageLabel\(job\.stage\)/);
  assert.match(javascript, /videoStepStatusLabel\(status\)/);
  assert.doesNotMatch(javascript, /\[status\] \|\| status \|\| "未知"/);
});
