"""Shared storyboard/request duration limits; no provider calls or credentials."""
import math


def max_video_duration(parameters=None):
    parameters = parameters or {}
    # The cluster H3 API currently advertises a hard ten-second maximum.
    # A saved personal-workflow preference must never override that capability.
    if parameters.get('video_generation_backend') == 'cloud_h3':
        return 10
    configured = parameters.get('dynamic_max_shot_duration')
    return configured if configured in (10, 15, 30) else 15


def generation_duration(duration):
    try:
        seconds = float(duration)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError('镜头时长无效，请重新规划') from exc
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError('镜头时长无效，请重新规划')
    return max(4, math.ceil(seconds))


def validate_video_duration(duration, parameters=None, *, requested_duration=None):
    """Validate the submitted whole seconds without truncating narration."""
    required = generation_duration(duration)
    requested = required
    if requested_duration is not None:
        try:
            supplied = float(requested_duration)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError('视频请求时长无效，请重新规划') from exc
        if not math.isfinite(supplied) or not supplied.is_integer():
            raise ValueError('视频请求时长必须是整数秒，请重新规划')
        requested = int(supplied)
        if requested < required:
            raise ValueError('视频请求时长小于字幕跨度或最低4秒，请重新规划')
    maximum = max_video_duration(parameters)
    if requested > maximum:
        raise ValueError(f'本镜视频请求需要 {requested} 秒，超过当前通道的 {maximum} 秒上限；'
                         '请重新规划，沿字幕边界拆分后再生成，配音与字幕会保留')
    return requested
