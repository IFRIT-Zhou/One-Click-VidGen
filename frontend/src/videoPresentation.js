import { reactive } from 'vue'

// Separate from refreshed image/timeline payloads so asset polling cannot erase
// an in-progress subtitle-style edit.
export const visualPresentation = reactive({ projectId: '', video_orientation: 'landscape', subtitle_layouts: {} })

export function hydrateVideoPresentation(projectId, request = {}) {
  visualPresentation.projectId = projectId
  visualPresentation.video_orientation = request.video_orientation || 'landscape'
  visualPresentation.subtitle_layouts = JSON.parse(JSON.stringify(request.subtitle_layouts || {}))
}
