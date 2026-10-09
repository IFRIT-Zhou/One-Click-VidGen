from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import (AliasChoices, BaseModel, ConfigDict, EmailStr, Field,
                      field_validator, model_validator)


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=200)
    captcha_token: str | None = None
    source: Literal[1, 2] = 1


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class UserPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    status: str
    role: str
    source: int = 1
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    refresh_token: str
    user: UserPublic


class TextChunk(BaseModel):
    index: int = Field(ge=0)
    text: str = Field(min_length=1, max_length=1000)


class VoiceSpec(BaseModel):
    type: Literal["preset", "uploaded"] = "preset"
    id: str = Field(min_length=1, max_length=180)


class EmotionSpec(BaseModel):
    name: Literal[
        "happy", "angry", "sad", "afraid", "disgusted",
        "melancholic", "surprised", "calm",
    ] | None = None
    weight: float = Field(default=0.65, ge=0, le=1)


class AudioSpec(BaseModel):
    speed: float = Field(default=1, ge=0.5, le=2)
    volume: float = Field(default=1, ge=0.1, le=10)
    pitch: int = Field(default=0, ge=-12, le=12)
    sample_rate: int = Field(default=24000, ge=8000, le=48000)
    channels: Literal[1, 2] = 1


class QuoteRequest(BaseModel):
    chunks: list[TextChunk] = Field(min_length=1, max_length=20)
    voice: VoiceSpec
    emotion: EmotionSpec = Field(default_factory=EmotionSpec)
    audio: AudioSpec = Field(default_factory=AudioSpec)
    gpu_acceleration: bool = True


class CloudJobRequest(QuoteRequest):
    client_job_id: str = Field(min_length=1, max_length=180)


class VideoVoiceSpec(BaseModel):
    type: Literal["preset", "user"]
    id: str = Field(min_length=1, max_length=180)


class VideoOutputSpec(BaseModel):
    aspect_ratio: Literal["16:9", "9:16", "1:1", "2:1"] = "16:9"
    method: Literal["running", "ican"] = "running"
    resolution: Literal["1k", "2k", "2.5k", "4k"] | None = None
    size: str | None = None

    @model_validator(mode="after")
    def validate_image_channel(self):
        if self.method == "ican":
            choices = {
                "16:9": {"2k": "2048x1152", "2.5k": "2560x1440"},
                "9:16": {"2k": "1152x2048", "2.5k": "1440x2560"},
                "1:1": {"1k": "1024x1024"},
            }.get(self.aspect_ratio)
            if not choices:
                raise ValueError("ICAN 不支持此视频画幅")
            self.resolution = self.resolution or ("2.5k" if "2.5k" in choices else "1k")
            if self.resolution not in choices:
                raise ValueError("ICAN 不支持此画幅与清晰度组合")
            expected = choices[self.resolution]
            if self.size is not None and self.size != expected:
                raise ValueError("图片尺寸与画幅、清晰度不一致")
            self.size = expected
        else:
            self.resolution = self.resolution or "1k"
            if self.resolution not in {"1k", "2k", "4k"} or self.size is not None:
                raise ValueError("RunningHub 使用 1K、2K 或 4K 清晰度")
        return self
    scene_count: int = Field(default=6, ge=1, le=16)
    visual_style: str = Field(default="cinematic", min_length=1, max_length=200)


class VideoAudioSpec(BaseModel):
    speed: float = Field(default=1, ge=0.5, le=2)
    pitch: int = Field(default=0, ge=-12, le=12)
    emotion: str | None = Field(default=None, max_length=64)
    emotion_weight: float = Field(default=0.65, ge=0, le=1)


class VideoStoryboardScene(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    index: int = Field(ge=0, le=15)
    title: str = Field(min_length=1, max_length=160)
    narration: str = Field(min_length=1, max_length=1500)
    description: str = Field(default="", max_length=2000)
    prompt: str = Field(min_length=1, max_length=4000)


class VideoJobQuoteRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    script: str = Field(
        validation_alias=AliasChoices("script", "text"), min_length=1, max_length=5_000
    )
    client_job_id: str | None = Field(default=None, min_length=1, max_length=180)
    voice: VideoVoiceSpec
    video: VideoOutputSpec = Field(default_factory=VideoOutputSpec)
    audio: VideoAudioSpec = Field(default_factory=VideoAudioSpec)
    storyboard: list[VideoStoryboardScene] | None = Field(default=None, min_length=1, max_length=16)
    reference_image_urls: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("reference_image_urls", "referenceImageUrls"),
        max_length=3,
    )

    @field_validator("reference_image_urls")
    @classmethod
    def reference_images_must_be_nonempty(cls, value: list[str]) -> list[str]:
        return [url.strip() for url in value if url.strip()]

    @model_validator(mode="after")
    def validate_storyboard(self) -> VideoJobQuoteRequest:
        if self.storyboard is None:
            if self.video.scene_count < 2:
                raise ValueError("自动分镜至少需要 2 个镜头")
            return self
        if len(self.storyboard) != self.video.scene_count:
            raise ValueError("镜头数量与提交的分镜不一致")
        if [scene.index for scene in self.storyboard] != list(range(len(self.storyboard))):
            raise ValueError("分镜编号必须从 0 开始连续排列")
        narration = "".join(scene.narration for scene in self.storyboard)
        if "".join(narration.split()) != "".join(self.script.split()):
            raise ValueError("分镜旁白必须按顺序完整保留原文")
        return self


