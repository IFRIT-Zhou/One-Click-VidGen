"""Shared storyboard/request duration limits; no provider calls or credentials."""
import math


def max_video_duration(parameters=None):
    """Planning preference, not a provider capability (legacy cluster opts in)."""
    parameters = parameters or {}
    if (parameters.get('video_generation_backend') == 'cloud_h3'
            and parameters.get('dynamic_cluster_duration_recommended') is not False):
        return 14
    configured = parameters.get('dynamic_max_shot_duration')
    return configured if type(configured) is int and configured >= 4 else 14


def max_request_duration(parameters=None):
    """Known execution cap; personal provider capabilities are checked by adapters."""
    return 14 if (parameters or {}).get('video_generation_backend') == 'cloud_h3' else None


def generation_duration(duration):
    try:
        seconds = float(duration)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError('镜头时长无效，请重新规划') from exc
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError('镜头时长无效，请重新规划')
    return max(4, math.ceil(seconds))


def validate_video_duration(duration, parameters=None, *, requested_duration=None, planning=False):
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
    maximum = max_video_duration(parameters) if planning else max_request_duration(parameters)
    if maximum is not None and requested > maximum:
        scope = '本次规划的' if planning else '集群通道的'
        raise ValueError(f'本镜视频请求需要 {requested} 秒，超过{scope} {maximum} 秒上限；'
                         '请沿字幕边界拆分，或改用支持该时长的通道；配音与字幕会保留')
    return requested
