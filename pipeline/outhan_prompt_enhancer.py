"""Outhan Prompt 增强器 — 用 Gemini 把用户输入按目标模型偏好改写。

[AI-NOTE] 2026-05-07: 移植自 Outhan/D:\\AIvideo/src/lib/prompt-enhancer.ts。
不同模型吃不同 prompt 风格——同一句"橘猫散步"在 Seedream / Seedance / Vidu 出来效果差异巨大。
默认开启；如失败（密钥/网络/审核）静默回退到原 prompt，不阻塞主链路。
"""
from __future__ import annotations

from typing import Literal, Optional

from pipeline.tools import llm_client


Target = Literal["seedream", "seedance", "vidu"]


SYSTEM_PROMPTS: dict[str, str] = {
    "seedream": """你是 AI 图像生成提示词专家。任务：把用户的简短中文描述，改写成 Doubao Seedream 模型喜欢的高质量中文提示词。

要求：
- 中文输出，自然流畅，不要清单或编号
- 必须覆盖：主体（明确特征）、动作/情绪、环境（时间/地点/天气）、光线（光源、明暗对比）、构图（景别/视角/留白）、色彩（主色调）、风格（写实/电影感/水墨/插画等）
- 长度 80-150 个汉字
- 用户给参考图时，先描述参考图的视觉特征再融入
- 直接输出改写后的提示词，不要任何前缀、引号、解释

记住：保留用户的核心意图，不要发挥成完全不同的画面。""",

    "seedance": """你是 AI 视频生成提示词专家。任务：把用户的简短中文描述，改写成 Seedance 模型喜欢的高质量中文视频提示词。

要求：
- 中文输出，自然流畅
- 必须强调"动起来"——视频是连续动作不是静止画面
- 必须覆盖：主体特征 + 主体的具体动作（动词要具体）+ 镜头景别（远景/中景/近景/特写）+ 镜头运动（推/拉/摇/移/跟/升降，可选）+ 环境氛围 + 光线 + 风格
- 长度 100-180 个汉字
- 不要写参数 flag（如 --ratio --duration），那由系统自动加
- 直接输出改写后的提示词，不要前缀、引号、解释

记住：动作要可视化（"走"不如"踱步""碎步小跑""猫腰前进"）。""",

    "vidu": """你是 AI 视频生成提示词专家。任务：把用户的简短中文描述，改写成 Vidu 国内版模型喜欢的中文视频提示词。

要求：
- 中文，简洁但画面感强
- Vidu 偏爱结构：镜头景别 + 主体的具体动作 + 环境光线氛围 + 风格
- 长度 80-150 个汉字
- 比 Seedance 更紧凑，少冗词
- 直接输出，不要前缀、引号、解释""",
}


def enhance(
    target: Target,
    prompt: str,
    *,
    reference_image: Optional[dict[str, str]] = None,
) -> Optional[str]:
    """用 Gemini 改写 prompt。失败返回 None，调用方应回退到原 prompt。

    reference_image: {"mimeType": "image/png", "data": "<base64>"}
    """
    system = SYSTEM_PROMPTS.get(target)
    if system is None:
        return None
    user = f"用户原始描述：\n{prompt}\n\n请改写："
    media = [reference_image] if reference_image else None
    result = llm_client.call_gemini_multimodal_text(
        system, user,
        media=media,
        model="gemini-2.5-flash",  # flash 够用，pro 不值多等几秒
        timeout=120,
    )
    if result is None:
        return None
    text, _cost = result
    return text.strip() or None
