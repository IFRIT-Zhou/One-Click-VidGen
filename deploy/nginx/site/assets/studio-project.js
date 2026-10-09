(function(root) {
  "use strict";
  function build(input) {
    const { id, now, script, settings, account } = input;
    const job = account && input.audioJob?.account === account ? input.audioJob : null;
    const chunks = job?.result?.chunks || [];
    let cursor = 0;
    const scenes = input.scenes.map((scene, index) => {
      const chunk = chunks.find((part) => Number(part.index) === index);
      const duration = Number(chunk?.duration_seconds || chunk?.duration || scene.duration) || 5;
      const start = cursor; cursor += duration;
      return { ...scene, id: `scene-${id}-${index + 1}`, index, order: index, title: scene.title || `镜头 ${index + 1}`, narration: scene.narration || "", visualPrompt: scene.prompt || "", duration, start, currentImageVersionId: null };
    });
    const voiceLines = scenes.map((scene, index) => {
      const chunk = chunks.find((part) => Number(part.index) === index);
      const cloudAudio = chunk && job.job_id ? { url: `/api/v1/cloud/jobs/${encodeURIComponent(job.job_id)}/chunks/${index}/audio`, jobId: job.job_id, account, createdAt: now } : undefined;
      return { id: `audio-${id}-${index + 1}`, sceneId: scene.id, text: scene.narration, subtitleText: scene.narration, speechText: scene.narration, speed: settings.speed, pitch: settings.pitch, emotion: settings.emotion, pause: index === scenes.length - 1 ? 0 : 350, pauseAfter: index === scenes.length - 1 ? 0 : .35, status: cloudAudio ? "generated" : "draft", ...(cloudAudio ? { cloudAudio } : {}) };
    });
    const imageVersions = [];
    (input.images || []).forEach((item, index) => {
      const scene = scenes[Number(item.scene?.index ?? index)];
      if (!account || !scene || item.account !== account || item.status !== "SUCCESS" || !item.imageUrl || /^(blob:|file:)/i.test(item.imageUrl)) return;
      const version = { id: `image-${id}-${index + 1}`, sceneId: scene.id, url: item.imageUrl, status: "ready", source: "redraw", taskId: item.taskId, account, createdAt: now };
      imageVersions.push(version); scene.currentImageVersionId = version.id;
    });
    const videoJob = account && input.videoJob?.account === account ? input.videoJob : null;
    const exports = videoJob?.status === "completed" ? [{ id: `export-${id}`, type: "mp4", status: "completed", url: `/api/v1/video-jobs/${encodeURIComponent(videoJob.job_id)}/result`, jobId: videoJob.job_id, account, createdAt: now }] : [];
    return { id, title: script.split(/[。！？.!?\n]/).find(Boolean)?.trim().slice(0, 28) || "未命名视频项目", script, createdAt: now, updatedAt: now, aspectRatio: settings.aspectRatio, resolution: settings.resolution, visualStyle: settings.visualStyle, scenes, characters: [], subtitles: scenes.map((scene, index) => ({ id: `subtitle-${id}-${index + 1}`, sceneId: scene.id, start: scene.start, end: scene.start + scene.duration, text: scene.narration, hidden: false })), audio: voiceLines, voiceLines, imageVersions, videoClips: [], bgm: [], exports, source: { studio: true, account, voiceId: input.voiceId, referenceImages: input.referenceImages || [], ...(videoJob ? { videoJob: { jobId: videoJob.job_id, status: videoJob.status, account } } : {}) } };
  }
  function saveContinuing(storage, projectStore, candidate, previous) {
    const key = "ocvg.projects.v1";
    if (!previous) {
      const projects = projectStore.unpack(JSON.parse(storage.getItem(key) || "[]"));
      if (projects.some(project => project.id === candidate.id)) throw new Error("作品编号重复，请重新开始创作。");
      projects.unshift(candidate); storage.setItem(key, JSON.stringify(projects));
      return candidate;
    }
    // Only producer fields that actually changed participate in the merge.
    // A subsequent visit to Studio must not undo edits made in another editor.
    const fields = ["title", "script", "aspectRatio", "resolution", "visualStyle", "scenes", "subtitles", "audio", "voiceLines", "imageVersions", "exports", "source"]
      .filter(field => JSON.stringify(candidate[field]) !== JSON.stringify(previous[field]));
    if (!fields.length) {
      const latest = projectStore.unpack(JSON.parse(storage.getItem(key) || "[]")).find(project => project.id === candidate.id);
      if (!latest) throw new Error("作品已移除，请开始新的创作。");
      return latest;
    }
    return projectStore.save(storage, key, previous, candidate, fields);
  }
  function safeReturn(value) {
    if (typeof value !== "string" || !/^\/(workspace|subtitles|voice-editor|visual-editor|video-editor|projects)\//.test(value) || /[\\\r\n]/.test(value)) return "";
    const url = new URL(value, "https://local.invalid");
    return url.origin === "https://local.invalid" && /^\/(workspace|subtitles|voice-editor|visual-editor|video-editor|projects)\/$/.test(url.pathname) ? url.pathname + url.search + url.hash : "";
  }
  root.OCVGStudioProject = { build, saveContinuing, safeReturn };
})(typeof window !== "undefined" ? window : globalThis);