class VideoJobCreateRequest(VideoJobQuoteRequest):
    client_job_id: str = Field(min_length=1, max_length=180)


class RechargeRequest(BaseModel):
    product_id: str = Field(min_length=1, max_length=64)
    payment_provider: str = Field(min_length=1, max_length=32)


class PaymentNotifyRequest(BaseModel):
    event_id: str = Field(min_length=1, max_length=160)
    order_id: str = Field(min_length=1, max_length=40)
    provider_order_id: str = Field(min_length=1, max_length=160)
    amount_fen: int = Field(gt=0)
    status: Literal["paid", "refunded"]


class QuotaUpdateRequest(BaseModel):
    max_concurrent_jobs: int = Field(ge=0, le=100)
    daily_characters_limit: int = Field(ge=0, le=10_000_000)
    max_queue_jobs: int = Field(ge=0, le=1000)


class CreditAdjustRequest(BaseModel):
    amount: Decimal = Field(max_digits=18, decimal_places=6)
    reason: str = Field(min_length=2, max_length=500)
    idempotency_key: str = Field(min_length=1, max_length=160)

    @field_validator("amount")
    @classmethod
    def amount_must_not_be_zero(cls, value: Decimal) -> Decimal:
        if value == 0:
            raise ValueError("amount must not be zero")
        return value


class EnrollmentTokenRequest(BaseModel):
    device_id: str | None = Field(default=None, min_length=16, max_length=128,
                                  pattern=r"^[A-Za-z0-9_-]+$")
    profile_id: str = Field(min_length=3, max_length=64, pattern=r"^[a-z0-9-]+$")
    expires_in_seconds: int = Field(default=900, ge=60, le=3600)


class EnrollmentAdminActionRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)
    idempotency_key: str = Field(min_length=16, max_length=160)


class ErrorResponse(BaseModel):
    code: str
    message: str
    request_id: str
    details: dict[str, Any] = Field(default_factory=dict)


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | list[dict[str, Any]]
    name: str | None = None


class ModelPoolCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str = "auto"
    messages: list[ChatMessage] = Field(min_length=1, max_length=200)
    max_tokens: int | None = Field(default=None, ge=1, le=65536)
    temperature: float | None = Field(default=None, ge=0, le=2)


class ImagePoolGenerateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    provider: Literal["runninghub", "ican"] = "runninghub"
    method: Literal["running", "ican"] | None = None
    model: str | None = Field(default=None, max_length=100)
    size: str | None = Field(default=None, max_length=32)
    prompt: str = Field(min_length=1, max_length=20_000)
    aspect_ratio: str = Field(default="2:1", alias="aspectRatio", min_length=1, max_length=32)
    resolution: str = Field(default="1k", min_length=1, max_length=32)
    image_urls: list[str] = Field(default_factory=list, alias="imageUrls", max_length=4)
    client_task_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("clientJobId", "client_job_id", "clientTaskId"),
        serialization_alias="clientJobId",
        max_length=180,
        description="Optional client request ID (8-180 characters when nonblank). Omitted, null, empty or whitespace-only values let the server generate an ID. Reuse an explicit ID to deduplicate retries.",
    )

    @model_validator(mode="before")
    @classmethod
    def resolve_image_channel(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        method = data.get("method")
        if method is not None:
            if method not in ("running", "ican"):
                raise ValueError("method must be running or ican")
            provider = "ican" if method == "ican" else "runninghub"
            if "provider" in data and data["provider"] != provider:
                raise ValueError("method and provider must select the same channel")
            data["provider"] = provider
        if data.get("provider") == "ican":
            if data.get("model") not in (None, "gpt-image-2.5"):
                raise ValueError("ICAN only supports gpt-image-2.5")
            size = str(data.get("size") or "2560x1440").strip().lower().replace("×", "x")
            if size not in {"1024x1024", "1536x1024", "1024x1536", "2048x1152",
                            "1152x2048", "2560x1440", "1440x2560"}:
                raise ValueError("ICAN size must be one of the seven regular sizes; experimental sizes are not enabled")
            data["size"] = size
        return data

    @field_validator("client_task_id", mode="before")
    @classmethod
    def empty_client_task_id(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        if isinstance(value, str) and len(value) < 8:
            raise ValueError("Nonblank clientJobId must contain at least 8 characters")
        return value


class ImagePoolQueryRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    task_id: str = Field(alias="taskId", min_length=1, max_length=160)


class CreativeProjectCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    document_json: dict[str, Any]

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("title must not be blank")
        return value


class CreativeProjectUpdateRequest(CreativeProjectCreateRequest):
    expected_revision: int = Field(ge=1)


class CreativeProjectSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    revision: int
    created_at: datetime
    updated_at: datetime


class CreativeProjectDetail(CreativeProjectSummary):
    document_json: dict[str, Any]
