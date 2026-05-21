"""赛道画像 — 给 LLM 角度生成提供上下文。

每个画像描述：受众 / 调性 / 偏好角度 / 雷区。LLM 按画像匹配信号生成角度，
而不是对所有信号套同一套万能角度（避免 AI Slop）。
"""
from __future__ import annotations


TRACK_PROFILES: dict[str, dict] = {
    "finance_kp": {
        "label": "金融科普",
        "audience": "25-40 岁城市白领，对金融有基础但不专业",
        "tone": "通俗易懂、有梗、不卖课、不装专家",
        "preferred_angles": ["概念拆解", "历史类比", "操作避坑", "数据反直觉", "误区粉碎"],
        "avoid": ["内幕预测", "荐股", "监管敏感词", "民族情绪", "末日论"],
    },
    "macro_analysis": {
        "label": "经济分析",
        "audience": "30-50 岁有投资/经营经验，关心宏观",
        "tone": "有立场、有数据、克制、不亢奋",
        "preferred_angles": ["政策解读", "局势推演", "跨国对比", "长周期视角", "结构性变化"],
        "avoid": ["民族情绪", "阴谋论", "短期预测", "情绪化结论"],
    },
}


def get_profile_brief(track: str) -> str:
    """给 LLM prompt 用的紧凑赛道描述。"""
    p = TRACK_PROFILES.get(track)
    if not p:
        return ""
    return (
        f"赛道：{p['label']}\n"
        f"- 受众：{p['audience']}\n"
        f"- 调性：{p['tone']}\n"
        f"- 偏好角度：{', '.join(p['preferred_angles'])}\n"
        f"- 避免：{', '.join(p['avoid'])}"
    )


def all_profiles_brief() -> str:
    return "\n\n".join(get_profile_brief(t) for t in TRACK_PROFILES)
