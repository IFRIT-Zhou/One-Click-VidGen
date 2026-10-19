"""Bound one image's local wait, including retries, without claiming remote cancellation."""
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
import os
import threading
import time


class ImageWaitStopped(RuntimeError):
    def __init__(self):
        super().__init__('已停止本地图片等待，已完成图片保留。云端已接收的任务可能仍在运行或计费；重试前请核对，未自动重复提交。')


class ImageWaitTimeout(RuntimeError):
    pass


_CURRENT = ContextVar('image_request_control', default=None)


class _Control:
    def __init__(self, cancel_event, label, timeout):
        self.cancel_event = cancel_event
        self.label = label or '图片'
        self.timeout = timeout
        self.deadline = time.monotonic() + timeout
        self.cleanups = []

    def check(self):
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise ImageWaitStopped()
        if time.monotonic() >= self.deadline:
            raise self.timeout_error()

    def timeout_error(self):
        return ImageWaitTimeout(
            f'{self.label} 等待超过 {self.timeout:g} 秒，已结束本地等待并停止自动重试。'
            '已完成图片保留；可切换号池或 API 后手动重试未完成项。'
            '云端任务可能仍在运行或计费，请先核对任务结果，避免重复付费。')


@contextmanager
def scope(*, cancel_event=None, label='', timeout=None):
    """Nested helpers share a deadline; retries must never reset it."""
    if _CURRENT.get() is not None:
        yield
        return
    if timeout is None:
        try:
            timeout = float(os.getenv('OCV_IMAGE_TASK_TIMEOUT_SECONDS', '300'))
        except ValueError:
            timeout = 300
    if not 0 < timeout < float('inf'):
        timeout = 300
    control = _Control(cancel_event, label, timeout)
    token = _CURRENT.set(control)
    try:
        yield
    finally:
        try:
            for cleanup in reversed(control.cleanups):
                cleanup()
        finally:
            _CURRENT.reset(token)


def check():
    control = _CURRENT.get()
    if control is not None:
        control.check()


def on_exit(cleanup):
    control = _CURRENT.get()
    if control is not None:
        control.cleanups.append(cleanup)


def wait(seconds):
    control = _CURRENT.get()
    if control is None:
        time.sleep(seconds)
        return
    check()
    delay = min(max(0.0, seconds), max(0.0, control.deadline - time.monotonic()))
    if control.cancel_event is not None:
        control.cancel_event.wait(delay)
    else:
        time.sleep(delay)
    check()
    # Some Windows clocks/sleeps round down by a tick. A backoff truncated by
    # the budget must not sneak in one more paid submission on that last tick.
    if delay < seconds:
        raise control.timeout_error()


@contextmanager
def locked(lock):
    while not lock.acquire(timeout=0.1):
        check()
    try:
        check()
        yield
    finally:
        lock.release()


def request(operation):
    """Only the blocking HTTP operation runs separately, never asset/record writes.

    requests cannot abort an in-flight socket. Stop waiting promptly, discard a
    late response, and let the finite transport timeout retire that daemon.
    The copied control also prevents auth/retry code from submitting after stop.
    """
    if _CURRENT.get() is None:
        return operation()
    check()
    done = threading.Event()
    lock = threading.Lock()
    state = {'abandoned': False}
    context = copy_context()

    def run():
        try:
            check()
            value = operation()
            with lock:
                if state['abandoned']:
                    if hasattr(value, 'close'):
                        value.close()
                else:
                    state['value'] = value
        except BaseException as exc:
            with lock:
                state['error'] = exc
        finally:
            done.set()

    threading.Thread(target=lambda: context.run(run), daemon=True,
                     name='image-http-wait').start()
    try:
        while not done.wait(0.1):
            check()
        check()
        if 'error' in state:
            raise state['error']
        return state['value']
    except BaseException:
        with lock:
            state['abandoned'] = True
            value = state.pop('value', None)
            if hasattr(value, 'close'):
                value.close()
        raise
