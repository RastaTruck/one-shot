"""文案 Agent：一次生成 3 套完整文案 + 单字段 refine。

- 有 LLM 密钥：走真 API + 任务日志
- 无密钥：返回一套基于输入主题的 stub，让前端 UI 仍可演示
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pipeline import state
from pipeline.tools import llm_client

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "config" / "prompts"

# ============================================================
# 意图 → 规则 / 叙事路径 / 标签提示
# ============================================================
_INTENT_RULES: dict[str, str] = {
    "搞懂概念": """\
核心任务：解释「这个东西是什么」，让零基础的人听完能用一句话向别人解释。
✓ 正确方向：定义本身、本质原理、用类比/故事/数字让人瞬间理解「是什么」
✗ 禁止越界：不写「XX对YY的影响」（那是探讨影响的任务）
✗ 禁止越界：不给操作建议或步骤（那是学会操作的任务）""",

    "探讨影响": """\
核心任务：拆解「这件事会怎么影响你/社会/市场」，有主体、有时间维度、有量化感。
✓ 正确方向：具体生活场景的变化、连锁反应链条、数据量化影响规模
✗ 禁止越界：不做纯概念解释（假设观众已知这是什么）
✗ 禁止越界：不给操作建议，只分析/呈现影响""",

    "学会操作": """\
核心任务：给出「可落地执行的步骤或决策框架」，让观众看完知道该怎么做。
✓ 正确方向：判断信号（什么时候用）+ 行动步骤 + 避坑提醒
✗ 禁止越界：不做纯知识讲解，每套必须有可执行的「行动锚点」
✗ 禁止越界：不写影响分析，聚焦「怎么做」""",

    "引发共鸣": """\
核心任务：让观众说「这说的就是我」，情绪先于知识，故事先于道理。
✓ 正确方向：具体人物处境、情感认同、让人想评论「我也是」的结尾
✗ 禁止越界：不做知识科普，不列步骤，核心是情感共振
✗ 禁止越界：钩子必须是情感/处境切入，不是信息量堆积""",
}

_DRAFT_ROUTES: dict[str, str] = {
    "搞懂概念": """\
d1（类比拆解）：用1-2个精准的生活类比，把概念「可视化」——让人听完能说「哦原来就像XX一样」
d2（场景还原）：设计一个「主角第一次遇到这件事」的具体场景，随情节推进自然讲清概念
d3（数字解码）：用一个震撼数字切入展示量级或比例，再往回倒推解释概念本身的运作原理""",

    "探讨影响": """\
d1（身边变化）：聚焦离观众最近的影响——房贷/存款/消费/工作，用真实数字量化「你今天就能感受到」
d2（连锁反应）：从一个直接影响出发，展开多米诺骨牌式的间接影响链条，层层递进
d3（数据画像）：用3个核心数据，从宏观到微观呈现影响的真实规模与结构，让人感受到量级""",

    "学会操作": """\
d1（信号判断）：教观众识别「什么时候该行动/注意/触发」，核心是判断条件而非步骤，让人学会「看时机」
d2（步骤拆解）：最简操作流程，用「第一步/第二步/第三步」结构，每步聚焦一个关键动作
d3（避坑清单）：从3个最高频错误切入，每个错误配一个正确做法，帮观众绕过来""",

    "引发共鸣": """\
d1（镜中人）：选一个典型处境（工资/存款/贷款/投资失误），让观众在主角身上看见自己，情绪积累到最后揭示洞察
d2（转折顿悟）：先呈现观众的「错误认知/无奈处境」，再用一个认知转折带来「原来如此」的释然
d3（说出心声）：直接替观众说出他们想说但说不出口的话，用第一人称制造强烈认同感""",
}

_INTENT_LABEL_HINTS: dict[str, str] = {
    "搞懂概念": "类比拆解 / 场景还原 / 数字解码 / 图解式 / 极简版",
    "探讨影响": "身边变化 / 连锁反应 / 数据画像 / 影响全景 / 微观感知",
    "学会操作": "信号判断 / 步骤拆解 / 避坑清单 / 实操版 / 决策框架",
    "引发共鸣": "镜中人 / 转折顿悟 / 说出心声 / 情感共振 / 观众自白",
}

# ============================================================
# 受众 → 正向写作模式（script_gen 用）
# ============================================================
_AUDIENCE_MODES: dict[str, str] = {
    "完全小白": """\
