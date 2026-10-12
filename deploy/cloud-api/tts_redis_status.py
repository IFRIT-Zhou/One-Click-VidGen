"""Read-only queue depth plus existing authenticated drain status.

Works without redeploying Ray Serve. Redis access is private to the head host.
Only the two Redis connection settings are read; they are never exported or
included in errors. It uses the same simple KEY=value parsing as start-head.sh.
"""
from pathlib import Path
from gpu_scheduler import TTSHTTP


class TTSRedisHTTP(TTSHTTP):
    def __init__(self, *args, ray_env="/opt/ray-tts/head/.env", redis_client=None, **kwargs):
        super().__init__(*args, **kwargs)
        if redis_client is None:
            import redis
            selected = {}
            for line in Path(ray_env).read_text().splitlines():
                if line.startswith(("REDIS_URL=", "REDIS_PREFIX=")):
                    key, value = line.split("=", 1)
                    selected[key] = value
            if not selected.get("REDIS_URL"):
                raise RuntimeError("Ray Redis configuration is unavailable")
            self.redis = redis.Redis.from_url(selected["REDIS_URL"], decode_responses=True,
                                               socket_connect_timeout=3, socket_timeout=5)
            self.prefix = selected.get("REDIS_PREFIX", "raytts")
        else:
            self.redis, self.prefix = redis_client, "raytts"

    def snapshot(self):
        status = self._request("drain")
        # HTTP and Redis exceptions propagate: scheduler cannot infer idle from
        # an unavailable control plane or unknown queue contents.
        depth = self.redis.llen(f"{self.prefix}:queue")
        return {**status, "queue_depth": int(depth), "redis_ready": True}
