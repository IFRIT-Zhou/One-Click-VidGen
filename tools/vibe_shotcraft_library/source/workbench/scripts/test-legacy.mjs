import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import test from "node:test";
import ts from "typescript";

const wb = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const manifest = JSON.parse(fs.readFileSync(path.join(wb, "../demos/_legacy/manifest.json"), "utf8"));
const oldProject = {
  name: "Saved before the October library update", fps: 30, width: 1920, height: 1080,
  tracks: [{ id: "old-track", name: "My edited shots", clips: manifest.cards.map((card, i) => ({
    id: `old-${i}`, cardId: `demo:${card.stem}`, start: i * 20 + 7, duration: card.duration + 11,
    inOffset: 3, speed: 0.75, opacity: 0.8, scale: 1.1, x: 12, y: -4,
    props: { custom: "preserve my edits" }, label: `My ${card.stem}`,
  })) }],
};

// Evaluate the actual TS registry/store/composition with the project's compiler.
// Aliases use the same virtual paths as Vite/webpack; only browser storage and
// events are stubbed. No source-module mocks or install-time Git history needed.
const cache = new Map();
const storage = new Map([["shotcraft-workbench-project-v1", JSON.stringify(oldProject)]]);
let save;
const windowStub = { location: { search: "" }, addEventListener() {} };
const documentStub = { addEventListener() {} };
const localStorageStub = { getItem: k => storage.get(k) ?? null, setItem: (k, v) => storage.set(k, v) };
const load = filename => {
  if (cache.has(filename)) return cache.get(filename).exports;
  const mod = { exports: {} };
  cache.set(filename, mod);
  if (filename.endsWith(".json")) {
    mod.exports = JSON.parse(fs.readFileSync(filename, "utf8"));
    return mod.exports;
  }
  const require = createRequire(filename);
  const resolve = spec => {
    if (spec.startsWith("@demos/")) return path.join(wb, "demosrc", spec.slice(7));
    if (spec.startsWith("@proj/")) return path.join(wb, "proj-stub", spec.slice(6));
    if (spec.startsWith(".")) return path.resolve(path.dirname(filename), spec);
    return null;
  };
  const localRequire = spec => {
    const base = resolve(spec);
    if (!base) return require(spec);
    const file = [base, base + ".ts", base + ".tsx", base + ".json"].find(f => fs.existsSync(f) && fs.statSync(f).isFile());
    if (!file) throw new Error(`Cannot resolve ${spec} from ${filename}`);
    return load(file);
  };
  const code = ts.transpileModule(fs.readFileSync(filename, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  new Function("require", "module", "exports", "window", "document", "localStorage", "setTimeout", "clearTimeout", code)(
    localRequire, mod, mod.exports, windowStub, documentStub, localStorageStub,
    callback => { save = callback; return 1; }, () => {},
  );
  return mod.exports;
};

const { CARD_LIST, CARDS } = load(path.join(wb, "src/cards/registry.ts"));
const { buildCardRegistry } = load(path.join(wb, "src/cards/buildRegistry.ts"));
const { useStore } = load(path.join(wb, "src/store.ts"));
const { MainComposition } = load(path.join(wb, "src/preview/Composition.tsx"));

test("all 66 historical card IDs resolve, without returning to the motion library", () => {
  assert.equal(manifest.cards.length, 66);
  assert.equal(new Set(manifest.cards.map(c => c.stem)).size, 66);
  const visible = new Set(CARD_LIST.map(c => c.id));
  for (const card of manifest.cards) {
    const id = `demo:${card.stem}`;
    assert.equal(CARDS[id].durationInFrames, card.duration);
    assert.equal(CARDS[id].nameEn, card.nameEn);
    assert.ok(card.nameEn);
    assert.equal(typeof CARDS[id].component, "function");
    assert.ok(!visible.has(id), `${id} must not be offered for new projects`);
  }
  assert.equal(CARD_LIST.filter(c => c.id.startsWith("demo:")).length, 150);
  assert.equal(CARDS["demo:CounterConfetti"].durationInFrames, 138);
});

test("old localStorage saves retain every clip and all timing/transform/property edits", () => {
  assert.deepEqual(useStore.getState().project, oldProject);
  assert.equal(storage.get("shotcraft-workbench-project-v1"), JSON.stringify(oldProject));
});

test("JSON import, undo and saving preserve retired clips without substitution", () => {
  const imported = JSON.parse(JSON.stringify(oldProject));
  imported.name = "Imported old project";
  imported.tracks[0].clips[0].speed = 1.25;
  useStore.getState().setProject(imported);
  assert.deepEqual(useStore.getState().project, imported);
  save();
  assert.deepEqual(JSON.parse(storage.get("shotcraft-workbench-project-v1")), imported);
  useStore.getState().undo();
  assert.deepEqual(useStore.getState().project, oldProject);
});

test("the shared preview/export composition renders every retired clip instead of dropping it", () => {
  const sequences = MainComposition({ project: oldProject }).props.children.flat();
  assert.equal(sequences.length, 66);
  sequences.forEach((sequence, i) => {
    assert.ok(sequence, oldProject.tracks[0].clips[i].cardId);
    assert.equal(sequence.props.from, oldProject.tracks[0].clips[i].start);
    assert.equal(sequence.props.durationInFrames, oldProject.tracks[0].clips[i].duration);
  });
});

test("current definitions take precedence over compatibility IDs and duplicates", () => {
  const current = CARDS["demo:HitCounter"];
  const old = { ...CARDS["demo:CounterConfetti"], id: current.id };
  const registry = buildCardRegistry([current, old], [old, CARDS["demo:CounterConfetti"]]);
  assert.deepEqual(registry.cardList, [current]);
  assert.equal(registry.cards[current.id], current);
  assert.equal(registry.cards["demo:CounterConfetti"], CARDS["demo:CounterConfetti"]);
});

test("snapshot imports resolve locally and legacy textures use an isolated namespace", () => {
  const legacyDir = path.join(wb, "demosrc/_legacy");
  for (const card of manifest.cards) {
    const file = path.join(legacyDir, card.path + ".tsx");
    const src = fs.readFileSync(file, "utf8");
    assert.ok(!src.includes("textures/live/"), `${card.stem} must not read linked/new textures`);
    for (const match of src.matchAll(/from\s*['"]([^'"]+)['"]/g)) {
      if (!match[1].startsWith(".")) continue;
      const base = path.resolve(path.dirname(file), match[1]);
      assert.ok([base, base + ".ts", base + ".tsx", base + ".json"].some(f => fs.existsSync(f)), `${card.stem}: ${match[1]}`);
    }
  }
  const assets = path.join(wb, "public/legacy-textures");
  assert.equal(fs.realpathSync(assets), fs.realpathSync(path.join(legacyDir, "_textures")));
  assert.ok(fs.existsSync(path.join(assets, "agent-stream.jpg")));
  assert.ok(!fs.readFileSync(path.join(wb, "src/mediaManifest.ts"), "utf8").includes("legacy-textures/"));
});