★ 单句限制：每句话不超过15字（严格执行，超长句必须拆成两句）
★ 先类比后概念：永远不要先说术语再解释。顺序必须是：先说一个观众日常生活里已知的事物（租房/网购/发工资/借钱给朋友/水电费），再给出概念名词
★ 场景锚定：每段至少一个生活场景（越具体越好：「你每个月发完工资」「你去买咖啡」「你租的那套房」）
★ 禁止括号解释：括号让小白觉得「这里很难」，改用口语句式翻译
★ 禁止成语/四字词/书面搭配

示范对比（以「加息」为例）：
  坏文案：「加息是央行通过提高基准利率来调控货币供应量，抑制通货膨胀压力的工具。」
  好文案：「你租房子，房东说'下个月房租涨500'。加息就是这个——央行告诉所有银行：借钱，今天起要贵了。」""",

    "有点基础": """\
★ 单句限制：每句不超过25字
★ 术语规则：每段最多用1个专业术语，且必须在同一句内用口语翻译（格式：术语+破折号/括号+口语解释）
★ 数字锚定：每段至少1个具体数字（百分比/金额/时间/倍数），不说「很多」「大幅」「显著」
★ 类比可以更金融化：股票/基金/存款/贷款场景都可用，但每次类比后必须给出实际数字

示范（以「加息影响房贷」为例）：
  「利率上调0.5个点——听起来小吧？100万房贷30年算下来，你要多还19万。这就是为什么利率变动1%，楼市就会抖一下。」""",

    "进阶用户": """\
★ 可以直接使用专业术语，不需要解释
★ 信息密度要高：每段至少一个具体数字/机构/年份/可验证事实
★ 可以挑战常见误区：「大多数人认为X，但实际上Y」结构
★ 句子可以更复杂，但禁止书面语连接词（综上/由此/如前所述）
★ 可以预设观众已有基本认知，直接切入机制和反常识层面

示范（以「加息」为例）：
  「市场普遍认为加息是利空，但加息初期银行股往往反而会涨——息差扩大，净利润改善。这个反直觉的点，很多散户都在这踩坑。」""",
}

# ============================================================
# 意图 × 示例好文案（few-shot，script_gen 用）
# ============================================================
_SCRIPT_EXAMPLES: dict[str, str] = {
    "搞懂概念": """\
以下是一套「搞懂概念」好文案的示范（以「加息是什么」为例，类比降维路线）：

hook：「你房贷利息涨了，背后就三个字——加息了。」

S1（共鸣）：「你有没有发现，最近贷款利息好像贵了？不是你的错觉。这背后，只有一件事——」
S2（类比）：「把央行想象成全国最大的钱庄老板。它一开口说'我要调高借钱的价格'，所有银行都得跟着涨。你的房贷、你的消费贷，全都贵了。说白了——」
S3（定义）：「加息就是央行在向全国发通知：从今天起，借钱要多付一点。记住这句话，你就搞懂了。」

golden：「加息，是央行给借钱的人发的涨价通知。」
cta：「你觉得加息对你影响最大的是哪块？评论里聊聊。」

注意：S1结尾有「只有一件事——」桥接S2；S2结尾有「说白了——」桥接S3。这是流畅感的来源。""",

    "探讨影响": """\
以下是一套「探讨影响」好文案的示范（以「加息对房贷的影响」为例，数据画像路线）：

hook：「加息0.5个点，听起来很小，但你的房贷可能多还19万。」

S1（数据）：「100万房贷，30年，利率从4.3%涨到4.8%。每月月供多275块，看起来还好？但问题来了——」
S2（连锁）：「30年下来，这275块变成了99000块，快10万。而且这还不是全部——加息还会带来房价预期下调、提前还款潮、银行收紧放贷额度。落到你身上就是——」
S3（落点）：「买房更贵，持有更重，出手更难。现在最该想的是：你的月供占收入多少比例，还扛得住吗？」

golden：「0.5个点，不是数字，是19万。」
cta：「你现在月供占收入多少？这个比例安全吗？」

注意：S1→S2用「但问题来了——」；S2→S3用「落到你身上就是——」。""",

    "学会操作": """\
以下是一套「学会操作」好文案的示范（以「加息周期如何配置资产」为例，步骤拆解路线）：

hook：「加息了，三个动作，现在就能做。」

