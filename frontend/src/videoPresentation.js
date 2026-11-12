import { reactive } from 'vue'

// Separate from refreshed image/timeline payloads so asset polling cannot erase
// an in-progress subtitle-style edit.
export const visualPresentation = reactive({ projectId: '', video_orientation: 'landscape', subtitle_layouts: {}, image_transition:'none', intro_video_id:'', outro_video_id:'' })

export function hydrateVideoPresentation(projectId, request = {}) {
  visualPresentation.projectId = projectId
  visualPresentation.intro_video_id = request.intro_video_id || ''
  visualPresentation.outro_video_id = request.outro_video_id || ''
  visualPresentation.image_transition = request.image_transition || 'none'
  visualPresentation.video_orientation = request.video_orientation || 'landscape'
  visualPresentation.subtitle_layouts = JSON.parse(JSON.stringify(request.subtitle_layouts || {}))
}
