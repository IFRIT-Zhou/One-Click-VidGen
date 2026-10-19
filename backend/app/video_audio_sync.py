"""Keep completed visuals when the final narration is edited."""
import copy
import math
import wave

from .final_narration import remap_visual_groups, visual_playback_range


def _signature(file, start, end):
    with wave.open(str(file), 'rb') as stream:
        rate = stream.getframerate()
        stream.setpos(min(stream.getnframes(), round(max(0, start) * rate)))
        return (stream.getnchannels(), stream.getsampwidth(), rate,
                stream.readframes(round(max(0, end-start)*rate)))


def sync_shot_timings(record, scenes, old_audio, new_audio, *, new_duration=None):
    old_scenes = record['scenes']
    old_by_id = {s['slide_id']: s for s in old_scenes}
    by_id = {s['slide_id']: s for s in scenes}
    previous = {s['id']: s for s in record['shots']}
    previous_ranges = {s['id']: visual_playback_range(record['shots'], i, record.get('final_narration', {}).get('duration'))
                       for i, s in enumerate(record['shots'])}
    shots, detached = remap_visual_groups(old_scenes, scenes, record['shots'])
    for shot in shots:
        shot.update(start=by_id[shot['slide_ids'][0]]['start'], end=by_id[shot['slide_ids'][-1]]['end'])
    for index, shot in enumerate(shots):
        original = previous[shot['id']]
        old_text = ''.join(old_by_id[sid]['text'] for sid in original['slide_ids'])
        new_text = ''.join(by_id[sid]['text'] for sid in shot['slide_ids'])
        text_changed = old_text != new_text
        start, end = by_id[shot['slide_ids'][0]]['start'], by_id[shot['slide_ids'][-1]]['end']
        duration = round(end-start, 3)
        visual_duration = visual_playback_range(shots, index, new_duration)['duration']
        previous_duration = previous_ranges[shot['id']]['duration']
        timing_changed = abs(visual_duration-previous_duration) > .01
        try:
            before = _signature(old_audio, original['start'], original['end'])
            after = _signature(new_audio, start, end)
            changed = before != after
            if changed and before[:3] == after[:3] and not timing_changed:
                margin = round(before[2] * .15) * before[0] * before[1]
                interior = before[3][margin:-margin] if margin else before[3]
                if len(interior) > before[2] * before[0] * before[1] * .2:
                    changed = interior not in after[3]
        except (OSError, wave.Error, EOFError):
            changed = True
        if text_changed:
            shot['image_prompt_out_of_sync'] = True
            shot['video_prompt_out_of_sync'] = True
        if text_changed or changed or timing_changed:
            pending = original.get('audio_timing_adjustment') or {}
            shot['audio_timing_adjustment'] = dict(
                mode='trim_or_slow', needs_review=True,
                previous_duration=pending.get('previous_duration', previous_duration) if pending.get('needs_review') else previous_duration,
                target_duration=visual_duration,
                text_changed=text_changed or bool(pending.get('needs_review') and pending.get('text_changed')),
            )
        shot.update(start=start, end=end, duration=duration,
                    source_subtitles=[copy.deepcopy(by_id[sid]) for sid in shot['slide_ids']],
                    generation_duration=max(4, math.ceil(duration)) if shot['kind'] == 'video' else None)
        shot.pop('h3_prompt_source', None)
    record['shots'] = shots
    if detached:
        record.setdefault('audio_retained_shots', []).extend(detached)
    return detached
