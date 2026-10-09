(function (root) {
  "use strict";
  const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
  const clone = (value) => JSON.parse(JSON.stringify(value));
  function unpack(raw) { return Array.isArray(raw) ? raw : Array.isArray(raw?.projects) ? raw.projects : Array.isArray(raw?.items) ? raw.items : Object.values(raw || {}).filter((item) => item && typeof item === "object"); }
  function mergeFields(base, edited, latest, fields) {
    const merged = { ...latest };
    for (const field of fields) {
      if (same(base[field], edited[field])) continue;
      if (!same(latest[field], base[field]) && !same(latest[field], edited[field])) throw new Error(`“${field}”已在其它页面修改，请保留当前内容并刷新核对。`);
      merged[field] = edited[field];
    }
    return merged;
  }
  function mergeScenes(base = [], edited = [], latest = []) {
    const byId = (list) => new Map(list.map((scene) => [String(scene.id), scene]));
    const oldMap = byId(base), newMap = byId(edited), liveMap = byId(latest);
    const result = [];
    for (const scene of latest) {
      const id = String(scene.id), old = oldMap.get(id), local = newMap.get(id);
      if (old && !local) {
        if (!same(old, scene)) throw new Error("要删除的镜头已在其它页面修改，请刷新核对。");
        continue;
      }
      result.push(old && local ? mergeFields(old, local, scene, Object.keys(local)) : scene);
    }
    for (const scene of edited) {
      if (!liveMap.has(String(scene.id))) {
        if (oldMap.has(String(scene.id))) {
          if (!same(scene, oldMap.get(String(scene.id)))) throw new Error("正在修改的镜头已在其它页面删除，请刷新核对。");
        } else result.push(scene);
      }
    }
    if (!same(base.map(s => s.id), edited.map(s => s.id))) {
      const order = new Map(edited.map((scene, index) => [String(scene.id), index]));
      result.sort((a, b) => (order.get(String(a.id)) ?? Infinity) - (order.get(String(b.id)) ?? Infinity));
    }
    return result;
  }
  function save(storage, key, base, edited, fields) {
    const raw = JSON.parse(storage.getItem(key) || "[]"), list = unpack(raw);
    const index = list.findIndex((p) => String(p.id) === String(edited.id));
    if (index < 0) throw new Error("项目已在其它页面删除，请先导出当前编辑备份。");
    const latest = list[index];
    const merged = mergeFields(base, edited, latest, fields.filter(field => field !== "scenes"));
    if (fields.includes("scenes")) merged.scenes = mergeScenes(base.scenes, edited.scenes, latest.scenes);
    merged.updatedAt = new Date().toISOString();
    Object.assign(latest, merged);
    storage.setItem(key, JSON.stringify(raw));
    return clone(latest);
  }
  root.OCVGProjectStore = { save, mergeFields, mergeScenes, unpack, clone };
})(typeof window !== "undefined" ? window : globalThis);
