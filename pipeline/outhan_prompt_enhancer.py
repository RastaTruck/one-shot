"""Outhan Prompt 增强器 — 用 Gemini 把用户输入按目标模型偏好改写。

[AI-NOTE] 2026-05-07: 移植自 Outhan/D:\\AIvideo/src/lib/prompt-enhancer.ts。
不同模型吃不同 prompt 风格——同一句"橘猫散步"在 Seedream / Seedance / Vidu 出来效果差异巨大。
默认开启；如失败（密钥/网络/审核）静默回退到原 prompt，不阻塞主链路。
"""
from __future__ import annotations

from typing import Literal, Optional

from pipeline.tools import llm_client


Target = Literal["seedream", "seedance", "vidu"]


# [AI-NOTE] 2026-05-08 重构：参考图按角色分配（不再统一糅合）。
# 每张图带一个 role 标签：content/texture/style/palette/composition
# Gemini 按角色精确分解 → 只把该图的对应维度信号融进最终 prompt。
_FIRST_FRAME_FUSION_SYSTEM = """你是 AI 视频首帧的视觉总监 + Doubao Seedream prompt 专家。

输入会包含：
1. 主题文本（仅作"故事走向"提示，不是画面内容指令）
2. 用户对首帧的补充描述（可能为空）
3. N 张参考图（1-5 张），每张图都标注了一个明确的"角色"：
   - 📐 content（内容）：决定主体是什么、在做什么、姿势、场景背景
   - 🎨 texture（材质）：仅借表面质感（毛绒/瓷器/水晶/纸艺/金属/木质...）
   - ✨ style（风格）：决定整体画风（写实/3D/插画/水墨/油画/像素风...）
   - 🌈 palette（色调）：仅借主色调与光线方向
   - 📷 composition（构图）：仅借景别/视角/留白结构

================================
两种工作模式，按是否存在 content 图切换：
================================

【模式 A · 基底模式】当输入里至少有一张 content 角色的图时
————————————————
🚨 这是关键模式：用户已经用 content 图明确了画面，你只是按其他角色图做"局部修改"。

铁律：
1. **画面基底完全由 content 图决定**——主体、姿势、表情、动作、场景、背景元素，全都按 content 图描述。不要新增 content 图里没有的物体、角色、场景元素。
2. **主题文本不能影响画面内容**——主题只是说明这个视频"讲什么故事"，但首帧画面就是 content 图。即使主题提到"股市爆仓"而 content 图是"小熊坐桌前"，输出 prompt 也只能描述坐着的小熊，不能加股市/黑板/K线等等。
3. **其他角色图按各自范围做最小化修改**：
   - texture：仅把 content 图主体的表面材质换成 texture 图的质感（如毛绒/瓷器），主体形态/姿态/背景/构图全保持
   - style：仅转换 content 图的画风（如写实→插画），主体内容不变
   - palette：仅调色调和光线方向
   - composition：仅调景别/视角
4. 用户的补充描述优先级最高，如有冲突听用户的（但用户描述也不能凭空加 content 图里没有的东西）

错误示例（绝对不要这么做）：
❌ content 图是"两只毛绒玩偶坐桌前"，主题是"股市爆仓"，输出"小熊小兔站在黑板前看K线图"——错！主题不能加新场景元素，桌子也消失了
✅ 正确：输出"特写两只毛绒玩偶熊和兔子，坐在木桌前，背景虚化的房间..."

正确示例：
✅ content 图=小熊兔玩偶坐桌前，texture 图=羊毛毡材质 →
   输出"近景两只羊毛毡材质的小熊与小兔子，毛绒纤维细节清晰，圆润饱满的玩偶造型，坐在浅色木桌前，背景柔和虚化"

【模式 B · 创作模式】当输入里完全没有 content 角色的图时
————————————————
- 主题决定主体内容（这时候主题才是内容来源）
- 其他角色图各自贡献对应维度
- 用户补充描述优先级最高

================================
通用输出要求
================================
- 中文 80-180 字
- 自然流畅的画面描述，不要分条列"主体: xxx, 材质: xxx"
- 直接输出 prompt 文本，无前缀/引号/解释
- 不要写"参考图1""根据图2""主题里说"等元描述——描述最终画面本身"""


_ROLE_LABELS = {
    "content":     ("📐 content", "决定主体是什么、做什么；保留主体形态"),
    "texture":     ("🎨 texture", "借用表面材质质感，应用到 content 主体"),
    "style":       ("✨ style",   "决定整体画风（不引入具体物体）"),
    "palette":     ("🌈 palette", "借用主色调与光线方向（不引入物体）"),
    "composition": ("📷 composition", "借用景别/视角/留白结构"),
}


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
    *,
    reference_roles: Optional[list[str]] = None,
) -> Optional[str]:
    """[AI-NOTE] 2026-05-08: 首帧三方融合——按角色看图 + 看主题 + 看用户描述 → Seedream prompt。

    reference_images: 至多 5 张 [{"mimeType": "image/jpeg", "data": "<base64>"}, ...]
    reference_roles : 与 reference_images 一一对应；不传或对应位 None 默认 'content'
                       合法值：content / texture / style / palette / composition
    返回精准 prompt 文本；失败返回 None（调用方 fallback）。
    """
    theme = (theme or "").strip()
    user_desc = (user_desc or "").strip()
    if not theme and not user_desc and not reference_images:
        return None

    # 每张图的角色对齐
    n = len(reference_images)
    roles: list[str] = []
    if reference_roles:
        for i in range(n):
            r = reference_roles[i] if i < len(reference_roles) else None
            roles.append(r if r in _ROLE_LABELS else "content")
    else:
        roles = ["content"] * n

    has_content = "content" in roles
    parts = []

    # 主题 —— 模式 A 时降级为"故事提示"，模式 B 时是内容来源
    if theme:
        if has_content:
            parts.append(
                f"【主题（仅故事提示，不要写进画面）】{theme}"
            )
        else:
            parts.append(f"【主题】{theme}")

    # 用户描述
    if user_desc:
        parts.append(f"【用户对首帧的补充描述】{user_desc}")
    else:
        if has_content:
            parts.append("【用户对首帧的补充描述】（未填，仅按 content 图 + 其他角色图做修改）")
        else:
            parts.append("【用户对首帧的补充描述】（未填，按主题想象）")

    # 参考图清单
    if reference_images:
        ref_lines = [f"【参考图】共 {n} 张，每张角色如下："]
        for i, role in enumerate(roles, start=1):
            label, hint = _ROLE_LABELS[role]
            ref_lines.append(f"  · 图{i} 角色={label}（{hint}）")
        parts.append("\n".join(ref_lines))
    else:
        parts.append("【参考图】无（按主题想象画面）。")

    # 模式提示
    if has_content:
        parts.append(
            "🚨 【当前是模式 A · 基底模式】至少有一张 content 图，画面基底完全由 content 图决定。\n"
            "  · 主题里的内容元素绝对不能写进画面（哪怕主题写着「股市/爆仓/黑板/K 线」这类）\n"
            "  · 仅按其他角色图做对应维度的最小化修改\n"
            "  · 例如只有 texture 图时：换主体表面材质，其他全保留 content 图的样子"
        )
    else:
        parts.append("【当前是模式 B · 创作模式】没有 content 图，按主题想象画面。")

    user_msg = "\n\n".join(parts) + "\n\n请输出 Seedream 首帧 prompt："

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
