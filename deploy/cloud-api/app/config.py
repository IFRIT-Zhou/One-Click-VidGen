from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _csv(name: str, default: str) -> tuple[str, ...]:
    values = tuple(item.strip() for item in os.getenv(name, default).split(",") if item.strip())
    return values or tuple(item.strip() for item in default.split(",") if item.strip())


def _secret_pool(name: str) -> tuple[str, ...]:
    """Read a secret pool without ever formatting its values into logs/errors."""
    values: list[str] = []
    prefix = name.removesuffix("S")
    for raw in (os.getenv(name, ""), os.getenv(prefix, "")):
        values.extend(item.strip() for item in raw.replace(";", ",").split(","))
    for index in range(1, 101):
        value = os.getenv(f"{prefix}_{index}", "").strip()
        if value:
            values.append(value)
    unique: list[str] = []
    for value in values:
        if value and value not in unique:
            unique.append(value)
    return tuple(unique)


def _merge_secret_pools(*names: str) -> tuple[str, ...]:
    values: list[str] = []
    for name in names:
        for value in _secret_pool(name):
            if value not in values:
                values.append(value)
    return tuple(values)


def _alipay_gateway_url() -> str:
    environment = os.getenv("ALIPAY_ENVIRONMENT", "sandbox").lower()
    default = (
        "https://openapi-sandbox.dl.alipaydev.com/gateway.do"
        if environment == "sandbox"
        else "https://openapi.alipay.com/gateway.do"
    )
    return os.getenv("ALIPAY_GATEWAY_URL", default)


