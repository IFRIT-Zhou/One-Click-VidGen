"""Publish closed Vibe artifacts on the same filesystem, without re-rendering."""
import time


def publish(source, destination, *, replace=False):
    for delay in (.04, .08, .12, .2, .32, .48, .65, None):
        try:
            return source.replace(destination) if replace else source.rename(destination)
        except OSError as exc:
            # Scanners/thumbnail handlers can briefly lock a file or directory
            # after ffmpeg exits. Never replace an existing immutable attempt.
            if delay is None or getattr(exc,'winerror',None) not in (5,32,33):
                raise
            if not replace and destination.exists():
                raise
            time.sleep(delay)