S1（信号）：「怎么知道加息来了？三个信号：房贷利率在涨、银行理财收益率在涨、股市开始震荡下行。三个信号都有了，就是时候了。但是——」
S2（步骤）：「第一步：能提前还贷的就还，存款利息永远低于贷款利息。第二步：把活期、余额宝转进短债或货币基金，加息周期里它们会跟着涨。第三步：股票先按兵不动，等利率见顶再说。说白了——」
S3（避坑）：「最多人犯的错：加息马上卖股买债。但债券在加息周期里也会跌，要等利率见顶才买。先动第一和第二步，第三步等信号。」

golden：「加息时，还钱比赚钱更确定。」
cta：「你现在的资产配置，准备好迎接加息了吗？」""",

    "引发共鸣": """\
以下是一套「引发共鸣」好文案的示范（以「贷款买房遇到加息」为例，镜中人路线）：

hook：「她贷款200万买房，没想到一年后，每月要多还2800块。」

S1（人物）：「王姐，35岁，去年咬牙贷了200万买了套二手房。当时利率4.3%，月供11000。她说：'刚好在承受范围内。'但是——」
S2（转折）：「今年加息，利率变4.8%，月供变13800。多了2800块。一个月，少了一次带女儿出去玩，少了两顿和朋友的聚餐，少了三件给自己买的衣服。她说：'我以为0.5个点，没多少。'落到你身上就是——」
S3（共鸣）：「数字很小，但生活里，它是真实的2800块。你有没有这种感觉——利率涨了一点点，日子却明显不一样了？」

golden：「利率涨的是百分点，影响的是生活本身。」
cta：「你有没有因为加息，改变过某个计划？说说看。」""",
}

# ============================================================
# 受众 → 完整文稿写作模式（script_full 用）
# ============================================================
_AUDIENCE_FULL_MODES: dict[str, str] = {
    "完全小白": """\
【完全小白写作模式】优先级：流畅易懂 > 信息密度

写作标准（严格执行）：
- 单句≤15字。超过15字的必须拆成两句
- 先类比后概念：每次解释一个概念，必须先给出生活类比，再给概念名称
- 每段必须有一个生活场景（租房/发工资/买东西/借钱/手机费）
- 禁止术语直出：任何专业词必须在同句内用最简单的话翻译
- 禁止括号解释：改用口语翻译句「就是说」「简单来说」「换句话就是」
- 语气：像朋友聊天，不像老师讲课

参照风格：生活化口语频道（非金融专业），核心是「让外婆听懂」的标准""",

    "有点基础": """\
【有点基础写作模式】优先级：清晰准确 > 极度简化

写作标准：
- 单句≤25字
- 每段最多1个专业术语，且必须在同句或下句内翻译
- 每段至少1个具体数字，不说「很多」「大幅」「显著」
- 类比可以更金融化（股票/基金/房贷/存款），但每个类比后必须有数字量化
- 语气：理解观众有一些基础，可以直接进入机制，但不做学术推导

参照风格：回形针、老雅皮、花朵财经等中等密度金融科普""",

    "进阶用户": """\
【进阶用户写作模式】优先级：信息密度 > 基础铺垫

写作标准：
- 单句无严格限制，但禁止书面语
- 可直接使用术语，假设观众已知
- 每段至少2条硬信息（具体数字/机构/年份/可验证事实）
- 可以挑战常见误区，制造认知反差
- 可以有更复杂的因果链，但每个环节要有逻辑衔接词

参照风格：半佛仙人、所长林超、梁中华等高密度金融分析""",
}

# ============================================================
# 意图 → 完整文稿叙事结构（script_full 用）
# ============================================================
_INTENT_FULL_MODES: dict[str, str] = {
    "搞懂概念": """\
【搞懂概念叙事结构】
目标：观众听完能用一句话向别人解释这个概念
结构模板：
  钩子：制造「咦，这个我以为我懂但其实不懂」的感觉
  前提：建立「你以为是X」的认知基线（观众现有的错误/模糊认知）
  类比：用一个日常场景类比，让概念「可视化」
  机制：用最简单的语言解释概念的核心运作原理
  强化：用第二个角度（反例/数字/场景）再次确认理解
  金句：给出一句可复述的最简定义
  CTA：鼓励观众向别人解释这个概念

禁止：全程出现「影响是...」「你应该...」——这个 intent 只讲「是什么」""",

    "探讨影响": """\
