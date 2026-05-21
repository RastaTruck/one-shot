"""方向库 · 预设的选题方向（金融科普 8 + 经济分析 6）。

为什么需要方向库：
- AI 凭空生成"4 个候选角度"容易模板化，每个新闻都套同一组角度 = AI Slop
- 方向库提供有审美判断的范式集合，AI 在已有方向上扩写，避免乱发挥
- 用户从"选预设方向"开始，明确目标后 AI 才知道往哪个方向找补内容

每个方向：
- key:        程序标识
- track:      赛道（finance_kp / macro_analysis）
- label:      简称（4 字内）
- desc:       一句话方向定义
- example:    一个典型选题例子（让 AI 和用户都"看懂"这方向长什么样）
- pattern:    内容范式（AI 生成立意时遵循的结构）
"""
from __future__ import annotations


DIRECTIONS: list[dict] = [
    # ============== 金融科普 8 个 ==============
    {
        "key": "concept_unpack",
        "track": "finance_kp",
        "label": "概念拆解",
        "desc": "把陌生金融术语转成生活类比，降低理解门槛",
        "example": "什么是 LPR 利率？买房和你有什么关系",
        "pattern": "术语 → 生活类比（关键）→ 拆 3 个核心要素 → 受众能用得上",
    },
    {
        "key": "operation_pitfall",
        "track": "finance_kp",
        "label": "操作避坑",
        "desc": "实操场景告诉用户常见坑，直接服务于决策",
        "example": "新手买基金最容易踩的 5 个坑",
        "pattern": "场景设定 → 列 3-5 个具体坑 → 每个坑给避法 → 总结心法",
    },
    {
        "key": "data_counterintuitive",
        "track": "finance_kp",
        "label": "数据反直觉",
        "desc": "用真实数据打破常见认知，制造反差感",
        "example": "A 股散户真的输给机构吗？数据说的不一样",
        "pattern": "提出常见认知 → 抛真实数据 → 解释反差原因 → 给受众启发",
    },
    {
        "key": "history_analogy",
        "track": "finance_kp",
        "label": "历史类比",
        "desc": "用历史事件解读当下，借古鉴今",
        "example": "这次美联储降息和 2008 年那次有什么不同",
        "pattern": "当下事件 → 引一个历史类似事件 → 关键异同对比 → 启示",
    },
    {
        "key": "myth_busting",
        "track": "finance_kp",
        "label": "误区粉碎",
        "desc": "拆穿常见的金融误解，给真相",
        "example": "你以为定投稳赚不赔？说点没人告诉你的真相",
        "pattern": "罗列常见误区 → 逐一证伪 → 解释为什么会有此误区 → 给正解",
    },
    {
        "key": "scenario_what_if",
        "track": "finance_kp",
        "label": "场景演练",
        "desc": "what-if 推演，用具体情境给受众代入感",
        "example": "如果央行明天加息 100 个基点，你的房贷会怎样",
        "pattern": "设定一个假设场景 → 推演会发生什么 → 量化数字 → 给应对建议",
    },
    {
        "key": "cross_country",
        "track": "finance_kp",
        "label": "跨国对比",
        "desc": "同议题的国际视角，开眼界",
        "example": "为什么美国人不存钱中国人爱存钱？背后的逻辑",
        "pattern": "议题 → 中外行为/数据对比 → 解释文化/制度根源 → 启示",
    },
    {
        "key": "cold_fact",
        "track": "finance_kp",
        "label": "冷知识",
        "desc": "鲜少人知的金融事实，激发好奇",
        "example": "你每月工资里有多少是被通胀吃掉的",
        "pattern": "抛一个反常识冷知识 → 解释机制 → 量化展示 → 引发反思",
    },

    # ============== 经济分析 6 个 ==============
    {
        "key": "policy_interpret",
        "track": "macro_analysis",
        "label": "政策解读",
        "desc": "拆政策对普通人/行业的真实影响",
        "example": "新出的房贷利率政策到底意味着什么",
        "pattern": "政策内容 → 真实影响（普通人/行业）→ 背后政策意图 → 长期推演",
    },
    {
        "key": "situation_forecast",
        "track": "macro_analysis",
        "label": "局势推演",
        "desc": "给推演框架，不预测结果但提供思考工具",
        "example": "如果中美贸易摩擦升级，谁会先撑不住",
        "pattern": "议题 → 列出 2-3 个关键变量 → 不同变量组合下的推演 → 观察指标",
    },
    {
        "key": "macro_cross_country",
        "track": "macro_analysis",
        "label": "跨国对比",
        "desc": "大国经济议题比较，看清结构性差异",
        "example": "日韩台都经历过的房价崩盘，中国会不会重演",
        "pattern": "议题 → 多国历史走势对比 → 结构性差异分析 → 中国独特之处",
    },
    {
        "key": "long_cycle",
        "track": "macro_analysis",
        "label": "长周期",
        "desc": "几十年长尺度看问题，避免被短期噪音迷惑",
        "example": "过去 30 年中国财富分配的真实曲线",
        "pattern": "拉长时间轴 → 趋势性变化 → 几个关键拐点 → 当下的位置",
    },
    {
        "key": "structural_change",
        "track": "macro_analysis",
        "label": "结构性变化",
        "desc": "识别系统性转折点，这次和过去不一样",
        "example": "为什么这一波周期和过去都不一样",
        "pattern": "提出当下不同之处 → 拆 3 个结构性因素 → 不可逆 vs 周期性 → 应对",
    },
    {
        "key": "market_game",
        "track": "macro_analysis",
        "label": "市场博弈",
        "desc": "分析各方利益博弈，看懂背后的角色",
        "example": "这次降息背后，美联储和华尔街的博弈",
        "pattern": "议题 → 列出 3+ 利益方 → 各方诉求/筹码 → 当前博弈格局",
    },
]


def by_key(key: str) -> dict | None:
    for d in DIRECTIONS:
        if d["key"] == key:
            return d
    return None


def by_track(track: str) -> list[dict]:
    return [d for d in DIRECTIONS if d["track"] == track]


def all_keys() -> list[str]:
    return [d["key"] for d in DIRECTIONS]


def brief_for_prompt(d: dict) -> str:
    """给 LLM 用的紧凑方向描述。"""
    return f"【{d['label']}】({d['key']}) {d['desc']}。范式：{d['pattern']}。例：{d['example']}"
