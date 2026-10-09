"""Server-owned prompts for the free, authenticated storyboard workflow."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from .schemas import ModelPoolCompletionRequest


class StoryboardStageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stage: Literal[0, 1, 2]
    input: str = Field(min_length=1, max_length=60000)
    style: str = Field(default="电影感写实摄影", min_length=1, max_length=200)
    count: int = Field(default=6, ge=1, le=16)
    exact_count: bool = False
    repair_error: str | None = Field(default=None, max_length=1000)


def stage_payload(request: StoryboardStageRequest) -> ModelPoolCompletionRequest:
    prompts = [
        '你是视频策划 Agent 0。通读文案，返回严格 JSON：{"theme":"","tone":"","characters":[],"locations":[],"visual_continuity":""}。主题、语气、连续性说明不能为空。不要 Markdown。',
        f'你是视频分镜 Agent 1。根据叙事转折、主体动作和地点变化规划连续镜头，最多 16 个。按预计配音时长建议约 {request.count} 个镜头，这只是节奏参考，允许根据内容调整数量。普通短文每镜约 4–8 秒，避免把多个事件堆进一个长镜头；长文达到 16 镜上限时优先保证内容完整和语义边界。每个镜头必须保留原文旁白，所有 narration 按顺序拼接须与原文一致（允许空白差异）。返回严格 JSON：{{"scenes":[{{"title":"","narration":"","description":"镜头主体、动作、环境和构图"}}]}}。不要 Markdown。',
        f'你是画面提示词 Agent 2。为每个镜头生成一条可直接生图的中文提示词，保持人物、时代和地点连续，统一采用“{request.style}”。不要在画面中生成文字、水印或标志。返回严格 JSON：{{"scenes":[{{"index":0,"prompt":""}}]}}。每个镜头编号从 0 开始，不能重复或遗漏。不要 Markdown。',
    ]
    system = prompts[request.stage]
    if request.stage == 1 and request.exact_count:
        system = system.replace(
            f"按预计配音时长建议约 {request.count} 个镜头，这只是节奏参考，允许根据内容调整数量。",
            f"用户指定严格生成 {request.count} 个镜头，不得增减；每镜旁白最多 900 字，须在指定镜头数内完整保留全文。",
        )
    if request.repair_error:
        system += f"\n上次返回未通过校验：{request.repair_error}。请重新生成满足全部约束的严格 JSON。"
    return ModelPoolCompletionRequest(model="auto", temperature=0.45, messages=[
        {"role":"system", "content":system}, {"role":"user", "content":request.input},
    ])
