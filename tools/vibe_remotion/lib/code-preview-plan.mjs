/** Trusted preview work plan. Layout checks are never temporal delivery proof. */
import {codeProofFrames} from './code-render-contract.mjs';

export function codePreviewPlan({mode, preview_level = 'full', duration, fps = 30}) {
  if (!['measure', 'preview', 'video'].includes(mode)) throw new Error('OCV_CODE_CONTRACT: Invalid render mode');
  if (!['layout', 'full'].includes(preview_level)) throw new Error('OCV_CODE_CONTRACT: preview_level must be layout or full');
  const level = mode === 'video' ? 'full' : preview_level;
  const temporal = mode !== 'measure' && level === 'full';
  return {
    preview_level: level,
    proof_complete: temporal,
    animation_check: temporal ? 'checked' : 'not_checked',
    stage_frames: temporal ? codeProofFrames(duration, fps) : [],
  };
}
