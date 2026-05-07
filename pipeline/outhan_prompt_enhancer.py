"""Outhan Prompt 增强器 — 用 Gemini 把用户输入按目标模型偏好改写。

[AI-NOTE] 2026-05-07: 移植自 Outhan/D:\\AIvideo/src/lib/prompt-enhancer.ts。
不同模型吃不同 prompt 风格——同一句"橘猫散步"在 Seedream / Seedance / Vidu 出来效果差异巨大。
默认开启；如失败（密钥/网络/审核）静默回退到原 prompt，不阻塞主链路。
"""
from __future__ import annotations

from typing import Literal, Optional

from pipeline.tools import llm_client


Target = Literal["seedream", "seedance", "vidu"]


# [AI-NOTE] 2026-05-08: 首帧"参考图 + 主题 + 用户描述"三方融合的 system prompt，
# 与单纯文本改写场景不同——这里要让 Gemini 真正"看"参考图（构图/色调/物体/光线），
# 再融合主题和用户描述提炼一段 Seedream prompt。
_FIRST_FRAME_FUSION_SYSTEM = """你是 AI 视频首帧的视觉总监 + 提示词专家。

输入会包含：
1. 主题文本（视频的故事走向）
2. 用户对首帧的补充描述（可能为空 → 完全跟随主题）
3. N 张参考图（1-5 张，承载用户期待的视觉语言）

任务：把这三方融合成一段 Doubao Seedream 模型喜欢的中文图像提示词。

要求：
- 先看参考图：提炼出共有的构图特征 / 色调氛围 / 主体特征 / 光线风格
- 主题决定故事内容（画什么），参考图决定视觉风格（怎么画）
- 用户的补充描述优先级最高（如有冲突，听用户的）
- 输出一段 80-180 个汉字的中文 prompt，覆盖：主体特征 + 动作姿态 + 环境时空 + 光线氛围 + 构图视角 + 主色调 + 风格定位
- 不要逐条列出"主体: xxx, 环境: xxx"，要写成自然流畅的画面描述
- 直接输出 prompt 文本，无前缀/引号/解释
- 不要写"参考图风格"这类元描述（描述视觉本身，不描述参考关系）"""


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


def fuse_first_frame_prompt(
    theme: str,
    user_desc: str,
    reference_images: list[dict[str, str]],
) -> Optional[str]:
    """[AI-NOTE] 2026-05-08: 首帧三方融合——看图 + 看主题 + 看用户描述 → 输出 Seedream prompt。

    reference_images: 至多 5 张 [{"mimeType": "image/jpeg", "data": "<base64>"}, ...]
    返回精准 prompt 文本；失败返回 None（调用方应 fallback）。
    """
    theme = (theme or "").strip()
    user_desc = (user_desc or "").strip()
    if not theme and not user_desc and not reference_images:
        return None

    parts = []
    if theme:
        parts.append(f"【主题】{theme}")
    if user_desc:
        parts.append(f"【用户对首帧的补充描述】{user_desc}")
    else:
        parts.append("【用户对首帧的补充描述】（用户未填，请完全跟随主题）")
    if reference_images:
        parts.append(f"【参考图】共 {len(reference_images)} 张，请结合它们的视觉语言。")
    else:
        parts.append("【参考图】无（请仅按主题想象画面）。")
    user_msg = "\n\n".join(parts) + "\n\n请输出 Seedream 首帧 prompt："

    # Pro 模型对多图融合理解更强，但响应也慢一点；这里用 Pro 因为首帧出图本就慢，多 3-5s 可接受
    result = llm_client.call_gemini_multimodal_text(
        _FIRST_FRAME_FUSION_SYSTEM,
        user_msg,
        media=reference_images or None,
        model="gemini-2.5-pro",
        timeout=180,
    )
    if result is None:
        return None
    text, _cost = result
    return text.strip() or None
