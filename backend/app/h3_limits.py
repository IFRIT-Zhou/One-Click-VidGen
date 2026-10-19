"""Measured cluster H3 limits; personal workflows are not restricted here."""
import math


def maximum_dimensions(seconds):
    try:
        seconds = float(seconds)
    except (TypeError, ValueError, OverflowError):
        raise ValueError('集群 H3 请求时长须大于 0 且不超过 15 秒') from None
    if not math.isfinite(seconds) or not 0 < seconds <= 15:
        raise ValueError('集群 H3 最长 15 秒，请拆分分镜后生成')
    return (1532, 832) if seconds > 14 else (1664, 936)


def fits_dimensions(width, height, seconds):
    long, short = maximum_dimensions(seconds)
    return (type(width) is int and type(height) is int
            and 0 < min(width, height) <= short and max(width, height) <= long)


def validate_dimensions(width, height, seconds):
    long, short = maximum_dimensions(seconds)
    if not fits_dimensions(width, height, seconds):
        raise ValueError(f'集群 H3 请求 {seconds} 秒时，尺寸上限为 {long} × {short}（竖屏交换宽高）；'
                         '请重新选择尺寸，或拆分镜头；不会自动缩小已选尺寸')
