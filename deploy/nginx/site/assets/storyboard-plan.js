(function(root) {
  "use strict";
  const compact = (text) => String(text).replace(/\s/g, "");
  const nonempty = (value) => typeof value === "string" && value.trim().length > 0;
  function analysis(value) {
    if (!value || typeof value !== "object" || Array.isArray(value) || !["theme", "tone", "visual_continuity"].every(key => nonempty(value[key])) || !["characters", "locations"].every(key => Array.isArray(value[key]))) throw new Error("分析结果缺少主题、语气、人物/地点列表或连续性说明");
    return value;
  }
  function plan(value, script, splitText, expectedCount = null) {
    if (!Array.isArray(value?.scenes) || !value.scenes.length || value.scenes.length > 16) throw new Error("分镜须包含 1–16 个镜头");
    if (value.scenes.some(scene => !scene || !["title", "narration", "description"].every(key => nonempty(scene[key])))) throw new Error("每个镜头须有标题、原文旁白和画面描述");
    if (compact(value.scenes.map(scene => scene.narration).join("")) !== compact(script)) throw new Error("旁白拼接必须按原顺序完整保留全文，不得增删或改写");
    if (expectedCount !== null && value.scenes.length !== expectedCount) throw new Error(`必须严格生成 ${expectedCount} 个镜头，当前返回 ${value.scenes.length} 个`);
    if (expectedCount !== null && value.scenes.some(scene => scene.narration.trim().length > 900)) throw new Error("每个镜头旁白不得超过 900 字，请在指定镜头数内重新分配旁白");
    const count = value.scenes.reduce((sum, scene) => sum + splitText(scene.narration).length, 0);
    if (count > 16) throw new Error("旁白按长度拆分后超过 16 个镜头，请调整分镜或缩短原文后重试");
    return value.scenes;
  }
  function prompts(value, scenes) {
    if (!Array.isArray(value?.scenes) || value.scenes.length !== scenes.length) throw new Error("画面提示词必须与全部镜头一一对应");
    const indexes = new Set();
    for (const item of value.scenes) {
      if (!Number.isInteger(item?.index) || item.index < 0 || item.index >= scenes.length || indexes.has(item.index) || !nonempty(item.prompt)) throw new Error("画面提示词编号无效、重复或内容为空");
      indexes.add(item.index);
    }
    return value.scenes;
  }
  async function generate({ script, style, count, exactCount = false, poolChat, parseModelJson, splitText, onProgress = () => {} }) {
    if (exactCount && (!Number.isInteger(count) || count < 1 || count > 16)) throw new Error("分镜数量请输入 1–16 的整数");
    if (exactCount && compact(script).length > count * 900) throw new Error("当前文案过长，请增加分镜数量（每镜最多 900 字）");
    // Only malformed successful responses are repaired. Network/provider errors
    // propagate immediately, avoiding an automatic second billable submission.
    async function stage(index, system, input, validate) {
      onProgress(index, false);
      const instruction = exactCount && index === 1 ? `${system}\n用户指定严格生成 ${count} 个镜头，不得增减；每镜旁白最多 900 字，完整保留全文。` : system;
      let reply = await poolChat(instruction, input, { stage: index, style, count, exact_count: exactCount });
      try { return validate(parseModelJson(reply)); }
      catch (first) {
        onProgress(index, true);
        reply = await poolChat(`${instruction}\n上次返回未通过校验：${first.message}。请重新生成满足全部约束的严格 JSON。`, input, { stage: index, style, count, exact_count: exactCount, repair_error: first.message.slice(0, 1000) });
        try { return validate(parseModelJson(reply)); }
        catch (error) { throw new Error(`Agent ${index} 修复后仍未通过校验：${error.message}。原分镜已保留。`); }
      }
    }
    const overview = await stage(0, '你是视频策划 Agent 0。通读文案，返回严格 JSON：{"theme":"","tone":"","characters":[],"locations":[],"visual_continuity":""}。主题、语气、连续性说明不能为空。不要 Markdown。', script, analysis);
    const scenes = await stage(1, `你是视频分镜 Agent 1。根据叙事转折、主体动作和地点变化规划连续镜头，最多 16 个。${exactCount ? `严格生成 ${count} 个镜头，每镜旁白最多 900 字。` : `按预计配音时长建议约 ${count} 个镜头，这只是节奏参考，允许根据内容调整数量。`}普通短文每镜约 4–8 秒，避免把多个事件堆进一个长镜头；长文达到 16 镜上限时优先保证内容完整和语义边界。每个镜头必须保留原文旁白，所有 narration 按顺序拼接须与原文一致（允许空白差异）。返回严格 JSON：{"scenes":[{"title":"","narration":"","description":"镜头主体、动作、环境和构图"}]}。不要 Markdown。`, JSON.stringify({ analysis: overview, script }), value => plan(value, script, splitText, exactCount ? count : null));
    const pictures = await stage(2, `你是画面提示词 Agent 2。为每个镜头生成一条可直接生图的中文提示词，保持人物、时代和地点连续，统一采用“${style}”。不要在画面中生成文字、水印或标志。返回严格 JSON：{"scenes":[{"index":0,"prompt":""}]}。每个镜头编号从 0 开始，不能重复或遗漏。不要 Markdown。`, JSON.stringify({ analysis: overview, scenes }), value => prompts(value, scenes));
    const result = scenes.flatMap((scene, index) => {
      const parts = exactCount ? [scene.narration.trim()] : splitText(scene.narration), prompt = pictures.find(item => item.index === index).prompt.trim();
      return parts.map((narration, part) => ({ title: parts.length > 1 ? `${scene.title}（${part + 1}）` : scene.title, narration, description: scene.description, prompt, imageStatus: "pending" }));
    }).map((scene, index) => ({ ...scene, index }));
    if (result.length > 16 || compact(result.map(scene => scene.narration).join("")) !== compact(script)) throw new Error("最终旁白校验失败，原分镜已保留。");
    return result;
  }
  root.OCVGStoryboardPlan = { generate, analysis, plan, prompts };
})(typeof window !== "undefined" ? window : globalThis);