【探讨影响叙事结构】
目标：观众听完能感受到这件事对自己生活的真实影响
结构模板：
  钩子：一个具体数字或可感知的变化，立刻建立「与我有关」的连接
  直接影响：离观众最近的影响，必须量化到个人（房贷/存款/消费/工资）
  连锁影响：第一层影响如何触发第二层、第三层
  时间维度：短期可感知 vs 长期累积效应
  个人落点：落到观众的具体决策上——「这对你意味着什么」
  CTA：引发观众分享自己受到的影响

禁止：只讲宏观数字不落到个人，或者解释「是什么」而不讲「影响是什么」""",

    "学会操作": """\
【学会操作叙事结构】
目标：观众听完知道在什么情况下做什么，避开什么坑
结构模板：
  钩子：制造「我现在就需要这个」的紧迫感
  时机判断：「什么情况下你需要这个操作」（触发条件）
  核心步骤：用「第一步/第二步/第三步」结构，每步≤20字，只有一个动作
  原因解释：最简单地解释每步「为什么这么做」（不做深度展开）
  避坑提醒：最高频的1-2个错误 + 正确做法
  CTA：鼓励观众收藏或立即行动

禁止：只讲知识不给步骤，或步骤超过5步（超过5步 = 没有提炼）""",

    "引发共鸣": """\
【引发共鸣叙事结构】
目标：观众说「这说的就是我」，想评论/转发
结构模板：
  钩子：一个具体的人物处境（名字/年龄/数字/场景），第一句就让人认出自己
  情绪积累：跟着主角经历，把情绪带到峰值（委屈/无奈/愤怒/感动）
  转折/揭示：一个让情绪「落地」的认知转折或洞察
  共鸣升华：从主角处境提炼出观众的普遍感受，让人说「我也是」
  CTA：开放性问题，让观众说出自己的故事