@dataclass(frozen=True)
class Settings:
    app_env: str = os.getenv("APP_ENV", "development")
    database_url: str = os.getenv(
        "DATABASE_URL", "sqlite:///./cloud_api.db"
    )
    redis_url: str = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    jwt_secret: str = os.getenv(
        "JWT_SECRET", "development-secret-change-before-production"
    )
    smtp_host: str = os.getenv("SMTP_HOST", "smtp.qq.com")
    smtp_port: int = int(os.getenv("SMTP_PORT", "465"))
    smtp_username: str = os.getenv("SMTP_USERNAME", "")
    smtp_password: str = field(default_factory=lambda: os.getenv("SMTP_PASSWORD", ""), repr=False)
    smtp_sender: str = os.getenv("SMTP_SENDER", "")
    access_token_seconds: int = int(os.getenv("ACCESS_TOKEN_SECONDS", "3600"))
    refresh_token_seconds: int = int(
        os.getenv("REFRESH_TOKEN_SECONDS", "2592000")
    )
    ray_tts_base_url: str = os.getenv(
        "RAY_TTS_BASE_URL", "http://127.0.0.1:8000/api/tts"
    ).rstrip("/")
    ray_tts_api_key: str = os.getenv("RAY_TTS_API_KEY", "")
    ray_cluster_status_url: str = os.getenv(
        "RAY_CLUSTER_STATUS_URL", "http://10.250.0.1:18265"
    ).rstrip("/")
    ray_request_timeout_seconds: int = int(
        os.getenv("RAY_REQUEST_TIMEOUT_SECONDS", "60")
    )
    enrollment_api_base_url: str = os.getenv(
        "ENROLLMENT_API_BASE_URL", "http://10.250.0.3:18443"
    ).rstrip("/")
    enrollment_admin_token: str = os.getenv("ENROLLMENT_ADMIN_TOKEN", "")
    # Optional read-only/control-plane compatibility DSN. Production points this
    # at the local restricted SSH tunnel; it is never exposed to clients.
    enrollment_database_url: str = os.getenv("ENROLLMENT_DATABASE_URL", "")
    enrollment_request_timeout_seconds: int = int(
        os.getenv("ENROLLMENT_REQUEST_TIMEOUT_SECONDS", "15")
    )
    result_ttl_seconds: int = int(os.getenv("RESULT_TTL_SECONDS", "86400"))
    tts_credits_per_200_chars: Decimal = Decimal(
        os.getenv("TTS_CREDITS_PER_200_CHARS", "0.1")
    )
    default_max_concurrent_jobs: int = int(
        os.getenv("DEFAULT_MAX_CONCURRENT_JOBS", "2")
    )
    default_daily_characters: int = int(
        os.getenv("DEFAULT_DAILY_CHARACTERS", "10000")
    )
    default_max_queue_jobs: int = int(
        os.getenv("DEFAULT_MAX_QUEUE_JOBS", "5")
    )
    payment_provider: str = os.getenv("PAYMENT_PROVIDER", "mock")
    mock_payment_secret: str = os.getenv(
        "MOCK_PAYMENT_SECRET", "development-only-secret"
    )
    alipay_environment: str = os.getenv("ALIPAY_ENVIRONMENT", "sandbox").lower()
    alipay_app_id: str = os.getenv("ALIPAY_APP_ID", "")
    alipay_private_key_path: str = os.getenv("ALIPAY_PRIVATE_KEY_PATH", "")
    alipay_public_key_path: str = os.getenv("ALIPAY_PUBLIC_KEY_PATH", "")
    alipay_gateway_url: str = _alipay_gateway_url()
    alipay_notify_url: str = os.getenv(
        "ALIPAY_NOTIFY_URL",
        "https://oneclickvidgen.com/api/v1/payments/alipay/notify",
    )
    alipay_return_url: str = os.getenv(
        "ALIPAY_RETURN_URL",
        "https://oneclickvidgen.com/recharge/?alipay_return=1",
    )
    alipay_seller_id: str = os.getenv("ALIPAY_SELLER_ID", "")
    allow_registration: bool = _bool("ALLOW_REGISTRATION", True)
    auto_create_schema: bool = _bool("AUTO_CREATE_SCHEMA", False)
    preset_voice_ids: tuple[str, ...] = _csv(
        "PRESET_VOICE_IDS",
        "voice_01.wav,voice_02.wav,voice_03.wav,voice_04.wav,voice_05.wav,"
        "voice_06.wav,voice_07.wav,voice_08.wav,voice_09.wav,voice_11.wav,voice_12.wav",
    )
    preset_voice_dir: str = os.getenv(
        "PRESET_VOICE_DIR", "/opt/cloud-api/preset-voices"
    )
    max_voice_bytes: int = int(os.getenv("MAX_VOICE_BYTES", str(20 * 1024 * 1024)))
    max_user_voices: int = int(os.getenv("MAX_USER_VOICES", "20"))
    model_pool_api_keys: tuple[str, ...] = field(
        default_factory=lambda: _secret_pool("MODEL_POOL_API_KEYS"), repr=False
    )
    model_pool_base_url: str = os.getenv(
        "MODEL_POOL_BASE_URL", "https://llm.runninghub.ai/v1"
    ).rstrip("/")
    model_pool_models: tuple[str, ...] = _csv(
        "MODEL_POOL_MODELS", "google/gemini-3.1-flash-lite-preview"
    )
    model_pool_timeout_seconds: int = int(os.getenv("MODEL_POOL_TIMEOUT_SECONDS", "150"))
    model_pool_per_account_concurrency: int = int(
        os.getenv("MODEL_POOL_PER_ACCOUNT_CONCURRENCY", "4")
    )
    billing_usd_to_cny_rate: Decimal = Decimal(
        os.getenv("BILLING_USD_TO_CNY_RATE", "6.75")
    )
    model_pool_input_usd_per_1000_tokens: Decimal = Decimal(
        os.getenv("MODEL_POOL_INPUT_USD_PER_1000_TOKENS", "0.000125")
    )
    model_pool_output_usd_per_1000_tokens: Decimal = Decimal(
        os.getenv("MODEL_POOL_OUTPUT_USD_PER_1000_TOKENS", "0.000750")
    )
    model_pool_markup_percent: int = int(
        os.getenv("MODEL_POOL_MARKUP_PERCENT", "5")
    )
    model_pool_account_cooldown_seconds: int = int(
        os.getenv("MODEL_POOL_ACCOUNT_COOLDOWN_SECONDS", "60")
    )
    runninghub_image_api_keys: tuple[str, ...] = field(
        default_factory=lambda: _merge_secret_pools(
            "RUNNINGHUB_IMAGE_API_KEYS", "RUNNINGHUB_API_KEYS"
        ),
        repr=False,
    )
    ican_image_api_keys: tuple[str, ...] = field(
        default_factory=lambda: _secret_pool("ICAN_API_KEYS"), repr=False,
    )
    ican_image_model: str = os.getenv("ICAN_IMAGE_MODEL", "gpt-image-2.5")
    # Explicit, operator-approved prices per model and size; no Image 2 fallback.
    ican_image_prices_json: str = os.getenv("ICAN_IMAGE_PRICES_JSON", "{}")
    ican_image_markup_percent: int = int(os.getenv("ICAN_IMAGE_MARKUP_PERCENT", "5"))
    ican_image_timeout_seconds: int = int(os.getenv("ICAN_IMAGE_TIMEOUT_SECONDS", "300"))
    ican_image_concurrency: int = int(os.getenv("ICAN_IMAGE_CONCURRENCY", "2"))
    ican_image_storage_dir: str = os.getenv("ICAN_IMAGE_STORAGE_DIR", "/var/lib/cloud-api/ican-images")
    ican_image_max_bytes: int = int(os.getenv("ICAN_IMAGE_MAX_BYTES", str(20 * 1024 * 1024)))
    ican_image_download_hosts: tuple[str, ...] = _csv("ICAN_IMAGE_DOWNLOAD_HOSTS", "")
    runninghub_image_base_url: str = os.getenv(
        "RUNNINGHUB_IMAGE_BASE_URL", "https://www.runninghub.ai/openapi/v2"
    ).rstrip("/")
    runninghub_text_to_image_endpoint: str = os.getenv(
        "RUNNINGHUB_TEXT_TO_IMAGE_ENDPOINT", "/rhart-image-g-2/text-to-image"
    )
    runninghub_image_to_image_endpoint: str = os.getenv(
        "RUNNINGHUB_IMAGE_TO_IMAGE_ENDPOINT", "/rhart-image-g-2/image-to-image"
    )
    runninghub_query_endpoint: str = os.getenv(
        "RUNNINGHUB_QUERY_ENDPOINT", "/query"
    )
    runninghub_media_upload_endpoint: str = os.getenv(
        "RUNNINGHUB_MEDIA_UPLOAD_ENDPOINT", "/media/upload/binary"
    )
    runninghub_download_hosts: tuple[str, ...] = _csv(
        "RUNNINGHUB_DOWNLOAD_HOSTS", ""
    )
    image_pool_request_timeout_seconds: int = int(
        os.getenv("IMAGE_POOL_REQUEST_TIMEOUT_SECONDS", "120")
    )
    image_pool_max_upload_bytes: int = int(
        os.getenv("IMAGE_POOL_MAX_UPLOAD_BYTES", str(20 * 1024 * 1024))
    )
    image_pool_per_account_concurrency: int = int(
        os.getenv("IMAGE_POOL_PER_ACCOUNT_CONCURRENCY", "2")
    )
    image_pool_max_user_jobs: int = int(os.getenv("IMAGE_POOL_MAX_USER_JOBS", "100"))
    image_pool_default_upstream_cost_usd: Decimal = Decimal(
        os.getenv("IMAGE_POOL_DEFAULT_UPSTREAM_COST_USD", "0.015")
    )
    image_pool_max_upstream_cost_usd: Decimal = Decimal(
        os.getenv("IMAGE_POOL_MAX_UPSTREAM_COST_USD", "0.030")
    )
    image_pool_markup_percent: int = int(os.getenv("IMAGE_POOL_MARKUP_PERCENT", "5"))
    pool_worker_lease_seconds: int = int(os.getenv("POOL_WORKER_LEASE_SECONDS", "180"))
    pool_poll_max_retries: int = int(os.getenv("POOL_POLL_MAX_RETRIES", "8"))
    pool_poll_timeout_seconds: int = int(os.getenv("POOL_POLL_TIMEOUT_SECONDS", "900"))
    provider_account_cooldown_seconds: int = int(
        os.getenv("PROVIDER_ACCOUNT_COOLDOWN_SECONDS", "60")
    )
    video_storage_dir: str = os.getenv(
        "VIDEO_STORAGE_DIR", "/var/lib/cloud-api/video-jobs"
    )
    video_asset_max_bytes: int = int(
        os.getenv("VIDEO_ASSET_MAX_BYTES", str(100 * 1024 * 1024))
    )
    video_result_max_bytes: int = int(
        os.getenv("VIDEO_RESULT_MAX_BYTES", str(2 * 1024 * 1024 * 1024))
    )
    video_worker_concurrency: int = int(os.getenv("VIDEO_WORKER_CONCURRENCY", "2"))
    video_worker_poll_seconds: float = float(os.getenv("VIDEO_WORKER_POLL_SECONDS", "1"))
    video_action_lease_seconds: int = int(os.getenv("VIDEO_ACTION_LEASE_SECONDS", "180"))
    video_compose_lease_seconds: int = int(os.getenv("VIDEO_COMPOSE_LEASE_SECONDS", "3600"))
    # Retry only transport/storage failures at the current durable step. The
    # provider-specific state machines remain responsible for provider errors.
    video_step_max_retries: int = int(os.getenv("VIDEO_STEP_MAX_RETRIES", "2"))
    video_retry_backoff_seconds: float = float(
        os.getenv("VIDEO_RETRY_BACKOFF_SECONDS", "2")
    )

    def validate(self) -> None:
        if not 0 <= self.ican_image_markup_percent <= 100:
            raise RuntimeError("ICAN_IMAGE_MARKUP_PERCENT must be between 0 and 100")
        if not 1 <= self.ican_image_concurrency <= 16 or not 30 <= self.ican_image_timeout_seconds <= 900:
            raise RuntimeError("Invalid ICAN concurrency or timeout")
        if not 1 <= self.ican_image_max_bytes <= 50 * 1024 * 1024:
            raise RuntimeError("Invalid ICAN image byte limit")
        if self.app_env == "production":
            if len(self.jwt_secret) < 32:
                raise RuntimeError("JWT_SECRET must contain at least 32 characters")
            if self.payment_provider == "mock":
                raise RuntimeError("Mock payment provider is disabled in production")
            if not self.ray_tts_api_key:
                raise RuntimeError("RAY_TTS_API_KEY is required in production")
        if len(set(self.preset_voice_ids)) != len(self.preset_voice_ids):
            raise RuntimeError("PRESET_VOICE_IDS must not contain duplicate values")
        if self.billing_usd_to_cny_rate <= 0:
            raise RuntimeError("BILLING_USD_TO_CNY_RATE must be positive")
        if not 0 <= self.image_pool_markup_percent <= 100:
            raise RuntimeError("IMAGE_POOL_MARKUP_PERCENT must be between 0 and 100")
        if not 0 <= self.model_pool_markup_percent <= 100:
            raise RuntimeError("MODEL_POOL_MARKUP_PERCENT must be between 0 and 100")
        if self.image_pool_default_upstream_cost_usd <= 0:
            raise RuntimeError("IMAGE_POOL_DEFAULT_UPSTREAM_COST_USD must be positive")
        if self.image_pool_max_upstream_cost_usd < self.image_pool_default_upstream_cost_usd:
            raise RuntimeError("IMAGE_POOL_MAX_UPSTREAM_COST_USD must cover the default cost")
        if self.model_pool_input_usd_per_1000_tokens <= 0:
            raise RuntimeError("MODEL_POOL_INPUT_USD_PER_1000_TOKENS must be positive")
        if self.model_pool_output_usd_per_1000_tokens <= 0:
            raise RuntimeError("MODEL_POOL_OUTPUT_USD_PER_1000_TOKENS must be positive")
        if self.tts_credits_per_200_chars <= 0:
            raise RuntimeError("TTS_CREDITS_PER_200_CHARS must be positive")
        if self.tts_credits_per_200_chars.as_tuple().exponent < -1:
            raise RuntimeError("TTS_CREDITS_PER_200_CHARS supports at most one decimal place")
        if self.model_pool_per_account_concurrency <= 0:
            raise RuntimeError("MODEL_POOL_PER_ACCOUNT_CONCURRENCY must be positive")
        if self.video_asset_max_bytes <= 0 or self.video_result_max_bytes <= 0:
            raise RuntimeError("Video storage byte limits must be positive")
        if self.video_worker_concurrency <= 0 or self.video_worker_concurrency > 16:
            raise RuntimeError("VIDEO_WORKER_CONCURRENCY must be between 1 and 16")
        if self.video_worker_poll_seconds <= 0:
            raise RuntimeError("VIDEO_WORKER_POLL_SECONDS must be positive")
        if self.video_action_lease_seconds <= 0 or self.video_compose_lease_seconds <= 0:
            raise RuntimeError("Video worker lease durations must be positive")
        if self.video_step_max_retries < 0 or self.video_step_max_retries > 10:
            raise RuntimeError("VIDEO_STEP_MAX_RETRIES must be between 0 and 10")
        if self.video_retry_backoff_seconds < 0:
            raise RuntimeError("VIDEO_RETRY_BACKOFF_SECONDS must not be negative")
        if self.pool_poll_max_retries < 1:
            raise RuntimeError("POOL_POLL_MAX_RETRIES must be positive")
        if self.pool_poll_timeout_seconds <= 0:
            raise RuntimeError("POOL_POLL_TIMEOUT_SECONDS must be positive")
        if any(any(marker in host for marker in ("/", ":", "*", " ")) for host in self.runninghub_download_hosts):
            raise RuntimeError("RUNNINGHUB_DOWNLOAD_HOSTS must contain exact hostnames only")
        if self.payment_provider == "alipay":
            if self.alipay_environment not in {"sandbox", "production"}:
                raise RuntimeError("ALIPAY_ENVIRONMENT must be sandbox or production")
            required = {
                "ALIPAY_APP_ID": self.alipay_app_id,
                "ALIPAY_PRIVATE_KEY_PATH": self.alipay_private_key_path,
                "ALIPAY_PUBLIC_KEY_PATH": self.alipay_public_key_path,
                "ALIPAY_GATEWAY_URL": self.alipay_gateway_url,
                "ALIPAY_NOTIFY_URL": self.alipay_notify_url,
                "ALIPAY_RETURN_URL": self.alipay_return_url,
            }
            missing = [name for name, value in required.items() if not value]
            if missing:
                raise RuntimeError(f"Missing Alipay settings: {', '.join(missing)}")


settings = Settings()