禁止：情绪没铺垫就讲道理，或讲道理多于情绪（比例应该是 情绪70% + 洞察30%）""",
}


def _read_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def _fill(template: str, **kw: Any) -> str:
    """安全替换：仅把 {key} 换成值，不影响模板里的 JSON 花括号。"""
    out = template
    for k, v in kw.items():
        out = out.replace("{" + k + "}", str(v))
    return out


def _ensure_video_row(video_id: str, title: str) -> None:
    """API 可能传进来 mock video_id（没进过 DB），补一条占位 row 让 tasks 外键通过。"""
    if state.get_video(video_id) is None:
        state.create_video(video_id, title=title, topic=title)


def _stub_drafts(topic: str, style_name: str) -> list[dict[str, Any]]:
    """无密钥时的演示数据 —— 3 套差异化 stub。"""
    return [
        {
            "draft_id": "d1", "label": "反常识型", "tone": "犀利",
            "hook": f"关于「{topic}」，99% 的人理解反了。",
            "body": [
                {"step": "S1", "role": "共鸣", "text": f"你以为「{topic}」只是一个小众话题？打开手机，每天都有人在为它付真金白银。"},
                {"step": "S2", "role": "反转", "text": "但真相是，大多数人看到的只是表面——底层逻辑和你想的完全相反，这一点专业人士从不愿意说破。"},
                {"step": "S3", "role": "论证", "text": "回到一个最朴素的问题：钱是怎么流动的？一旦想明白这个，你就会发现自己长期站在了天平的错误一侧。"},
            ],
            "golden": "看懂规则的人，才不是赌徒。", "cta": "点关注，下期扒更硬的内幕。",
            "est_chars": 140, "est_duration_sec": 35,
        },
        {
            "draft_id": "d2", "label": "故事化", "tone": "叙事",
            "hook": f"2019 年，一个 27 岁的年轻人因为「{topic}」翻身。",
            "body": [
                {"step": "S1", "role": "案例", "text": "他不是富二代，也没学过金融，只是在一个深夜刷到了一张不起眼的图，把自己存了 5 年的 8 万块全押了进去。"},
                {"step": "S2", "role": "原理", "text": f"他赌的，就是今天要讲的「{topic}」——它的规则并不复杂，但敢落地的人不到万分之一，因为它和直觉完全对冲。"},
                {"step": "S3", "role": "反转", "text": "两年后他翻了 6 倍，但真正改变他的不是钱，是他终于理解了：市场奖励的从来不是努力，是结构性的认知差。"},
            ],
            "golden": "大多数人输的，是自己的常识。", "cta": "想听完整故事？下集继续。",
            "est_chars": 170, "est_duration_sec": 42,
        },
        {
            "draft_id": "d3", "label": "数据驱动", "tone": "理性",
            "hook": f"「{topic}」相关的钱，一年规模 1.2 万亿。",
            "body": [
                {"step": "S1", "role": "数据", "text": "这个数字什么概念？相当于中国三个一线城市全年社会零售总额加起来。但 80% 的普通人，对它的认知停留在一句话。"},
                {"step": "S2", "role": "原理", "text": f"「{topic}」的本质是一套定价机制——三个变量、两条曲线就能拆开它。今天用最短的 30 秒讲清楚。"},
                {"step": "S3", "role": "风险", "text": "注意：看懂机制不等于能赚到钱。真正决定盈亏的，是你在什么位置进场、以什么纪律退场，这才是大多数人栽的地方。"},
            ],
            "golden": "数字不会说谎，但会被误读。", "cta": "收藏，回头慢慢看。",
            "est_chars": 170, "est_duration_sec": 42,
        },
    ]


def generate_three_drafts(
    video_id: str,
    topic: str,
    style: dict[str, Any],
    topic_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """返回 {"drafts": [...], "provider": str, "cost": float}
    topic_meta 可传入选题时保存的完整 topic 对象，用于读取 intent/perspective/audience。
    """
    _ensure_video_row(video_id, topic)
    meta = topic_meta or {}
    intent = meta.get("intent") or "搞懂概念"
    audience = meta.get("audience") or "完全小白"
    perspective = meta.get("perspective") or "类比降维"

    system = _fill(
        _read_prompt("script_gen.txt"),
        style_name=style.get("name", "默认"),
        style_desc=style.get("desc", ""),
        style_sample=style.get("sample", ""),
        intent=intent,
        audience=audience,
        perspective=perspective,
        intent_rules=_INTENT_RULES.get(intent, _INTENT_RULES["搞懂概念"]),
        draft_routes=_DRAFT_ROUTES.get(intent, _DRAFT_ROUTES["搞懂概念"]),
        intent_label_hints=_INTENT_LABEL_HINTS.get(intent, ""),
        audience_mode=_AUDIENCE_MODES.get(audience, _AUDIENCE_MODES["有点基础"]),
        script_example=_SCRIPT_EXAMPLES.get(intent, _SCRIPT_EXAMPLES["搞懂概念"]),
    )
    user = f"选题：{topic}"
    task_id = state.start_task(video_id, agent="script_writer",
                               input_data={"topic": topic, "intent": intent, "audience": audience})

    result = llm_client.call_json_with_fallback(system, user)
    if result is None:
        drafts = _stub_drafts(topic, style.get("name", "默认"))
        state.finish_task(task_id, success=True, output={"mode": "stub", "n": len(drafts)}, cost_cny=0.0)
        return {"drafts": drafts, "provider": "stub", "cost": 0.0}

    provider, content, cost = result
    try:
        parsed = json.loads(content)
        drafts = parsed.get("drafts") or []
        assert len(drafts) == 3
    except (json.JSONDecodeError, AssertionError, TypeError) as e:
        state.finish_task(task_id, success=False, error=f"parse error: {e}", cost_cny=cost)
        drafts = _stub_drafts(topic, style.get("name", "默认"))
        return {"drafts": drafts, "provider": "stub", "cost": 0.0}

    state.finish_task(task_id, success=True, output={"n": 3}, cost_cny=cost)
    # 写回 provider 字段（start_task 创建时还不知道）
    import sqlite3  # noqa
    with state.get_conn() as conn:
        conn.execute("UPDATE tasks SET provider = ? WHERE task_id = ?", (provider, task_id))
    return {"drafts": drafts, "provider": provider, "cost": cost}


def refine_field(
    video_id: str, field_label: str, current_text: str, instruction: str,
    style: dict[str, Any], context: dict[str, Any],
) -> dict[str, Any]:
    """单字段 AI 改写。返回 {"text": str, "provider": str, "cost": float}"""
    _ensure_video_row(video_id, context.get("topic", "(untitled)"))
    system = _fill(
        _read_prompt("script_refine.txt"),
        field_label=field_label,
        current_text=current_text,
        instruction=instruction or "换一个更有张力的版本",
        style_name=style.get("name", "默认"),
        ctx_hook=context.get("hook", ""),
        ctx_body=" / ".join(context.get("body", [])),
        ctx_golden=context.get("golden", ""),
        ctx_cta=context.get("cta", ""),
    )
    task_id = state.start_task(video_id, agent="script_refine", input_data={"field": field_label})

    result = llm_client.call_json_with_fallback(system, "请按上述要求改写。")
    if result is None:
        # 无密钥：做个简单变体
        variants = [
            f"{current_text}（换个说法）",
            f"再说一遍：{current_text}",
            current_text.rstrip("。.!？?") + "——这次说透。",
        ]
        import hashlib
        pick = variants[int(hashlib.md5(current_text.encode()).hexdigest(), 16) % len(variants)]
        state.finish_task(task_id, success=True, output={"mode": "stub"}, cost_cny=0.0)
        return {"text": pick, "provider": "stub", "cost": 0.0}

    provider, content, cost = result
    try:
        parsed = json.loads(content)
        text = parsed.get("text", "").strip()
        assert text
    except (json.JSONDecodeError, AssertionError, TypeError) as e:
        state.finish_task(task_id, success=False, error=f"parse error: {e}", cost_cny=cost)
        return {"text": current_text, "provider": provider, "cost": cost}

    state.finish_task(task_id, success=True, output={"len": len(text)}, cost_cny=cost)
    with state.get_conn() as conn:
        conn.execute("UPDATE tasks SET provider = ? WHERE task_id = ?", (provider, task_id))
    return {"text": text, "provider": provider, "cost": cost}


def generate_full_script(
    video_id: str, topic: str, style: dict[str, Any],
    outline: dict[str, Any], duration_sec: int,
    pace_chars_per_sec: float = 5.5,
    topic_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """根据已确认的大纲扩写成完整文稿。
    默认 5.5 字/秒（科普正常语速）；小林/回形针式密度可调 6；央视式严肃可调 4.5。
    topic_meta 传入选题对象，用于读取 intent/audience/perspective 定制叙事结构。
    返回 {full, provider, cost}
    """
    _ensure_video_row(video_id, topic)
    meta = topic_meta or {}
    intent = meta.get("intent") or "搞懂概念"
    audience = meta.get("audience") or "完全小白"
    perspective = meta.get("perspective") or "类比降维"

    body = outline.get("body") or [{}, {}, {}]
    target_chars = int(duration_sec * pace_chars_per_sec)
    # ±15% 容忍区间
    min_chars = int(target_chars * 0.85)
    max_chars = int(target_chars * 1.15)
    system = _fill(
        _read_prompt("script_full.txt"),
        style_name=style.get("name", "默认"),
        style_desc=style.get("desc", ""),
        style_sample=style.get("sample", ""),
        intent=intent,
        audience=audience,
        perspective=perspective,
        audience_writing_mode=_AUDIENCE_FULL_MODES.get(audience, _AUDIENCE_FULL_MODES["有点基础"]),
        intent_writing_mode=_INTENT_FULL_MODES.get(intent, _INTENT_FULL_MODES["搞懂概念"]),
        topic=topic,
        outline_hook=outline.get("hook", ""),
        s1_role=body[0].get("role", "S1"), s1_text=body[0].get("text", ""),
        s2_role=body[1].get("role", "S2"), s2_text=body[1].get("text", ""),
        s3_role=body[2].get("role", "S3"), s3_text=body[2].get("text", ""),
        outline_golden=outline.get("golden", ""),
        outline_cta=outline.get("cta", ""),
        duration_sec=duration_sec,
        pace=pace_chars_per_sec,
        min_chars=min_chars,
        max_chars=max_chars,
    )
    user = "请按上述大纲生成完整口播文稿。"
    task_id = state.start_task(
        video_id, agent="script_full",
        input_data={"outline_id": outline.get("draft_id"), "duration_sec": duration_sec},
    )
    # 超时随时长线性放大：1min≈120s，3min≈300s
    gen_timeout = max(120, 60 + duration_sec * 2)
    result = llm_client.call_json_with_fallback(system, user, timeout=gen_timeout)
    if result is None:
        # stub
        full = {
            "title": topic,
            "duration_sec": duration_sec,
            "pace_chars_per_sec": pace_chars_per_sec,
            "total_chars": target_chars,
            "framework": {
                "one_liner": f"[stub] {topic}",
                "logical_arc": ["认知前提", "暴露反例", "引入机制", "落到行动"],
                "info_points": ["[stub] 具体数字", "[stub] 真实人物"],
            },
            "paragraphs": [
                {"id": f"p{i+1}", "role": ["钩子","共鸣","原理","金句","CTA"][i % 5],
                 "purpose": "stub 占位",
                 "text": f"[stub 段 {i+1}] " + (body[i % 3].get("text", "") if i < 3 else outline.get("golden", "")),
                 "duration_sec": 8.0,
                 "info_density": "低"}
                for i in range(max(4, duration_sec // 15))
            ],
            "summary": "无 API key 时的 stub 演示版本",
        }
        state.finish_task(task_id, success=True, output={"mode": "stub"}, cost_cny=0.0)
        return {"full": full, "provider": "stub", "cost": 0.0}

    provider, content, cost = result
    try:
        parsed = json.loads(content)
        assert "paragraphs" in parsed and isinstance(parsed["paragraphs"], list)
        for i, p in enumerate(parsed["paragraphs"]):
            p.setdefault("id", f"p{i+1}")
            p.setdefault("role", "段落")
            p.setdefault("purpose", "")
            p.setdefault("text", "")
            p.setdefault("info_density", "中")
            # 永远重算 duration（不相信 AI 报的）
            p["duration_sec"] = round(len(p["text"]) / pace_chars_per_sec, 1)
        parsed.setdefault("title", topic)
        parsed.setdefault("duration_sec", duration_sec)
        parsed.setdefault("pace_chars_per_sec", pace_chars_per_sec)
        # 永远重算总字数（AI 经常虚报）
        actual_chars = sum(len(p["text"]) for p in parsed["paragraphs"])
        parsed["total_chars"] = actual_chars
        parsed.setdefault("framework", {"one_liner": "", "logical_arc": [], "info_points": []})
        parsed.setdefault("summary", "")
    except (json.JSONDecodeError, AssertionError, KeyError, TypeError) as e:
        state.finish_task(task_id, success=False, error=f"parse: {e}", cost_cny=cost)
        return {"full": None, "provider": provider, "cost": cost, "error": str(e)}

    # 字数不达标 → 自动补写一次（给 AI 看实际短了多少，让它扩）
    shortfall = min_chars - actual_chars
    retried = False
    if shortfall > 20:
        retried = True
        expand_user = (
            f"你上一版总字数只有 {actual_chars} 字，但目标是 {min_chars}-{max_chars} 字（时长 {duration_sec}s）。"
            f"缺 {shortfall} 字。请保留结构/段数/顺序不变，往每段里**补具体细节**（数字、案例细节、机构名、场景动作），"
            f"直到总字数落进目标区间。返回完整的同样 JSON 结构，不要只返回新增部分。当前文稿 JSON：\n"
            + json.dumps(parsed, ensure_ascii=False)
        )
        r2 = llm_client.call_json_with_fallback(system, expand_user, timeout=gen_timeout)
        if r2 is not None:
            p2, c2, cost2 = r2
            cost += cost2
            try:
                parsed2 = json.loads(c2)
                assert "paragraphs" in parsed2 and isinstance(parsed2["paragraphs"], list)
                for i, p in enumerate(parsed2["paragraphs"]):
                    p.setdefault("id", f"p{i+1}")
                    p.setdefault("role", parsed["paragraphs"][i]["role"] if i < len(parsed["paragraphs"]) else "段落")
                    p.setdefault("purpose", "")
                    p.setdefault("text", "")
                    p.setdefault("info_density", "中")
                    p["duration_sec"] = round(len(p["text"]) / pace_chars_per_sec, 1)
                parsed2.setdefault("title", parsed.get("title", topic))
                parsed2["duration_sec"] = duration_sec
                parsed2["pace_chars_per_sec"] = pace_chars_per_sec
                new_chars = sum(len(p["text"]) for p in parsed2["paragraphs"])
                parsed2["total_chars"] = new_chars
                parsed2.setdefault("framework", parsed.get("framework", {}))
                parsed2.setdefault("summary", parsed.get("summary", ""))
                # 采纳条件：变长 且 没超上限（超上限宁可保持原版，让用户自己删）
                if new_chars > actual_chars and new_chars <= max_chars:
                    parsed = parsed2
                    actual_chars = new_chars
                elif new_chars > max_chars:
                    # 扩过头了 — 截断到 max_chars 附近：从尾部往前累加字数，保留前 N 段
                    trimmed = []
                    cum = 0
                    for p in parsed2["paragraphs"]:
                        if cum + len(p["text"]) <= max_chars:
                            trimmed.append(p); cum += len(p["text"])
                        else:
                            break
                    # 必须保留 CTA — 如果最后一段是 CTA role，从被删的段里找回
                    original_last = parsed2["paragraphs"][-1]
                    if trimmed and trimmed[-1].get("role") != "CTA" and original_last.get("role") == "CTA":
                        trimmed.append(original_last); cum += len(original_last["text"])
                    if trimmed and min_chars <= cum <= max_chars:
                        parsed2["paragraphs"] = trimmed
                        parsed2["total_chars"] = cum
                        parsed = parsed2
                        actual_chars = cum
            except Exception:
                pass  # 补写失败就用原版

    state.finish_task(task_id, success=True,
                      output={"chars": actual_chars, "target_min": min_chars, "target_max": max_chars,
                              "retried_expand": retried},
                      cost_cny=cost)
    with state.get_conn() as conn:
        conn.execute("UPDATE tasks SET provider = ? WHERE task_id = ?", (provider, task_id))
    # 把字数达标警告带回给前端
    if actual_chars < min_chars:
        parsed["char_warning"] = f"实际 {actual_chars} 字，低于目标 {min_chars} 字下限（已尝试补写）"
    return {"full": parsed, "provider": provider, "cost": cost}


def refine_paragraph(
    video_id: str, full: dict[str, Any], paragraph_idx: int, instruction: str,
    style: dict[str, Any],
) -> dict[str, Any]:
    """单段重写。返回 {text, duration_sec, provider, cost}"""
    _ensure_video_row(video_id, full.get("title", "(untitled)"))
    paras = full.get("paragraphs") or []
    if paragraph_idx < 0 or paragraph_idx >= len(paras):
        return {"error": "paragraph_idx out of range"}
    p = paras[paragraph_idx]
    cur = p.get("text", "")
    cur_len = len(cur)
    min_c, max_c = max(20, int(cur_len * 0.7)), int(cur_len * 1.4) + 20
    full_context = "\n".join(
        f"[{i+1}/{p2.get('role','')}] {p2.get('text','')[:60]}{'...' if len(p2.get('text',''))>60 else ''}"
        for i, p2 in enumerate(paras)
    )
    system = _fill(
        _read_prompt("paragraph_refine.txt"),
        full_context=full_context,
        idx=paragraph_idx + 1,
        role=p.get("role", ""),
        current_text=cur,
        instruction=instruction or "换一个更有张力的版本",
        style_name=style.get("name", "默认"),
        min_chars=min_c, max_chars=max_c,
    )
    task_id = state.start_task(video_id, agent="paragraph_refine", input_data={"idx": paragraph_idx})
    result = llm_client.call_json_with_fallback(system, "请按上述要求重写这一段。")
    if result is None:
        state.finish_task(task_id, success=True, output={"mode": "stub"}, cost_cny=0.0)
        return {"text": cur + "（stub 改写）", "duration_sec": p.get("duration_sec", 8), "provider": "stub", "cost": 0.0}
    provider, content, cost = result
    try:
        parsed = json.loads(content)
        text = parsed.get("text", "").strip()
        assert text
        dur = parsed.get("duration_sec") or round(len(text) / 4.0, 1)
    except Exception as e:
        state.finish_task(task_id, success=False, error=f"parse: {e}", cost_cny=cost)
        return {"text": cur, "duration_sec": p.get("duration_sec", 8), "provider": provider, "cost": cost, "error": str(e)}
    state.finish_task(task_id, success=True, output={"len": len(text)}, cost_cny=cost)
    with state.get_conn() as conn:
        conn.execute("UPDATE tasks SET provider = ? WHERE task_id = ?", (provider, task_id))
    return {"text": text, "duration_sec": dur, "provider": provider, "cost": cost}


def build_working_from_draft(draft: dict[str, Any]) -> dict[str, Any]:
    """把一整套 draft 展开成 working_draft 初始结构。"""
    did = draft["draft_id"]
    return {
        "hook": {"source_draft": did, "text": draft["hook"], "edited": False},
        "body": [
            {"source_draft": did, "step": b["step"], "role": b.get("role", ""), "text": b["text"], "edited": False}
            for b in draft["body"]
        ],
        "golden": {"source_draft": did, "text": draft["golden"], "edited": False},
        "cta": {"source_draft": did, "text": draft["cta"], "edited": False},
    }
