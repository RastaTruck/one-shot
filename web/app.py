"""一二布布 · AI 视频工作台 · FastAPI 入口。

当前: 项目看板 + 6 个工作台板块 + 素材库，全 Mock 数据。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import json as _json
import sys as _sys
from urllib.parse import quote
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_ROOT))

from pipeline import state as _state  # noqa: E402
from pipeline import shot_table as _shot_table  # noqa: E402
from pipeline import flow as _flow  # noqa: E402
from pipeline.asset_library import AssetLibrary as _AssetLibrary  # noqa: E402
from pipeline.agents import material_agent as _material_agent  # noqa: E402
from pipeline.agents import script_writer as _script_writer  # noqa: E402
from pipeline.agents import segmenter as _segmenter  # noqa: E402
from pipeline.agents import shot_table_agent as _shot_table_agent  # noqa: E402
from pipeline.agents import style_agent as _style_agent  # noqa: E402
from pipeline.agents import topic_generator as _topic_generator  # noqa: E402
from pipeline.agents import video_editor_agent as _video_editor  # noqa: E402
from pipeline.agents import publish_meta_agent as _publish_meta  # noqa: E402
from pipeline.tools import ark_client as _ark_client  # noqa: E402
from pipeline.tools import edge_tts_client as _edge_tts  # noqa: E402
from pipeline.tools import fish_audio_client as _fish_audio  # noqa: E402
from pipeline import outhan_usage_log as _usage  # noqa: E402

# 全局素材库（启动时加载一次；catalog 写改动用 library.save()）
_ASSET_LIB = _AssetLibrary()

_state.init_db()

BASE_DIR = Path(__file__).parent
TEMPLATES = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app = FastAPI(title="一二布布 · AI 视频工作台", version="0.1.0")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

# BGM 文件夹 mount —— 让浏览器能直接试听 /bgm/1.mp3
_BGM_DIR = Path(__file__).resolve().parent.parent / "bgm"
_BGM_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/bgm", StaticFiles(directory=str(_BGM_DIR)), name="bgm")

# 新闻口播专用 BGM 目录（用户提供的素材，避免与一二布布 bgm/ 串）
_NEWS_BGM_DIR = Path(__file__).resolve().parent.parent / "口播bgm"
_NEWS_BGM_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/news-bgm", StaticFiles(directory=str(_NEWS_BGM_DIR)), name="news_bgm")


# 禁缓存 HTML —— 否则 user 浏览器会卡在旧 base.html（旧 CDN 链接 / 旧字段）
@app.middleware("http")
async def _no_cache_html(request, call_next):
    response = await call_next(request)
    ct = response.headers.get("content-type", "")
    if "text/html" in ct:
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


# 域内路由（按域拆分逐步从 app.py 迁出 — 灵感库是起点）
from web.routes.inspiration import router as _inspiration_router  # noqa: E402
app.include_router(_inspiration_router)
from web.routes.style_api import router as _style_api_router  # noqa: E402
app.include_router(_style_api_router)
from web.routes.forge import router as _forge_router  # noqa: E402
app.include_router(_forge_router)

# /data 暴露项目 data/ 目录（audio / outputs 给浏览器直接读）
_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_DATA_DIR.mkdir(parents=True, exist_ok=True)
(_DATA_DIR / "audio").mkdir(exist_ok=True)
app.mount("/data", StaticFiles(directory=str(_DATA_DIR)), name="data")


# ==================== 步骤元数据 ====================
# 单一真相源在 pipeline/flow.py。这里不再重复定义 STEPS / 顺序 / 完成判定。
# 模板渲染时通过 build_ctx 注入 steps_view（已包含 done / blocker / url / current）。


# ==================== Mock 项目数据 ====================
MOCK_HERO_STATS = [
    {"label": "本月产出",  "value": "12",  "delta": "+3 vs 上月"},
    {"label": "进行中",    "value": "4",   "delta": None},
    {"label": "素材库",    "value": "127", "delta": "+8 本周"},
    {"label": "平均成本",  "value": "¥24", "delta": None},
]

MOCK_QUICK_ACTIONS = [
    {"title": "热点工坊", "desc": "从当日热搜 + 财经 RSS + 长尾雷达里挑信号，AI 帮你立意，一键送入下游管线",
     "icon": "radar", "bg": "#ECFEFF", "fg": "#0E7490", "href": "/forge"},
    {"title": "开始新选题", "desc": "从已收藏的灵感片段（钩子/金句/案例/CTA）里挑选，作为新选题的起点",
     "icon": "sparkles", "bg": "#FFF8EC", "fg": "#D06E05", "href": "/inspiration"},
    {"title": "逛素材库", "desc": "查看背景 / 立绘 / 元素 / 表情视频四大仓库",
     "icon": "images", "bg": "#EEF4FF", "fg": "#1E40AF", "href": "/library"},
]

MOCK_PROJECTS = [
    {"video_id": "V0001", "title": "做空是什么？3 分钟看懂熊市赚钱逻辑",
     "hook": "你知道股票跌了也能赚钱吗？", "category": "金融概念",
     "cover_emoji": "📉", "cover_bg": "linear-gradient(135deg, #F48A0C 0%, #D06E05 60%, #2E2C27 100%)",
     "duration": "01:30", "status_label": "分镜中", "status_badge_class": "badge-brand",
     "step_done": 3, "current_step_icon": "layout-grid",
     "current_step_label": "正在分镜 · 已拆 5 个镜头", "updated_ago": "12 分钟前",
     "next_step": "shots"},
    {"video_id": "V0002", "title": "通货膨胀：为什么你的钱越来越不值钱",
     "hook": "十年前一碗面 5 块，现在 20 块。", "category": "宏观经济",
     "cover_emoji": "🪙", "cover_bg": "linear-gradient(135deg, #1E40AF 0%, #1E3A8A 60%, #0D0D0B 100%)",
     "duration": "02:10", "status_label": "素材生产中", "status_badge_class": "badge-blue",
     "step_done": 4, "current_step_icon": "image",
     "current_step_label": "正在生图 · 2/6 完成", "updated_ago": "1 小时前",
     "next_step": "assets"},
    {"video_id": "V0003", "title": "复利的力量：爱因斯坦称它为世界第八大奇迹",
     "hook": "每天存 10 块，20 年能变多少？", "category": "投资入门",
     "cover_emoji": "🌱", "cover_bg": "linear-gradient(135deg, #047857 0%, #064E3B 60%, #0D0D0B 100%)",
     "duration": "01:45", "status_label": "待终审", "status_badge_class": "badge-green",
     "step_done": 5, "current_step_icon": "film",
     "current_step_label": "成片终审 · 等你确认", "updated_ago": "3 小时前",
     "next_step": "edit"},
    {"video_id": "V0004", "title": "房贷利率下调，现在是上车好时机吗？",
     "hook": "LPR 降了 25 个基点，每月省多少？", "category": "时事热点",
     "cover_emoji": "🏠", "cover_bg": "linear-gradient(135deg, #7C3AED 0%, #5B21B6 60%, #0D0D0B 100%)",
     "duration": "--", "status_label": "文案中", "status_badge_class": "badge-brand",
     "step_done": 1, "current_step_icon": "file-text",
     "current_step_label": "AI 正在撰写 3 版文案", "updated_ago": "刚刚",
     "next_step": "script"},
    {"video_id": "V0005", "title": "为什么 A 股是散户坟场？3 个结构性原因",
     "hook": "一个从未变过的残酷真相。", "category": "市场解读",
     "cover_emoji": "📊", "cover_bg": "linear-gradient(135deg, #DC2626 0%, #991B1B 60%, #0D0D0B 100%)",
     "duration": "02:30", "status_label": "草稿", "status_badge_class": "badge-gray",
     "step_done": 0, "current_step_icon": "lightbulb",
     "current_step_label": "刚建项目 · 待确认选题", "updated_ago": "昨天",
     "next_step": "topic"},
]


# ==================== 各板块 Mock 数据 ====================

# 选题 - 20 个候选
MOCK_TOPIC_POOL = [
    {"id": "T01", "title": "做空是什么？3 分钟看懂熊市赚钱逻辑",
     "category": "概念", "pain": 5, "surprise": 4, "hook": 5, "difficulty": 3, "score": 14,
     "tags": ["做空", "熊市", "金融工具"]},
    {"id": "T02", "title": "通货膨胀：为什么你的钱越来越不值钱",
     "category": "宏观", "pain": 5, "surprise": 3, "hook": 4, "difficulty": 2, "score": 14,
     "tags": ["通胀", "货币"]},
    {"id": "T03", "title": "复利的力量：每天存 10 块 20 年后能变多少",
     "category": "理财", "pain": 3, "surprise": 4, "hook": 5, "difficulty": 2, "score": 12,
     "tags": ["复利", "储蓄"]},
    {"id": "T04", "title": "房贷等额本息 vs 等额本金，谁更省钱",
     "category": "房贷", "pain": 5, "surprise": 4, "hook": 4, "difficulty": 4, "score": 13,
     "tags": ["房贷", "贷款"]},
    {"id": "T05", "title": "为什么 A 股散户 7 赔 2 平 1 赚",
     "category": "市场", "pain": 5, "surprise": 5, "hook": 5, "difficulty": 3, "score": 15,
     "tags": ["A股", "散户"]},
    {"id": "T06", "title": "定投基金真的稳赚不赔吗？一个反常识真相",
     "category": "基金", "pain": 4, "surprise": 5, "hook": 5, "difficulty": 3, "score": 14,
     "tags": ["基金", "定投"]},
    {"id": "T07", "title": "可转债打新：几乎零风险的套利机会",
     "category": "债券", "pain": 3, "surprise": 5, "hook": 5, "difficulty": 4, "score": 13,
     "tags": ["可转债", "打新"]},
    {"id": "T08", "title": "保险里的那些文字游戏你看懂了吗",
     "category": "保险", "pain": 5, "surprise": 4, "hook": 4, "difficulty": 3, "score": 13,
     "tags": ["保险", "条款"]},
    {"id": "T09", "title": "美联储加息为什么影响你的工资",
     "category": "宏观", "pain": 4, "surprise": 5, "hook": 4, "difficulty": 4, "score": 13,
     "tags": ["美联储", "加息"]},
    {"id": "T10", "title": "ETF 是什么？小白也能看懂的指数投资",
     "category": "基金", "pain": 4, "surprise": 3, "hook": 4, "difficulty": 2, "score": 11,
     "tags": ["ETF", "指数"]},
    {"id": "T11", "title": "黄金为什么是避险资产？古今逻辑不变",
     "category": "资产", "pain": 3, "surprise": 4, "hook": 4, "difficulty": 3, "score": 11,
     "tags": ["黄金", "避险"]},
    {"id": "T12", "title": "市盈率 PE 到底看哪个版本",
     "category": "指标", "pain": 4, "surprise": 3, "hook": 3, "difficulty": 4, "score": 10,
     "tags": ["PE", "估值"]},
]

# 文案 - 3 版
MOCK_SCRIPT_VERSIONS = [
    {"id": "v1", "style": "严肃科普",
     "tags": ["专业", "数据驱动", "中立"],
     "hook": "你知道股票跌了也能赚钱吗？这个操作叫「做空」。",
     "body": "做空的逻辑不复杂：向券商借入股票卖出，等股价下跌后再买回来还给券商，差价就是利润。2008 年次贷危机中，迈克尔·贝瑞靠做空 MBS 赚了超过 1 亿美元。但做空有三大风险：①股价理论上可以无限上涨，亏损无上限；②融券成本与利率挂钩；③监管层可能临时禁止做空。",
     "golden": "做空不是看空，是一种风险对冲工具。",
     "cta": "点关注，下期讲讲普通人如何做空 A 股。"},
    {"id": "v2", "style": "网感幽默",
     "tags": ["口语化", "段子感", "共情"],
     "hook": "股票跌的时候，你哭的死去活来，别人却在偷着乐？",
     "body": "对，他们在做空。说白了就是：从券商那借一堆股票，趁现在高价先卖掉，等跌了再买回来还给券商，差价白赚。就像你借邻居一件 2000 的羽绒服，拿去卖了 2000，等到夏天 500 收回来，1500 装进自己口袋。当然啊，万一股票不跌反涨，那你就要花更多钱买回去，亏得你妈都不认识你。",
     "golden": "牛市靠胆子，熊市靠脑子。",
     "cta": "下期讲普通人怎么做空，想看的扣 1。"},
    {"id": "v3", "style": "悬疑引钩",
     "tags": ["故事化", "反常识", "悬念"],
     "hook": "2008 年，有一个男人因为这个操作，净赚了 1 亿美元。",
     "body": "他叫迈克尔·贝瑞。在所有人都以为楼市会涨的时候，他做了一件反人类的事——押注美国房地产会崩盘。这个操作就是今天要说的「做空」。它的核心是「先卖后买」：向券商借股票卖掉，跌了之后再买回来还，差价归你。听起来很神奇？但这也是全世界监管最严的一个操作，因为稍有不慎就会血本无归。",
     "golden": "做空，是资本市场最锋利的一把双刃剑。",
     "cta": "关注我，下期聊聊 A 股做空的具体玩法。"},
]

# ============== 文案页 · 拓扑器渐进展开 ==============

# 第 1 步：3 个钩子方向（只给"点"，不给长文）
MOCK_HOOK_DIRECTIONS = [
    {
        "id": "h_counter", "emoji": "🎭", "label": "反常识提问",
        "color": "#DC2626", "bg": "#FEE2E2",
        "tagline": "用一个颠覆认知的问题开场",
        "preview": "你知道股票跌了也能赚钱吗？",
        "traits": ["立刻勾起好奇", "适合小白受众", "反转感强"],
        "match": 92,  # 和当前风格 DNA 的匹配度
    },
    {
        "id": "h_story", "emoji": "🎬", "label": "故事开场",
        "color": "#1E40AF", "bg": "#EEF4FF",
        "tagline": "从一个真实人物的戏剧性瞬间切入",
        "preview": "2008 年，有一个男人因为这个操作，净赚了 1 亿美元。",
        "traits": ["代入感强", "满足真实案例约束", "观众追完"],
        "match": 88,
    },
    {
        "id": "h_number", "emoji": "💥", "label": "数字震撼",
        "color": "#F48A0C", "bg": "#FFF8EC",
        "tagline": "用一个令人震惊的数字抓住眼球",
        "preview": "150 亿美金 —— 华尔街的大空头是怎么赚到的？",
        "traits": ["视觉冲击力", "金融题材友好", "精准定位"],
        "match": 75,
    },
]

# 第 2 步：3 条叙事骨架（基于选中钩子 h_counter 的分支）
MOCK_SKELETON_PATHS = [
    {
        "id": "sk_analogy", "emoji": "🔄", "label": "类比型",
        "color": "#047857", "bg": "#ECFDF5",
        "tagline": "用生活化类比讲金融原理",
        "nodes": [
            {"step": "A", "label": "钩子", "meta": "反常识提问"},
            {"step": "B", "label": "类比", "meta": "借球鞋卖掉再买回来"},
            {"step": "C", "label": "映射", "meta": "球鞋→股票，赚差价"},
            {"step": "D", "label": "风险", "meta": "鞋子涨价你要亏"},
            {"step": "E", "label": "金句", "meta": "..."},
            {"step": "F", "label": "CTA", "meta": "..."},
        ],
        "pros": "小白零门槛理解",
        "cons": "信息密度偏低",
    },
    {
        "id": "sk_story", "emoji": "📖", "label": "案例型",
        "color": "#7C3AED", "bg": "#F3E8FF",
        "tagline": "用贝瑞的真实故事贯穿",
        "nodes": [
            {"step": "A", "label": "钩子", "meta": "反常识提问"},
            {"step": "B", "label": "引入", "meta": "迈克尔·贝瑞 2008"},
            {"step": "C", "label": "操作", "meta": "做空次贷 MBS"},
            {"step": "D", "label": "结果", "meta": "净赚 1 亿美金"},
            {"step": "E", "label": "原理", "meta": "先借后卖再低价买"},
            {"step": "F", "label": "金句+CTA", "meta": "..."},
        ],
        "pros": "代入感强 · 满足真实人物约束",
        "cons": "需要更多铺垫",
    },
    {
        "id": "sk_logic", "emoji": "🧠", "label": "推理型",
        "color": "#0D0D0B", "bg": "#F4F3EE",
        "tagline": "从'为什么能赚'拆到'怎么做'",
        "nodes": [
            {"step": "A", "label": "钩子", "meta": "反常识提问"},
            {"step": "B", "label": "为什么", "meta": "金融市场允许借卖"},
            {"step": "C", "label": "机制", "meta": "融券/保证金/回补"},
            {"step": "D", "label": "怎么做", "meta": "普通散户的门路"},
            {"step": "E", "label": "风险", "meta": "亏损无上限"},
            {"step": "F", "label": "金句+CTA", "meta": "..."},
        ],
        "pros": "逻辑严密 · 专业感强",
        "cons": "对小白略烧脑",
    },
]

# 第 3 步：段落展开（基于选中 sk_analogy 类比型骨架）
# 每个节点只给 2 个变体，不堆量
MOCK_PARAGRAPH_NODES = [
    {
        "node_id": "N01", "step": "A", "label": "钩子",
        "locked": True,
        "selected_text": "你知道股票跌了也能赚钱吗？",
        "variants": [
            {"id": "v1", "text": "你知道股票跌了也能赚钱吗？", "tone": "提问", "len": 13, "active": True},
            {"id": "v2", "text": "股票跌到地板上，有人却赚到飞起。", "tone": "反差", "len": 15, "active": False},
        ],
    },
    {
        "node_id": "N02", "step": "B", "label": "类比-引入",
        "locked": False,
        "selected_text": "就像你借朋友一件限量球鞋——",
        "variants": [
            {"id": "v1", "text": "就像你借朋友一件限量球鞋——", "tone": "日常", "len": 13, "active": True},
            {"id": "v2", "text": "打个比方。你有个朋友，买了双限量联名。", "tone": "对话", "len": 18, "active": False},
        ],
    },
    {
        "node_id": "N03", "step": "C", "label": "类比-操作",
        "locked": False,
        "selected_text": "趁它热的时候，3000 借来卖了，等降价到 2000 再买回来还他，你净赚 1000。",
        "variants": [
            {"id": "v1", "text": "趁它热的时候，3000 借来卖了，等降价到 2000 再买回来还他，你净赚 1000。", "tone": "解释", "len": 38, "active": True},
            {"id": "v2", "text": "高价卖给球鞋贩子，低价再从二手市场收回来，中间差价揣兜里。", "tone": "口语", "len": 26, "active": False},
        ],
    },
    {
        "node_id": "N04", "step": "D", "label": "风险提醒",
        "locked": False,
        "selected_text": "但如果鞋价涨了呢？你要花更多钱买回去——这就是做空的致命风险。",
        "variants": [
            {"id": "v1", "text": "但如果鞋价涨了呢？你要花更多钱买回去——这就是做空的致命风险。", "tone": "警示", "len": 28, "active": True},
            {"id": "v2", "text": "当然，万一那双鞋断货炒到 5000，你得自己掏 2000 补差价。", "tone": "段子", "len": 25, "active": False},
        ],
    },
]

# 第 4 步：3 个金句候选
MOCK_GOLDEN_QUOTES = [
    {"id": "g1", "text": "牛市靠胆子，熊市靠脑子。", "vibe": "对仗工整", "len": 12, "match": 95},
    {"id": "g2", "text": "做空不是赌跌，是和市场下棋。", "vibe": "意境感", "len": 14, "match": 82},
    {"id": "g3", "text": "股票跌的时候，聪明钱已经在偷着乐。", "vibe": "冲击力", "len": 17, "match": 78},
]

# 第 5 步：3 个 CTA 候选
MOCK_CTA_OPTIONS = [
    {"id": "c1", "text": "点关注，下期讲普通人怎么做空 A 股。", "style": "下期预告", "match": 90},
    {"id": "c2", "text": "想看 A 股做空的具体玩法？扣 1。", "style": "互动扣字", "match": 85},
    {"id": "c3", "text": "金融冷知识每周更新，记得关注。", "style": "常规关注", "match": 70},
]

# 渐进式状态：用户当前走到哪一步
MOCK_SCRIPT_JOURNEY = {
    "current_stage": 3,      # 已经在段落展开阶段
    "total_stages": 5,
    "stages": [
        {"idx": 1, "key": "hook",     "label": "钩子方向", "icon": "sparkles",   "status": "done",    "selected": "反常识提问"},
        {"idx": 2, "key": "skeleton", "label": "叙事骨架", "icon": "git-branch", "status": "done",    "selected": "类比型"},
        {"idx": 3, "key": "paragraph","label": "段落展开", "icon": "file-text",  "status": "current", "selected": ""},
        {"idx": 4, "key": "golden",   "label": "金句",     "icon": "quote",      "status": "pending", "selected": ""},
        {"idx": 5, "key": "cta",      "label": "CTA",      "icon": "megaphone",  "status": "pending", "selected": ""},
    ],
}

# 最终合成预览（mock：假设用户都选完了）
MOCK_FINAL_PREVIEW = {
    "hook": "你知道股票跌了也能赚钱吗？",
    "body_segments": [
        "就像你借朋友一件限量球鞋——",
        "趁它热的时候，3000 借来卖了，等降价到 2000 再买回来还他，你净赚 1000。",
        "但如果鞋价涨了呢？你要花更多钱买回去——这就是做空的致命风险。",
    ],
    "golden": "牛市靠胆子，熊市靠脑子。",
    "cta": "点关注，下期讲普通人怎么做空 A 股。",
    "total_chars": 142,
    "estimated_duration": 58.0,
}


MOCK_SCRIPT_CHECKLIST = [
    {"id": 1, "label": "钩子吸引力", "desc": "前 3 秒能不能让自己想看下去", "ok": True},
    {"id": 2, "label": "术语通俗化", "desc": "有没有金融术语没解释", "ok": True},
    {"id": 3, "label": "知识点单一性", "desc": "是不是只讲 1 个知识点", "ok": True},
    {"id": 4, "label": "金句存在感", "desc": "有没有能被截图的金句", "ok": True},
    {"id": 5, "label": "事实准确性", "desc": "有没有金融常识错误", "ok": True},
    {"id": 6, "label": "合规安全", "desc": "有没有荐股/承诺收益", "ok": True},
    {"id": 7, "label": "朗读流畅度", "desc": "大声读一遍，卡不卡口", "ok": False},
]

# 本项目的素材来源配置 — 进入分镜前选好主 IP、启用哪些元素库
# 画风偏好不在这里 —— 已移到「素材」页做规范提示词 + 示例图确认 + 批量生成流程
MOCK_ASSET_CONFIG = {
    "primary_ip": {"id": "ip_buubuu", "name": "一二布布", "emoji": "🐰", "count": 48},
    "element_libs_enabled": ["finance_icons", "office_props", "life_scene", "ui_symbols"],
}

# 可选表情 IP 池（未来可扩展更多 IP）
MOCK_IP_OPTIONS = [
    {"id": "ip_buubuu", "name": "一二布布", "emoji": "🐰", "count": 48, "desc": "默认 IP · 通用财经/生活"},
    {"id": "ip_apple", "name": "苹果 Memoji", "emoji": "🍎", "count": 120, "desc": "iOS 风格 · 情绪丰富"},
    {"id": "ip_retro_cat", "name": "复古小猫", "emoji": "🐱", "count": 36, "desc": "像素怀旧风"},
    {"id": "ip_custom_01", "name": "我的自建 IP", "emoji": "✨", "count": 8, "desc": "自制 8 个 · 持续录入中"},
]

# 元素素材库（通用，按类别分组；每个库里可能同元素有多画风）
MOCK_ELEMENT_LIBS = [
    {"id": "finance_icons", "name": "金融图标", "emoji": "📊", "count": 32,
     "tags_sample": ["K线","箭头","数字牌","钱袋","牛熊","股票板"]},
    {"id": "office_props", "name": "办公道具", "emoji": "🏢", "count": 24,
     "tags_sample": ["便签","图钉","重点章","订阅钮","键盘"]},
    {"id": "life_scene", "name": "生活场景", "emoji": "🏠", "count": 68,
     "tags_sample": ["球鞋","咖啡","手机","购物车","汽车"]},
    {"id": "pets_anim", "name": "宠物动物", "emoji": "🐾", "count": 18,
     "tags_sample": ["狗","猫","鸟","兔子"]},
    {"id": "ui_symbols", "name": "UI 符号", "emoji": "🔔", "count": 16,
     "tags_sample": ["关注按钮","点赞","评论","分享"]},
]

# 分镜 - 7 个镜头
# 五段流水线: 文案 → 场景识别 → 表情库匹配(IP) → 元素库匹配 → 画框新生成(兜底)
MOCK_SHOT_TABLE = [
    {"id": "S01", "section": "钩子", "section_color": "#F48A0C",
     "time": "0.0 - 5.3s", "duration": 5.3,
     "text": "你知道股票跌了也能赚钱吗？",
     "detected_tags": [{"tag": "震惊", "intensity": 3, "reason": "反常识设问，制造悬念"}],
     "candidates": [
         {"id": "EXPR_SHOCK_01", "emoji": "😲", "desc": "布布惊讶捂嘴", "scene_tags": ["震惊"], "intensity": 3, "duration": 2.4, "selected": True},
         {"id": "EXPR_SHOCK_02", "emoji": "😮", "desc": "布布张嘴瞪眼", "scene_tags": ["震惊"], "intensity": 2, "duration": 2.0, "selected": False},
         {"id": "EXPR_STARTLED_01", "emoji": "🫨", "desc": "布布受惊摇头", "scene_tags": ["震惊", "害怕"], "intensity": 3, "duration": 2.6, "selected": False},
     ],
     "matched_elements": [],
     "custom_insets": [],
     "ready": True},
    {"id": "S02", "section": "共鸣", "section_color": "#1E40AF",
     "time": "5.3 - 18.9s", "duration": 13.6,
     "text": "去年大盘跌的时候，身边人都在哭，但有人反而赚了 30%。",
     "detected_tags": [
         {"tag": "担忧", "intensity": 2, "reason": "描述熊市亏损情绪"},
         {"tag": "失落", "intensity": 2, "reason": "身边人哭，情绪低落"},
     ],
     "candidates": [
         {"id": "EXPR_WORRIED_02", "emoji": "😟", "desc": "布布皱眉忧虑", "scene_tags": ["担忧", "失落"], "intensity": 2, "duration": 3.0, "selected": True},
         {"id": "EXPR_SAD_01", "emoji": "😔", "desc": "布布低头失落", "scene_tags": ["失落"], "intensity": 2, "duration": 2.8, "selected": False},
     ],
     "matched_elements": [
         {"keyword": "K 线下跌", "source_phrase": "大盘跌的时候", "lib": "金融图标",
          "at": 1.0, "duration": 3.0, "frame": "tv_screen", "pos": "top_right",
          "styles": [
              {"id": "EL_KLINE_DOWN_MBE", "emoji": "📉", "style": "MBE 扁平", "picked": True},
              {"id": "EL_KLINE_DOWN_PIXEL", "emoji": "📊", "style": "像素复古", "picked": False},
              {"id": "EL_KLINE_DOWN_HAND", "emoji": "📈", "style": "手绘插画", "picked": False},
          ]},
     ],
     "custom_insets": [],
     "ready": False},
    {"id": "S03", "section": "知识点", "section_color": "#047857",
     "time": "18.9 - 35.2s", "duration": 16.3,
     "text": "这件事叫\"做空\"。简单说：你先借来股票卖掉，跌了之后再买回来还。",
     "detected_tags": [{"tag": "讲解", "intensity": 2, "reason": "定义概念并拆步骤"}],
     "candidates": [
         {"id": "EXPR_EXPLAIN_01", "emoji": "🧑‍🏫", "desc": "布布指点讲解", "scene_tags": ["讲解"], "intensity": 2, "duration": 4.0, "selected": True},
         {"id": "EXPR_EXPLAIN_02", "emoji": "☝️", "desc": "布布食指上举", "scene_tags": ["讲解"], "intensity": 1, "duration": 3.2, "selected": False},
     ],
     "matched_elements": [
         {"keyword": "下跌箭头", "source_phrase": "跌了之后", "lib": "金融图标",
          "at": 3.6, "duration": 2.0, "frame": "sticky_note", "pos": "top_right",
          "styles": [
              {"id": "EL_ARROW_DOWN_MBE", "emoji": "⬇️", "style": "MBE 扁平", "picked": True},
              {"id": "EL_ARROW_DOWN_HAND", "emoji": "↘️", "style": "手绘插画", "picked": False},
          ]},
         {"keyword": "重点标签", "source_phrase": "再买回来还", "lib": "办公道具",
          "at": 9.9, "duration": 2.5, "frame": "no_frame", "pos": "bottom_left",
          "styles": [
              {"id": "EL_LABEL_IMPORTANT_MBE", "emoji": "⚠️", "style": "MBE 扁平", "picked": True},
          ]},
     ],
     "custom_insets": [],
     "ready": True},
    {"id": "S04", "section": "知识点", "section_color": "#047857",
     "time": "35.2 - 55.4s", "duration": 20.2,
     "text": "就像你借朋友一件限量球鞋，3000 卖掉，降价到 2000 再买回来还他，净赚 1000。",
     "detected_tags": [
         {"tag": "讲解", "intensity": 2, "reason": "打类比拆解过程"},
         {"tag": "得意", "intensity": 2, "reason": "净赚 1000 的愉悦"},
     ],
     "candidates": [
         {"id": "EXPR_PROUD_01", "emoji": "😏", "desc": "布布得意挑眉", "scene_tags": ["得意"], "intensity": 2, "duration": 2.8, "selected": True},
         {"id": "EXPR_EXPLAIN_01", "emoji": "🧑‍🏫", "desc": "布布指点讲解", "scene_tags": ["讲解"], "intensity": 2, "duration": 4.0, "selected": False},
     ],
     "matched_elements": [
         {"keyword": "球鞋", "source_phrase": "限量球鞋", "lib": "生活场景",
          "at": 3.3, "duration": 4.0, "frame": "polaroid", "pos": "top_right",
          "styles": [
              {"id": "EL_SNEAKER_MBE", "emoji": "👟", "style": "MBE 扁平", "picked": True},
              {"id": "EL_SNEAKER_PIXEL", "emoji": "🥿", "style": "像素复古", "picked": False},
          ]},
         {"keyword": "钱袋", "source_phrase": "净赚 1000", "lib": "金融图标",
          "at": 13.4, "duration": 3.0, "frame": "circle_window", "pos": "bottom_left",
          "styles": [
              {"id": "EL_MONEY_BAG_MBE", "emoji": "💰", "style": "MBE 扁平", "picked": True},
          ]},
     ],
     "custom_insets": [
         {"label": "价格牌 3000→2000", "emoji": "🏷️", "at": 7.5, "duration": 2.5, "frame": "polaroid", "pos": "top_right", "source_phrase": "3000 卖掉，降价到 2000", "reason": "元素库无「阶梯价格」现成款，需针对本视频生成", "status": "pending"},
     ],
     "ready": False},
    {"id": "S05", "section": "知识点", "section_color": "#047857",
     "time": "55.4 - 75.1s", "duration": 19.7,
     "text": "华尔街大空头迈克尔·贝瑞，2008 年做空次贷，一战赚了 150 亿美金。",
     "detected_tags": [{"tag": "严肃", "intensity": 2, "reason": "讲真实案例，语气沉稳"}],
     "candidates": [
         {"id": "EXPR_SERIOUS_01", "emoji": "😐", "desc": "布布严肃点头", "scene_tags": ["严肃"], "intensity": 2, "duration": 3.2, "selected": True},
     ],
     "matched_elements": [],
     "custom_insets": [
         {"label": "150 亿美金数字牌", "emoji": "💵", "at": 5.4, "duration": 3.5, "frame": "tv_screen", "pos": "top_right", "source_phrase": "赚了 150 亿美金", "reason": "具体数字 + 美金标识，元素库无现成组合", "status": "generating"},
     ],
     "ready": False},
    {"id": "S06", "section": "金句", "section_color": "#DC2626",
     "time": "75.1 - 85.5s", "duration": 10.4,
     "text": "牛市赚钱靠勇气，熊市赚钱靠做空。",
     "detected_tags": [{"tag": "得意", "intensity": 3, "reason": "金句点题，自信收束"}],
     "candidates": [
         {"id": "EXPR_CONFIDENT_01", "emoji": "😎", "desc": "布布自信指镜头", "scene_tags": ["得意"], "intensity": 3, "duration": 2.6, "selected": True},
         {"id": "EXPR_PROUD_01", "emoji": "😏", "desc": "布布得意挑眉", "scene_tags": ["得意"], "intensity": 2, "duration": 2.8, "selected": False},
     ],
     "matched_elements": [
         {"keyword": "牛熊 icon", "source_phrase": "牛市 / 熊市", "lib": "金融图标",
          "at": 0.5, "duration": 4.5, "frame": "no_frame", "pos": "center",
          "styles": [
              {"id": "EL_BULL_BEAR_MBE", "emoji": "🐂", "style": "MBE 扁平", "picked": True},
              {"id": "EL_BULL_BEAR_HAND", "emoji": "🐻", "style": "手绘插画", "picked": False},
          ]},
     ],
     "custom_insets": [],
     "ready": True},
    {"id": "S07", "section": "CTA", "section_color": "#7C3AED",
     "time": "85.5 - 90.2s", "duration": 4.7,
     "text": "想听更多金融冷知识，点个关注。",
     "detected_tags": [{"tag": "兴奋", "intensity": 2, "reason": "召唤关注的号召"}],
     "candidates": [],
     "matched_elements": [
         {"keyword": "关注按钮", "source_phrase": "点个关注", "lib": "UI 符号",
          "at": 0.8, "duration": 3.0, "frame": "sticky_note", "pos": "center",
          "styles": [
              {"id": "EL_SUBSCRIBE_MBE", "emoji": "🔔", "style": "MBE 扁平", "picked": True},
              {"id": "EL_SUBSCRIBE_3D", "emoji": "📣", "style": "3D 卡通", "picked": False},
          ]},
     ],
     "custom_insets": [],
     "ready": False},
]

# 素材生产 - 三阶段数据
# 阶段 1: 画风校准（规范提示词 + 示例图预览确认）
# 阶段 2: 画框内元素批量生成（standard + scene prompt per inset）
# 阶段 3: 配音 TTS
MOCK_ASSETS = {
    # 阶段 1: 画风校准
    "style_calibration": {
        "standard_prompt": (
            "MBE 扁平插画风，粗黑描边(2-3px)，饱和暖色块，白底，16:9 中心构图，"
            "无阴影渐变，简洁几何图形，童趣可爱，与人物立绘同款画风"
        ),
        "negative_prompt": "真实照片, 3D渲染, 复杂纹理, 写实阴影, 水印文字",
        "style_hint": "MBE 扁平",       # 来自用户选择
        "samples": [
            # 用「规范提示词 + 默认测试题材」生成的示例图，给用户确认画风
            {"id": "SAMPLE_01", "prompt_extra": "一只咖啡杯", "emoji": "☕",
             "thumb_bg": "linear-gradient(135deg,#FDE68A,#F59E0B)", "status": "done", "picked": True},
            {"id": "SAMPLE_02", "prompt_extra": "一本翻开的书", "emoji": "📖",
             "thumb_bg": "linear-gradient(135deg,#E0F2FE,#0EA5E9)", "status": "done", "picked": False},
            {"id": "SAMPLE_03", "prompt_extra": "一把雨伞", "emoji": "☂️",
             "thumb_bg": "linear-gradient(135deg,#FCE7F3,#EC4899)", "status": "done", "picked": False},
        ],
        "confirmed": True,   # 用户已确认此画风 → 才能进入阶段 2 批量生成
    },
    # 阶段 2: 画框内元素批量生成（只生成 shot.custom_insets 里的，其余都从库里拉）
    "insets": [
        {"id": "I01", "shot": "S04", "label": "价格牌 3000→2000", "desc": "红色价格牌阶梯式下跌",
         "source_phrase": "3000 卖掉，降价到 2000",
         "scene_prompt": "一块木质复古价格牌，上面写着大字 3000 带删除线，旁边箭头指向新价 2000，红色强调",
         "frame": "polaroid", "status": "pending", "emoji": "🏷️",
         "thumb_bg": "linear-gradient(135deg,#FECACA,#DC2626)"},
        {"id": "I02", "shot": "S05", "label": "150 亿美金数字牌", "desc": "金色立体数字 $15B",
         "source_phrase": "赚了 150 亿美金",
         "scene_prompt": "巨幅金色数字 150 亿美金（$15B），金光闪耀，配美元符号装饰",
         "frame": "tv_screen", "status": "generating", "emoji": "💵",
         "thumb_bg": "linear-gradient(135deg,#FEF3C7,#F59E0B)"},
    ],
    # 阶段 1 依赖：只有全局背景缺件时才需要跑（通常 0 个）
    "backgrounds_missing": [],
    "expression_hits": {
        "matched": 6, "total": 7,
        "unmatched_shots": [{"shot": "S07", "scene_tags": ["兴奋"]}],
    },
    "element_hits": {
        "matched": 7, "total_needed": 9,   # 7 个命中元素库，2 个要生成 (= insets)
    },
    "audios": [
        {"id": "A01", "shot": "S01", "label": "V0001_S01.mp3", "text": "你知道股票跌了也能赚钱吗？", "status": "done", "duration": "5.3s"},
        {"id": "A02", "shot": "S02", "label": "V0001_S02.mp3", "text": "去年大盘跌的时候，身边人都在哭...", "status": "done", "duration": "13.6s"},
        {"id": "A03", "shot": "S03", "label": "V0001_S03.mp3", "text": "这件事叫\"做空\"。简单说...", "status": "done", "duration": "16.3s"},
        {"id": "A04", "shot": "S04", "label": "V0001_S04.mp3", "text": "就像你借朋友一件限量球鞋...", "status": "generating", "duration": "--"},
        {"id": "A05", "shot": "S05", "label": "V0001_S05.mp3", "text": "华尔街大空头迈克尔·贝瑞...", "status": "pending", "duration": "--"},
        {"id": "A06", "shot": "S06", "label": "V0001_S06.mp3", "text": "牛市赚钱靠勇气，熊市赚钱靠做空。", "status": "pending", "duration": "--"},
        {"id": "A07", "shot": "S07", "label": "V0001_S07.mp3", "text": "想听更多金融冷知识，点个关注。", "status": "pending", "duration": "--"},
    ]
}

# 配音&字幕
MOCK_SUBTITLES = [
    {"idx": 1, "start": "00:00:00,000", "end": "00:00:05,300", "text": "你知道股票跌了也能赚钱吗？"},
    {"idx": 2, "start": "00:00:05,300", "end": "00:00:12,100", "text": "去年大盘跌的时候，身边人都在哭，"},
    {"idx": 3, "start": "00:00:12,100", "end": "00:00:18,900", "text": "但有人反而赚了 30%。"},
    {"idx": 4, "start": "00:00:18,900", "end": "00:00:26,500", "text": "这件事叫\"做空\"。"},
    {"idx": 5, "start": "00:00:26,500", "end": "00:00:35,200", "text": "简单说：你先借来股票卖掉，跌了之后再买回来还。"},
    {"idx": 6, "start": "00:00:35,200", "end": "00:00:45,800", "text": "就像你借朋友一件限量球鞋，3000 卖掉，"},
    {"idx": 7, "start": "00:00:45,800", "end": "00:00:55,400", "text": "降价到 2000 再买回来还他，净赚 1000。"},
    {"idx": 8, "start": "00:00:55,400", "end": "00:01:05,000", "text": "华尔街大空头迈克尔·贝瑞，"},
    {"idx": 9, "start": "00:01:05,000", "end": "00:01:15,100", "text": "2008 年做空次贷，一战赚了 150 亿美金。"},
    {"idx": 10, "start": "00:01:15,100", "end": "00:01:25,500", "text": "牛市赚钱靠勇气，熊市赚钱靠做空。"},
    {"idx": 11, "start": "00:01:25,500", "end": "00:01:30,200", "text": "想听更多金融冷知识，点个关注。"},
]

# 素材库 - 演示
MOCK_LIBRARY = {
    "backgrounds": [
        {"id": "BG_OFFICE_01", "tags": ["办公室","书桌","白天"], "emoji": "💼", "bg": "linear-gradient(135deg,#FED7AA,#F48A0C)"},
        {"id": "BG_STOCK_SCREEN_01", "tags": ["股市","大屏","K线"], "emoji": "📈", "bg": "linear-gradient(135deg,#FECACA,#DC2626)"},
        {"id": "BG_BOARD_01", "tags": ["白板","教学"], "emoji": "📋", "bg": "linear-gradient(135deg,#E0F2FE,#0EA5E9)"},
        {"id": "BG_BANK_01", "tags": ["银行","大厅"], "emoji": "🏦", "bg": "linear-gradient(135deg,#D1FAE5,#047857)"},
        {"id": "BG_HOME_01", "tags": ["家","客厅"], "emoji": "🏠", "bg": "linear-gradient(135deg,#F3E8FF,#7C3AED)"},
        {"id": "BG_CITY_NIGHT_01", "tags": ["城市","夜景"], "emoji": "🌃", "bg": "linear-gradient(135deg,#1E3A8A,#0D0D0B)"},
        {"id": "BG_COFFEE_01", "tags": ["咖啡厅","轻松"], "emoji": "☕", "bg": "linear-gradient(135deg,#FDE68A,#92400E)"},
        {"id": "BG_GRADIENT_01", "tags": ["纯色","金句"], "emoji": "🎨", "bg": "linear-gradient(135deg,#F48A0C,#DC2626)"},
    ],
    "characters": [
        {"id": "CHAR_SURPRISED", "tags": ["惊讶","情绪"], "emoji": "😲", "bg": "linear-gradient(135deg,#FEF3C7,#F59E0B)"},
        {"id": "CHAR_THINKING", "tags": ["思考"], "emoji": "🤔", "bg": "linear-gradient(135deg,#DBEAFE,#3B82F6)"},
        {"id": "CHAR_EXPLAIN", "tags": ["讲解","标准"], "emoji": "🧑‍🏫", "bg": "linear-gradient(135deg,#D1FAE5,#059669)"},
        {"id": "CHAR_HAPPY", "tags": ["开心","得意"], "emoji": "😄", "bg": "linear-gradient(135deg,#FCE7F3,#EC4899)"},
        {"id": "CHAR_CONFIDENT", "tags": ["自信"], "emoji": "😎", "bg": "linear-gradient(135deg,#E0E7FF,#4F46E5)"},
        {"id": "CHAR_WAVE", "tags": ["挥手","告别"], "emoji": "👋", "bg": "linear-gradient(135deg,#FEF3C7,#D97706)"},
    ],
    "elements": [
        {"id": "EL_KLINE_DOWN", "tags": ["K线","红色"], "emoji": "📉", "bg": "#FEE2E2"},
        {"id": "EL_ARROW_DOWN", "tags": ["箭头"], "emoji": "⬇️", "bg": "#FEE2E2"},
        {"id": "EL_LABEL_IMPORTANT", "tags": ["标签","重点"], "emoji": "⚠️", "bg": "#FEF3C7"},
        {"id": "EL_MONEY_BAG", "tags": ["钱袋","收益"], "emoji": "💰", "bg": "#FEF3C7"},
        {"id": "EL_SHOE_ICON", "tags": ["球鞋","类比"], "emoji": "👟", "bg": "#E0E7FF"},
        {"id": "EL_SUBSCRIBE_BTN", "tags": ["订阅","CTA"], "emoji": "🔔", "bg": "#FEE2E2"},
        {"id": "EL_QUOTE_FRAME", "tags": ["金句框"], "emoji": "❝", "bg": "#FEF3C7"},
        {"id": "EL_NUMBER_150B", "tags": ["数字","150亿"], "emoji": "💵", "bg": "#D1FAE5"},
    ],
    "expressions": [
        {"id": "EXPR_SHOCK_01", "scene_tags": ["震惊"], "intensity": 3, "duration": 2.4, "character": "一二布布", "emoji": "😲", "bg": "linear-gradient(135deg,#FED7AA,#F48A0C)"},
        {"id": "EXPR_WORRIED_02", "scene_tags": ["担忧", "失落"], "intensity": 2, "duration": 3.0, "character": "一二布布", "emoji": "😟", "bg": "linear-gradient(135deg,#DBEAFE,#1E40AF)"},
        {"id": "EXPR_EXPLAIN_01", "scene_tags": ["讲解"], "intensity": 2, "duration": 4.0, "character": "一二布布", "emoji": "🧑‍🏫", "bg": "linear-gradient(135deg,#D1FAE5,#047857)"},
        {"id": "EXPR_PROUD_01", "scene_tags": ["得意"], "intensity": 2, "duration": 2.8, "character": "一二布布", "emoji": "😏", "bg": "linear-gradient(135deg,#FEF3C7,#F59E0B)"},
        {"id": "EXPR_SERIOUS_01", "scene_tags": ["严肃"], "intensity": 2, "duration": 3.2, "character": "一二布布", "emoji": "😐", "bg": "linear-gradient(135deg,#E5E7EB,#6B7280)"},
        {"id": "EXPR_CONFIDENT_01", "scene_tags": ["得意"], "intensity": 3, "duration": 2.6, "character": "一二布布", "emoji": "😎", "bg": "linear-gradient(135deg,#FEE2E2,#DC2626)"},
        {"id": "EXPR_CONFUSED_01", "scene_tags": ["困惑"], "intensity": 2, "duration": 2.5, "character": "一二布布", "emoji": "🤨", "bg": "linear-gradient(135deg,#F3E8FF,#7C3AED)"},
        {"id": "EXPR_THINKING_01", "scene_tags": ["沉思"], "intensity": 1, "duration": 3.5, "character": "一二布布", "emoji": "🤔", "bg": "linear-gradient(135deg,#E0F2FE,#0EA5E9)"},
        {"id": "EXPR_PANIC_01", "scene_tags": ["恐慌"], "intensity": 3, "duration": 2.0, "character": "一二布布", "emoji": "😱", "bg": "linear-gradient(135deg,#FEE2E2,#B91C1C)"},
        {"id": "EXPR_SPEECHLESS_01", "scene_tags": ["无语"], "intensity": 2, "duration": 2.4, "character": "一二布布", "emoji": "😑", "bg": "linear-gradient(135deg,#F1F5F9,#64748B)"},
    ],
    "scene_tags": ["震惊","困惑","沉思","讲解","严肃","失落","得意","恐慌","担忧","兴奋","无语","愤怒","调侃","害怕","平静"],
    "frame_styles": ["polaroid","tv_screen","sticky_note","circle_window","no_frame"],
}


# ==================== 通用上下文 ====================

import time as _time
import shutil as _shutil

def _status_for(progress: "_flow.Progress") -> tuple[str, str, str]:
    """从 progress 派生 (status_label, badge_class, current_step_label)。"""
    all_done = all(sv.done for sv in progress.steps_view)
    if all_done:
        return ("已合成", "badge-green", "成片完成")
    cur = progress.current
    cur_short = cur.step.label_short
    if progress.can_advance and progress.next_step is not None:
        nxt_short = progress.next_step.label_short
        return (f"{cur_short}就绪", "badge-brand", f"{cur_short}完成 · 待{nxt_short}")
    blocker = cur.blocker or f"进行中：{cur_short}"
    return (f"{cur_short}中", "badge-brand", blocker)


def _humanize_ago(ts: float | None) -> str:
    if not ts:
        return "—"
    sec = max(0, _time.time() - ts)
    if sec < 60: return "刚刚"
    if sec < 3600: return f"{int(sec/60)} 分钟前"
    if sec < 86400: return f"{int(sec/3600)} 小时前"
    return f"{int(sec/86400)} 天前"


def _build_project_view(video_id: str) -> dict | None:
    """从 DB 实体 + flow.compute_progress 构造项目视图字典。不命中返回 None。

    [AI-NOTE] 2026-05-07: Outhan 类型项目走另一条流程（无 progress / 无 shot_table），
    给一个简化视图：next_step="outhan"，状态显示 "Outhan 通用"。
    """
    db_video = _state.get_video(video_id)
    if db_video is None:
        return None
    if db_video.project_type == "outhan":
        return {
            "video_id": video_id,
            "title": db_video.title or video_id,
            "hook": db_video.topic or "",
            "category": "Outhan 通用",
            "cover_emoji": "🎬",
            "cover_bg": "linear-gradient(135deg, #2C5282 0%, #1A365D 60%, #0D0D0B 100%)",
            "duration": "—",
            "status_label": "Outhan",
            "status_badge_class": "badge-brand",
            "step_done": 0,
            "current_step_icon": "film",
            "current_step_label": "通用单条视频流程",
            "updated_ago": _humanize_ago(db_video.updated_at),
            "next_step": "outhan",
            "from_db": True,
            "is_demo": False,
            "project_type": "outhan",
        }
    if db_video.project_type == "news":
        return {
            "video_id": video_id,
            "title": db_video.title or video_id,
            "hook": db_video.topic or "",
            "category": "新闻口播",
            "cover_emoji": "🎤",
            "cover_bg": "linear-gradient(135deg, #047857 0%, #064E3B 60%, #022C22 100%)",
            "duration": "—",
            "status_label": "News",
            "status_badge_class": "badge-brand",
            "step_done": 0,
            "current_step_icon": "mic",
            "current_step_label": "口播 lipsync 流水线",
            "updated_ago": _humanize_ago(db_video.updated_at),
            "next_step": "news",
            "from_db": True,
            "is_demo": False,
            "project_type": "news",
        }
    progress = _flow.compute_progress(video_id, _DATA_DIR / "outputs")
    label, badge, current_label = _status_for(progress)
    done_count = sum(1 for sv in progress.steps_view if sv.done)

    duration_str = "—"
    if progress.state.shot_table:
        total = progress.state.shot_table.get("total_duration_estimated") or 0
        if total:
            m, s = int(total // 60), int(total % 60)
            duration_str = f"{m:02d}:{s:02d}"

    title = db_video.title or video_id
    picked = progress.state.topic
    if picked and picked.get("title"):
        title = picked["title"]
    hook = (picked.get("hook") if picked else None) or (db_video.topic or "")
    hook = str(hook) if hook is not None else ""
    category = (picked.get("category") if picked else None) or "新项目"
    category = str(category) if category is not None else "新项目"

    next_step_key = progress.next_step.key if progress.next_step else progress.current.step.key
    return {
        "video_id": video_id,
        "title": title,
        "hook": hook[:60],
        "category": category,
        "cover_emoji": "📹",
        "cover_bg": "linear-gradient(135deg, #F48A0C 0%, #D06E05 60%, #2E2C27 100%)",
        "duration": duration_str,
        "status_label": label,
        "status_badge_class": badge,
        "step_done": done_count,
        "current_step_icon": progress.current.step.icon,
        "current_step_label": current_label,
        "updated_ago": _humanize_ago(db_video.updated_at),
        "next_step": next_step_key,
        "from_db": True,
        "is_demo": False,
        "topic_id": picked.get("id") if picked else None,
        "topic_score": picked.get("score") if picked else None,
        "topic_category": picked.get("category") if picked else None,
        "_progress": progress,
    }


def get_project(video_id: str) -> dict | None:
    """优先 DB 视图；如同时有 MOCK 美化字段则借用 cover/category。"""
    db_view = _build_project_view(video_id)
    mock_view = next((p for p in MOCK_PROJECTS if p["video_id"] == video_id), None)
    if db_view:
        if mock_view:  # 借 MOCK 的 cover_emoji / cover_bg / category
            for k in ("cover_emoji", "cover_bg"):
                db_view[k] = mock_view.get(k, db_view[k])
            if db_view["category"] in ("新项目", ""):
                db_view["category"] = mock_view.get("category", db_view["category"])
        style = _state.load_project_style(video_id)
        if style:
            db_view["style_preset"] = style
        return db_view
    if mock_view:
        proj = dict(mock_view)
        proj["from_db"] = False
        proj["is_demo"] = True
        return proj
    return None


def _steps_view_for_template(progress: "_flow.Progress | None", video_id: str | None) -> list[dict]:
    """把 flow.Progress.steps_view 转成 Jinja 友好的 dict 列表。

    progress=None 时（mock 项目 / 无 video_id）退化为静态列表（全 not done）。
    script step 会被 override 到 /script/full 子页（如果已生成完整文稿）。
    """
    overrides: dict[str, str] = {}
    if video_id and _state.load_script_full(video_id):
        overrides["script"] = f"/project/{video_id}/script/full"

    if progress is None:
        return [
            {
                "key": s.key, "label": s.label, "label_short": s.label_short, "icon": s.icon,
                "url": overrides.get(s.key) or (s.url_for(video_id) if video_id else "#"),
                "done": False, "is_current": False, "blocker": "",
            }
            for s in _flow.STEPS
        ]
    return [
        {
            "key": sv.step.key, "label": sv.step.label, "label_short": sv.step.label_short,
            "icon": sv.step.icon,
            "url": overrides.get(sv.step.key) or sv.url,
            "done": sv.done, "is_current": sv.is_current, "blocker": sv.blocker,
        }
        for sv in progress.steps_view
    ]


def build_ctx(request: Request, active_nav: str, video_id: str | None = None, **extra) -> dict[str, Any]:
    """注入：
    - steps_view: 整体步骤渲染数据（stepper / 侧边栏用）
    - next_step_url / next_step_label: **当前 active_nav 这一步的下一步**（不是 progress 全局 current 的下一步）
    - can_advance: 当前页面对应的步骤是否已 done（决定下一步按钮是否可点）
    - current_blocker: 当前步未完成的原因（disabled 状态下当 tooltip）
    """
    ctx: dict[str, Any] = {
        "request": request,
        "active_nav": active_nav,
        "current_video": None,
        "progress": None,
        "steps_view": _steps_view_for_template(None, video_id),
        "next_step_url": None,
        "next_step_label": None,
        "can_advance": False,
        "current_blocker": "",
    }
    if video_id:
        proj = get_project(video_id)
        if proj:
            ctx["current_video"] = proj
            progress = proj.get("_progress")
            if progress is not None:
                ctx["progress"] = progress
                ctx["steps_view"] = _steps_view_for_template(progress, video_id)

                # 当前页面对应步骤的"下一步"（按流水线顺序）
                if active_nav in _flow.STEP_KEYS:
                    cur_idx = _flow.STEP_KEYS.index(active_nav)
                    sv = progress.steps_view[cur_idx]
                    ctx["can_advance"] = sv.done
                    ctx["current_blocker"] = sv.blocker
                    if cur_idx + 1 < len(_flow.STEPS):
                        nxt = _flow.STEPS[cur_idx + 1]
                        ctx["next_step_url"] = nxt.url_for(video_id)
                        ctx["next_step_label"] = nxt.label_short
    ctx.update(extra)
    return ctx


# ==================== 路由 ====================
def _next_video_id() -> str:
    """找下一个可用 V_xxxx ID（避开 DB + MOCK 已占用的）。"""
    used = {v.video_id for v in _state.list_videos()}
    used |= {p["video_id"] for p in MOCK_PROJECTS}
    n = 10  # 0001-0009 留给 mock + 既有
    while f"V{n:04d}" in used:
        n += 1
    return f"V{n:04d}"


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    # 真实项目（DB 视图，按更新时间倒序）
    db_views = [get_project(v.video_id) for v in _state.list_videos()]
    db_views = [v for v in db_views if v]  # 过滤空
    db_ids = {v["video_id"] for v in db_views}
    # MOCK demo（仅展示未在 DB 出现的占位项目，标 is_demo）
    demo_views = [
        dict(p, is_demo=True, from_db=False)
        for p in MOCK_PROJECTS if p["video_id"] not in db_ids
    ]
    return TEMPLATES.TemplateResponse("projects.html", build_ctx(
        request, "projects",
        hero_stats=MOCK_HERO_STATS,
        quick_actions=MOCK_QUICK_ACTIONS,
        projects=db_views + demo_views,
    ))


@app.get("/project/new")
async def project_new(type: str = "yierbubu"):
    """新建项目入口。

    [AI-NOTE] 2026-05-07: 通过 ?type 参数区分管线模块——
      - yierbubu（默认/原一二布布 IP 流程）→ /project/{id}/topic
      - outhan（Outhan 通用单条视频流程）→ /project/{id}/outhan
      - news（新闻口播 lipsync 流水线，骨架）→ /project/{id}/news
    """
    if type not in ("yierbubu", "outhan", "news"):
        type = "yierbubu"
    new_id = _next_video_id()
    _state.create_video(
        new_id,
        title=f"未命名项目 {new_id}",
        topic=None,
        project_type=type,
    )
    if type == "outhan":
        return RedirectResponse(url=f"/project/{new_id}/outhan", status_code=303)
    if type == "news":
        return RedirectResponse(url=f"/project/{new_id}/news", status_code=303)
    return RedirectResponse(url=f"/project/{new_id}/topic", status_code=303)


# ========== 新闻口播管线 · 路由 + API（真闭环 v0） ==========
_NEWS_AUDIO_DIR = _DATA_DIR / "news_audio"
_NEWS_VIDEO_DIR = _DATA_DIR / "news_video"
_NEWS_AUDIO_DIR.mkdir(parents=True, exist_ok=True)
_NEWS_VIDEO_DIR.mkdir(parents=True, exist_ok=True)


def _news_render_ctx(video_id: str, current_step: int) -> dict:
    from pipeline import news_meta as _news_meta
    proj = _state.get_video(video_id)
    data = _news_meta.get_news_data(video_id)
    reached = max(data.get("current_step", 1), current_step)
    return {
        "video_id": video_id,
        "news_title": (proj.title if proj else None),
        "news_data": data,
        "current_step": current_step,
        "reached_step": reached,
        # 热点工坊传过来的完整立意 + 找补 + 信号（模板顶部 banner 用）
        "forge_brief": data.get("forge_brief"),
        "forge_supplements": data.get("forge_supplements"),
        "forge_signal": data.get("forge_signal"),
    }


@app.get("/project/{video_id}/news", response_class=HTMLResponse)
async def project_news_workflow(video_id: str):
    proj = _state.get_video(video_id)
    if not proj:
        return RedirectResponse(url="/", status_code=303)
    from pipeline import news_meta as _news_meta
    data = _news_meta.get_news_data(video_id)
    slug = _news_meta.step_path(video_id, data.get("current_step", 1))
    return RedirectResponse(url=f"/project/{video_id}/news/{slug}", status_code=303)


@app.get("/project/{video_id}/news/topic", response_class=HTMLResponse)
async def project_news_topic_page(request: Request, video_id: str):
    return TEMPLATES.TemplateResponse(
        "step_news_topic.html",
        {"request": request, **_news_render_ctx(video_id, 1)},
    )


@app.get("/project/{video_id}/news/script", response_class=HTMLResponse)
async def project_news_script_page(request: Request, video_id: str):
    return TEMPLATES.TemplateResponse(
        "step_news_script.html",
        {"request": request, **_news_render_ctx(video_id, 2)},
    )


@app.get("/project/{video_id}/news/presenter", response_class=HTMLResponse)
async def project_news_presenter_page(request: Request, video_id: str):
    from pipeline import shot_template_library as _stl
    return TEMPLATES.TemplateResponse(
        "step_news_presenter.html",
        {"request": request, "series_list": _stl.list_series(), **_news_render_ctx(video_id, 3)},
    )


@app.get("/project/{video_id}/news/audio", response_class=HTMLResponse)
async def project_news_audio_page(request: Request, video_id: str):
    return TEMPLATES.TemplateResponse(
        "step_news_audio.html",
        {"request": request, "voices": _edge_tts.list_voices(), **_news_render_ctx(video_id, 4)},
    )


@app.get("/project/{video_id}/news/video", response_class=HTMLResponse)
async def project_news_video_page(request: Request, video_id: str):
    return TEMPLATES.TemplateResponse(
        "step_news_video.html",
        {"request": request, **_news_render_ctx(video_id, 5)},
    )


@app.get("/project/{video_id}/news/export", response_class=HTMLResponse)
async def project_news_export_page(request: Request, video_id: str):
    return TEMPLATES.TemplateResponse(
        "step_news_export.html",
        {"request": request, **_news_render_ctx(video_id, 6)},
    )


# ---------- API ----------

@app.post("/api/news/{video_id}/generate-topics")
async def api_news_generate_topics(video_id: str, payload: dict):
    """真调 LLM 出 6 条候选选题。"""
    from pipeline import news_meta as _news_meta
    keywords = str(payload.get("keywords", "")).strip()
    if not keywords:
        return JSONResponse({"ok": False, "error": "关键词不能为空"})
    intent = str(payload.get("intent", "搞懂概念"))
    audience = str(payload.get("audience", "完全小白"))
    duration_sec = int(payload.get("duration_sec", 60))
    mode = str(payload.get("mode", "single"))

    # 用 news 专属 topic 生成器（不绑定金融领域）
    from pipeline.agents import news_script_writer as _nsw
    res = _nsw.generate_topics(
        keywords=keywords, intent=intent, audience=audience,
        duration_sec=duration_sec, n_topics=6,
    )
    if not res.get("ok"):
        return JSONResponse({"ok": False, "error": res.get("error", "生成失败")})

    topics = res.get("topics", [])
    _news_meta.update_news_data(
        video_id,
        mode=mode,
        topic_keywords=keywords,
        topic_intent=intent,
        topic_audience=audience,
        duration_sec=duration_sec,
        topic_pool=topics,
    )
    return JSONResponse({
        "ok": True,
        "topics": topics,
        "provider": res.get("provider"),
        "cost": res.get("cost", 0.0),
    })


@app.post("/api/news/{video_id}/pick-topic")
async def api_news_pick_topic(video_id: str, payload: dict):
    from pipeline import news_meta as _news_meta
    topic = payload.get("topic")
    if not topic:
        return JSONResponse({"ok": False, "error": "topic 缺失"})
    _news_meta.update_news_data(
        video_id,
        topic_picked=topic,
        current_step=max(_news_meta.get_news_data(video_id).get("current_step", 1), 2),
    )
    return JSONResponse({"ok": True})


@app.post("/api/news/{video_id}/generate-script")
async def api_news_generate_script(video_id: str):
    """根据已锁定选题真调 LLM 生成口播文案。"""
    from pipeline import news_meta as _news_meta
    from pipeline.agents import news_script_writer as _nsw
    data = _news_meta.get_news_data(video_id)
    picked = data.get("topic_picked")
    if not picked:
        return JSONResponse({"ok": False, "error": "还没选定题目，请回上一步"})
    res = _nsw.generate_news_script(
        topic=picked.get("title", ""),
        audience=data.get("topic_audience", "完全小白"),
        duration_sec=int(data.get("duration_sec", 60)),
        mode=data.get("mode", "single"),
    )
    if not res.get("ok"):
        return JSONResponse({"ok": False, "error": res.get("error", "生成失败")})
    # 自动切 units（用户可在 UI 再编辑）
    from pipeline.agents.news_script_segmenter import segment_script as _seg
    units = _seg(res["script"]) if res.get("script") else []
    _news_meta.update_news_data(
        video_id,
        script_title=res["title"],
        script_text=res["script"],
        script_char_count=res["char_count"],
        script_units=units,
    )
    return JSONResponse({
        "ok": True,
        "title": res["title"],
        "script": res["script"],
        "char_count": res["char_count"],
        "script_units": units,
        "provider": res.get("provider"),
        "cost": res.get("cost", 0.0),
    })


@app.post("/api/news/{video_id}/save-script")
async def api_news_save_script(video_id: str, payload: dict):
    """保存文案 + 自动重切字幕单元（如果客户端没显式传 units）。"""
    from pipeline import news_meta as _news_meta
    from pipeline.agents.news_script_segmenter import segment_script as _seg
    title = str(payload.get("title", "")).strip()
    script = str(payload.get("script", "")).strip()
    if not script:
        return JSONResponse({"ok": False, "error": "文案不能为空"})
    plain = "".join(c for c in script if not c.isspace())
    # 客户端可以传 units 覆盖（用户手动编辑过），否则按 script 自动切
    units_in = payload.get("units")
    if isinstance(units_in, list) and units_in:
        units = [str(u).strip() for u in units_in if str(u).strip()]
    else:
        units = _seg(script)
    _news_meta.update_news_data(
        video_id,
        script_title=title,
        script_text=script,
        script_char_count=len(plain),
        script_units=units,
        current_step=max(_news_meta.get_news_data(video_id).get("current_step", 2), 3),
    )
    return JSONResponse({"ok": True, "script_units": units})


@app.post("/api/news/{video_id}/segment-script")
async def api_news_segment_script(video_id: str):
    """对当前 script_text 跑 B2 自动切分，返回新 units（不立即保存，等用户审核后通过 save-script-units 持久化）。"""
    from pipeline import news_meta as _news_meta
    from pipeline.agents.news_script_segmenter import segment_script as _seg
    data = _news_meta.get_news_data(video_id)
    script = (data.get("script_text") or "").strip()
    if not script:
        return JSONResponse({"ok": False, "error": "还没有文案"})
    units = _seg(script)
    return JSONResponse({"ok": True, "script_units": units})


@app.post("/api/news/{video_id}/save-script-units")
async def api_news_save_script_units(video_id: str, payload: dict):
    """保存用户编辑过的字幕单元 list[str]。"""
    from pipeline import news_meta as _news_meta
    units_in = payload.get("units")
    if not isinstance(units_in, list):
        return JSONResponse({"ok": False, "error": "units 必须是列表"})
    units = [str(u).strip() for u in units_in if str(u).strip()]
    _news_meta.update_news_data(video_id, script_units=units)
    return JSONResponse({"ok": True, "script_units": units, "count": len(units)})


@app.post("/api/news/{video_id}/pick-shot-template")
async def api_news_pick_shot_template(video_id: str, payload: dict):
    from pipeline import news_meta as _news_meta
    sid = str(payload.get("series_id", "")).strip()
    if not sid:
        return JSONResponse({"ok": False, "error": "series_id 缺失"})
    _news_meta.update_news_data(
        video_id,
        shot_template_id=sid,
        current_step=max(_news_meta.get_news_data(video_id).get("current_step", 3), 4),
    )
    return JSONResponse({"ok": True})


@app.get("/api/news/voice_presets")
async def api_news_voice_presets():
    """news 管线的音色注册表（含 Edge TTS + Fish Audio 角色音色 + 字幕样式 + 栏目条）。
    chyron_styles 中带 hidden:true 的条目（老引擎 pil_overlay / ffmpeg_drawbox）不暴露给 UI。"""
    p = Path(__file__).resolve().parent.parent / "config" / "news_voice_presets.json"
    if not p.exists():
        return JSONResponse({"providers": [], "subtitle_styles": [], "chyron_styles": []})
    data = _json.loads(p.read_text(encoding="utf-8"))
    data["chyron_styles"] = [
        s for s in data.get("chyron_styles", []) if not s.get("hidden")
    ]
    return JSONResponse(data)


@app.get("/api/news/bgm_library")
async def api_news_bgm_library():
    """扫 ./口播bgm/ 下所有 mp3/wav/m4a，返回可选 BGM 列表（含"不加 BGM"占位）。
    增删 BGM 文件刷新即生效，无需重启。"""
    items: list[dict] = [
        {"id": "", "name": "不加 BGM", "desc": "干声 · 无背景音乐", "url": "", "filename": ""},
    ]
    if _NEWS_BGM_DIR.exists():
        exts = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}
        for f in sorted(_NEWS_BGM_DIR.iterdir()):
            if f.is_file() and f.suffix.lower() in exts:
                items.append({
                    "id": f.name,                    # 用文件名作 id（含扩展名）
                    "name": f.stem,                  # 显示名（去扩展名）
                    "desc": f"{f.suffix.lstrip('.').upper()} · {f.stat().st_size // 1024} KB",
                    "url": f"/news-bgm/{quote(f.name)}",
                    "filename": f.name,
                })
    return JSONResponse({"items": items})


@app.post("/api/news/{video_id}/save-bible")
async def api_news_save_bible(video_id: str, payload: dict):
    """保存 step 4 成片要素（声音 + 字幕 + 新闻栏目条 + BGM），不立刻合成配音。"""
    from pipeline import news_meta as _news_meta
    bgm_id_raw = payload.get("bgm_id")
    bgm_id = str(bgm_id_raw).strip() if bgm_id_raw else None
    if not bgm_id:
        bgm_id = None
    try:
        bgm_vol = float(payload.get("bgm_volume_db", -16.0))
    except (TypeError, ValueError):
        bgm_vol = -16.0
    bgm_vol = max(-40.0, min(0.0, bgm_vol))  # clamp [-40, 0] dB
    _news_meta.update_news_data(
        video_id,
        voice_provider=str(payload.get("voice_provider", "edge_tts")),
        voice_id=str(payload.get("voice_id", "")).strip(),
        subtitle_burn=bool(payload.get("subtitle_burn", True)),
        subtitle_style=str(payload.get("subtitle_style", "classic_white")),
        chyron_enabled=bool(payload.get("chyron_enabled", False)),
        chyron_style=str(payload.get("chyron_style", "znn_intl")),
        chyron_label=str(payload.get("chyron_label", "DEVELOPING")).strip()[:14],
        chyron_title=str(payload.get("chyron_title", "")).strip()[:36],
        chyron_title_sub=str(payload.get("chyron_title_sub", "")).strip()[:24],
        chyron_station=str(payload.get("chyron_station", "ZNN")).strip()[:6],
        chyron_time_str=str(payload.get("chyron_time_str", "")).strip()[:10],
        bgm_id=bgm_id,
        bgm_volume_db=bgm_vol,
    )
    return JSONResponse({"ok": True})


@app.post("/api/news/{video_id}/generate-audio")
async def api_news_generate_audio(video_id: str, payload: dict):
    """合成配音 · 支持 edge_tts / fish_audio 双 provider。

    payload: { voice_provider, voice_id, subtitle_burn?, subtitle_style? }
    """
    from pipeline import news_meta as _news_meta
    data = _news_meta.get_news_data(video_id)
    script = data.get("script_text", "").strip()
    if not script:
        return JSONResponse({"ok": False, "error": "没有文案，请回上一步"})

    provider = str(payload.get("voice_provider", data.get("voice_provider", "edge_tts")))
    voice_id = str(payload.get("voice_id", data.get("voice_id", ""))).strip()
    sub_burn = bool(payload.get("subtitle_burn", data.get("subtitle_burn", True)))
    sub_style = str(payload.get("subtitle_style", data.get("subtitle_style", "classic_white")))

    # TTS 输入构造：优先用 script_units（每个 unit 末尾 "。" 喂 → TTS 在每个 unit 边界真停顿，字幕能 1:1 对齐）
    # 兼容老项目（无 units）→ 走原始 script 拆 A:/B: 前缀
    units = data.get("script_units") or []
    if units:
        from pipeline.agents.news_script_segmenter import tts_text as _tts_text
        plain = _tts_text(units)
    else:
        plain = "\n".join(
            line.split(":", 1)[-1].strip() if ":" in line else line
            for line in script.split("\n")
        )
    out_dir = _NEWS_AUDIO_DIR / video_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "voice.mp3"

    if provider == "fish_audio":
        if not voice_id:
            return JSONResponse({"ok": False, "error": "Fish Audio 必须选一个角色音色"})
        r = await _fish_audio.synthesize_async(plain, voice_id, out_path)
    else:  # edge_tts
        if not voice_id or not _edge_tts.voice_exists(voice_id):
            voice_id = "zh-CN-XiaoxiaoNeural"
        r = await _edge_tts.synthesize_async(plain, voice_id, out_path)

    if not r.get("ok"):
        return JSONResponse({"ok": False, "error": r.get("error", "TTS 合成失败")})

    from pipeline.tools import ffmpeg_client as _ffm
    dur = _ffm.probe_duration(str(out_path)) or 0.0
    rel_path = str(out_path.relative_to(_DATA_DIR.parent))
    _news_meta.update_news_data(
        video_id,
        voice_provider=provider,
        voice_id=voice_id,
        subtitle_burn=sub_burn,
        subtitle_style=sub_style,
        audio_path=rel_path,
        audio_duration_sec=round(dur, 2),
        current_step=max(_news_meta.get_news_data(video_id).get("current_step", 4), 5),
    )
    return JSONResponse({
        "ok": True,
        "audio_url": "/" + rel_path,
        "duration": round(dur, 2),
        "voice_provider": provider,
        "voice_id": voice_id,
    })


@app.post("/api/news/preview-voice")
async def api_news_preview_voice(payload: dict):
    """声音试听 · Edge TTS 现场合成短 demo；Fish Audio 直接返回已存样本 URL。"""
    provider = str(payload.get("voice_provider", "edge_tts"))
    voice_id = str(payload.get("voice_id", "")).strip()
    if not voice_id:
        return JSONResponse({"ok": False, "error": "voice_id 缺失"})

    if provider == "fish_audio":
        # 从注册表查 sample_url
        p = Path(__file__).resolve().parent.parent / "config" / "news_voice_presets.json"
        presets = _json.loads(p.read_text(encoding="utf-8"))
        for prov in presets.get("providers", []):
            if prov.get("id") == "fish_audio":
                for v in prov.get("voices", []):
                    if v.get("id") == voice_id:
                        return JSONResponse({"ok": True, "audio_url": v.get("sample_url", "")})
        return JSONResponse({"ok": False, "error": "Fish 音色未注册或样本缺失"})

    # Edge TTS · 用一句固定短 demo 合成（缓存）
    demo_text = "你好，这是我的声音。我用这条样本帮你判断要不要选我。"
    demo_dir = _DATA_DIR / "voice_samples" / "edge_tts"
    demo_dir.mkdir(parents=True, exist_ok=True)
    demo_path = demo_dir / f"{voice_id}.mp3"
    if not demo_path.exists():
        r = await _edge_tts.synthesize_async(demo_text, voice_id, demo_path)
        if not r.get("ok"):
            return JSONResponse({"ok": False, "error": r.get("error", "demo 合成失败")})
    rel = str(demo_path.relative_to(_DATA_DIR.parent))
    return JSONResponse({"ok": True, "audio_url": "/" + rel})


@app.post("/api/news/{video_id}/assemble-video")
async def api_news_assemble_video(video_id: str, payload: dict):
    """ffmpeg 按配音时长循环切换镜头模板，输出最终 mp4。"""
    from pipeline import news_meta as _news_meta
    from pipeline import shot_template_library as _stl
    from pipeline.agents import news_video_assembler as _nva
    data = _news_meta.get_news_data(video_id)
    sid = data.get("shot_template_id")
    audio_rel = data.get("audio_path")
    if not sid or not audio_rel:
        return JSONResponse({"ok": False, "error": "缺少镜头模板或配音"})
    takes = _stl.get_take_paths(sid)
    framings = _stl.get_take_framings(sid)
    takes_meta = _stl.get_takes_meta(sid)
    if not takes:
        return JSONResponse({"ok": False, "error": f"系列 {sid} 没有 take 视频"})
    audio_path = _DATA_DIR.parent / audio_rel
    if not audio_path.exists():
        return JSONResponse({"ok": False, "error": f"配音文件丢失: {audio_rel}"})
    out_dir = _NEWS_VIDEO_DIR / video_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "final.mp4"
    switch_every = float(payload.get("switch_every", data.get("switch_every", 6.0)))

    # 字幕样式 + chyron profile：从注册表查
    sub_burn = bool(data.get("subtitle_burn", False))
    sub_style_id = str(data.get("subtitle_style", "classic_white"))
    sub_ass = ""
    chy_enabled = bool(data.get("chyron_enabled", False))
    chy_style_id = str(data.get("chyron_style", "znn_intl"))
    chy_profile = None
    try:
        _vp_path = Path(__file__).resolve().parent.parent / "config" / "news_voice_presets.json"
        _vp = _json.loads(_vp_path.read_text(encoding="utf-8"))
        if sub_burn:
            for s in _vp.get("subtitle_styles", []):
                if s.get("id") == sub_style_id:
                    sub_ass = s.get("ass_style", "")
                    break
        if chy_enabled:
            for s in _vp.get("chyron_styles", []):
                if s.get("id") == chy_style_id:
                    chy_profile = s
                    break
    except Exception:
        pass

    # chyron 标题：用户填的优先，否则取 script_title
    chy_title = str(data.get("chyron_title") or data.get("script_title") or "").strip()
    chy_label = str(data.get("chyron_label") or "DEVELOPING").strip()
    chy_title_sub = str(data.get("chyron_title_sub") or data.get("script_title") or "").strip()
    chy_station = str(data.get("chyron_station") or "ZNN").strip()
    chy_time = str(data.get("chyron_time_str") or "").strip()
    # chyron ticker：用户填的优先，否则取文案首句作底部滚动文字
    chy_ticker = str(data.get("chyron_ticker") or "").strip()
    if not chy_ticker:
        script_text = str(data.get("script_text") or "").strip()
        if script_text:
            # 取首句（到第一个 。！？ 为止）作为 ticker
            import re as _re
            m = _re.search(r"[^。！？!?]{8,40}[。！？!?]", script_text)
            if m:
                chy_ticker = m.group(0).strip()
            else:
                chy_ticker = script_text[:40]
    chy_live = bool(data.get("chyron_live_dot", True))

    # BGM 解析：bgm_id 是 ./口播bgm/ 下的文件名，None 表示不加 BGM
    bgm_file_id = data.get("bgm_id") or None
    bgm_path = None
    if bgm_file_id:
        candidate = _NEWS_BGM_DIR / bgm_file_id
        if candidate.exists() and candidate.is_file():
            bgm_path = candidate
    try:
        bgm_vol_db = float(data.get("bgm_volume_db", -16.0))
    except (TypeError, ValueError):
        bgm_vol_db = -16.0

    res = _nva.assemble(
        takes, audio_path, out_path,
        switch_every=switch_every,
        script=data.get("script_text", ""),
        framings=framings,
        takes_meta=takes_meta,
        subtitle_burn=sub_burn,
        subtitle_ass_style=sub_ass,
        chyron_enabled=chy_enabled,
        chyron_profile=chy_profile,
        chyron_label=chy_label,
        chyron_title=chy_title,
        chyron_ticker=chy_ticker,
        chyron_live_dot=chy_live,
        chyron_title_sub=chy_title_sub,
        chyron_station=chy_station,
        chyron_time_str=chy_time,
        bgm_path=bgm_path,
        bgm_volume_db=bgm_vol_db,
        script_units=(data.get("script_units") or None),
    )
    if not res.get("ok"):
        return JSONResponse({"ok": False, "error": res.get("error", "合成失败")})
    rel_path = str(out_path.relative_to(_DATA_DIR.parent))
    _news_meta.update_news_data(
        video_id,
        final_video_path=rel_path,
        switch_every=switch_every,
        current_step=max(_news_meta.get_news_data(video_id).get("current_step", 5), 6),
    )
    return JSONResponse({
        "ok": True,
        "video_url": "/" + rel_path,
        "duration_sec": res.get("duration_sec", 0),
        "segments": res.get("segments", 0),
        "size": res.get("size", "?"),
        "mode": res.get("mode"),
        "plan": res.get("plan", []),
        "subtitle_burned": res.get("subtitle_burned", False),
        "subtitle_segments": res.get("subtitle_segments", 0),
        "burn_warning": res.get("burn_warning"),
        "chyron_applied": res.get("chyron_applied", False),
        "chyron_warning": res.get("chyron_warning"),
    })


@app.get("/api/shot_templates")
async def api_shot_templates():
    from pipeline import shot_template_library as _stl
    return JSONResponse({"series": _stl.list_series()})


@app.post("/api/news/{video_id}/run-lipsync")
async def api_news_run_lipsync(video_id: str):
    """同步阻塞跑 VideoRetalk lipsync（OSS 上传 → 提交 → 轮询 → 下载）。
    成功返回 {ok, lipsync_video_path, task_id, elapsed_sec}.
    """
    from pipeline.agents import news_lipsync_agent as _nla
    res = _nla.run_lipsync(video_id, timeout_sec=600)
    return JSONResponse(res)


@app.get("/project/{video_id}/outhan", response_class=HTMLResponse)
async def project_outhan_workflow(video_id: str):
    """Outhan 流程根入口——重定向到当前步骤。"""
    proj = _state.get_video(video_id)
    if not proj:
        return RedirectResponse(url="/", status_code=303)
    from pipeline import outhan_meta as _outhan_meta
    data = _outhan_meta.get_outhan_data(video_id)
    slug = _outhan_meta.step_path(video_id, data["current_step"])
    return RedirectResponse(url=f"/project/{video_id}/outhan/{slug}", status_code=303)


# ==================== Outhan 步骤 1：选题 ====================

# [AI-NOTE] 2026-05-08: Outhan 步骤 1 头脑风暴页签的题材库 = Outhan 自家 5 domain（短视频形态）
# + 同事金融知识库 1 domain（schema 转换后）。两套合并，前端只调一个 endpoint。
_OUTHAN_TOPIC_LIB_PATH = Path(__file__).resolve().parents[1] / "config" / "outhan_topic_library.json"
_YIERBUBU_TOPIC_LIB_PATH = Path(__file__).resolve().parents[1] / "config" / "topic_library.json"
_OUTHAN_TOPIC_LIB_CACHE: dict | None = None


def _convert_yierbubu_topic_to_outhan(t: dict) -> dict:
    """[AI-NOTE] 2026-05-08: 同事金融题材 → Outhan schema。
    字段映射：
      - title / hook / level / angles：直接复用
      - intent / audience / keywords → 拼进 theme_full（让台本生成器读到完整上下文）
      - kind：金融科普全是讲解型 → 默认 dialogue
      - _source: 'yierbubu' 让前端显示来源徽章
    """
    intent_hint = {
        "搞懂概念": "用通俗类比把概念讲清楚",
        "探讨影响": "讨论这件事如何影响普通人的生活",
        "学会操作": "给出可操作的步骤建议",
        "引发共鸣": "戳中观众生活里的真实情绪",
    }.get(t.get("intent", ""), "")
    audience_hint = {
        "完全小白": "面向零基础观众，多用类比少术语",
        "有点基础": "面向有点基础的观众，可以引入一些专业概念",
        "进阶用户": "面向进阶用户，可深入技术细节",
    }.get(t.get("audience", ""), "")

    parts: list[str] = []
    if t.get("hook"):
        parts.append(t["hook"])
    if intent_hint:
        parts.append(intent_hint)
    if audience_hint:
        parts.append(audience_hint)
    if t.get("keywords"):
        parts.append(f"关键词：{t['keywords']}")
    theme_full = "。".join(parts)

    return {
        "title": t.get("title", ""),
        "hook": t.get("hook", ""),
        "level": t.get("level", "人人能懂"),
        "kind": "dialogue",  # 金融科普 ≈ 讲解型
        "angles": t.get("angles", []),
        "theme_full": theme_full,
        "_source": "yierbubu",
    }


def _convert_yierbubu_domain_to_outhan(d: dict) -> dict:
    """同事 domain schema 转 Outhan：domain 级补 emoji，每个 concept 的 topics 转字段。"""
    converted_concepts = []
    for c in d.get("concepts", []):
        converted_concepts.append({
            "id": c.get("id"),
            "name": c.get("name"),
            "emoji": c.get("emoji") or "📊",
            "one_liner": c.get("one_liner", ""),
            "scenes": c.get("scenes", []),
            "topics": [_convert_yierbubu_topic_to_outhan(t) for t in (c.get("topics") or [])],
        })
    return {
        "id": d.get("id", "yierbubu_finance"),
        "name": d.get("name") or "金融知识",
        "emoji": "💼",  # 同事金融库统一标识
        "active": True,
        "concepts": converted_concepts,
    }


def _load_combined_topic_library() -> dict:
    """合并 Outhan 自家库 + 同事金融库，做一份缓存。任意一份失败仅记录警告，不影响另一份。"""
    import json as _json
    domains: list[dict] = []
    # 1. Outhan 自家
    try:
        outhan_data = _json.loads(_OUTHAN_TOPIC_LIB_PATH.read_text(encoding="utf-8"))
        # Outhan 自家 topic 加 _source 标记
        for d in outhan_data.get("domains", []):
            for c in d.get("concepts", []):
                for t in c.get("topics", []):
                    t.setdefault("_source", "outhan")
            domains.append(d)
    except Exception as e:
        print(f"[outhan] load outhan_topic_library failed: {e}")
    # 2. 同事金融
    try:
        yierbubu_data = _json.loads(_YIERBUBU_TOPIC_LIB_PATH.read_text(encoding="utf-8"))
        for d in yierbubu_data.get("domains", []):
            if d.get("active") is False:
                continue
            domains.append(_convert_yierbubu_domain_to_outhan(d))
    except Exception as e:
        print(f"[outhan] load yierbubu topic_library failed: {e}")
    return {
        "version": 2,
        "domains": domains,
    }


@app.get("/api/outhan/topic-library")
async def api_outhan_topic_library():
    global _OUTHAN_TOPIC_LIB_CACHE
    if _OUTHAN_TOPIC_LIB_CACHE is None:
        try:
            _OUTHAN_TOPIC_LIB_CACHE = _load_combined_topic_library()
        except Exception as e:
            return JSONResponse({"error": f"加载题材库失败：{e}"}, status_code=500)
    return JSONResponse(_OUTHAN_TOPIC_LIB_CACHE)


@app.get("/project/{video_id}/outhan/topic", response_class=HTMLResponse)
async def project_outhan_topic(request: Request, video_id: str):
    """[AI-NOTE] 2026-05-08: 步骤 1 = 选题 + 首帧（合并）"""
    proj = _state.get_video(video_id)
    if not proj:
        return RedirectResponse(url="/", status_code=303)
    from pipeline import outhan_meta as _outhan_meta
    from pipeline import outhan_storage as _outhan_storage
    data = _outhan_meta.get_outhan_data(video_id)
    refs = []
    for a in _outhan_storage.list_assets(video_id, "reference"):
        meta = a.get("metadata") or {}
        refs.append({
            "id": a["id"],
            "url": f"/api/outhan/{video_id}/asset/{a['id']}",
            "source": meta.get("source") or "upload",
            "role": meta.get("role") or "content",  # [AI-NOTE] 默认 content（兼容老数据）
        })
    candidates = []
    for a in _outhan_storage.list_assets(video_id, "first_frame"):
        meta = a.get("metadata") or {}
        candidates.append({
            "id": a["id"],
            "url": f"/api/outhan/{video_id}/asset/{a['id']}",
            "is_current": a["id"] == data.get("first_frame_asset_id"),
            "source": meta.get("source") or "ai",  # upload | ai
            "ts": __import__("datetime").datetime.fromtimestamp(a["created_at"]).strftime("%Y-%m-%d %H:%M"),
        })
    # 最新的排前面，方便用户看到刚生成/上传的图
    candidates.sort(key=lambda c: c["ts"], reverse=True)
    # 热点工坊立意（如果是从工坊来）
    _sources = _state.load_project_sources(video_id) or {}
    _fb = _sources.get("forge_brief") if isinstance(_sources, dict) else None
    _fs = _sources.get("forge_supplements") if isinstance(_sources, dict) else None
    _fg = _sources.get("forge_signal") if isinstance(_sources, dict) else None
    return TEMPLATES.TemplateResponse(
        "step_outhan_topic.html",
        {
            "request": request,
            "video_id": video_id,
            "outhan_title": proj.title,
            "outhan_data": data,
            "refs": refs,
            "candidates": candidates,
            "current_step": 1,
            "reached_step": data["current_step"],
            "forge_brief": _fb,
            "forge_supplements": _fs,
            "forge_signal": _fg,
        },
    )


@app.get("/project/{video_id}/outhan/first-frame")
async def project_outhan_first_frame_legacy(video_id: str):
    """[AI-NOTE] 2026-05-08: 旧 6 步链接重定向到合并后的步骤 1（topic 页含首帧）。"""
    return RedirectResponse(url=f"/project/{video_id}/outhan/topic", status_code=303)


# [AI-NOTE] 2026-05-08: Fish Audio reference_id 只接 hex / 短 slug。校验防 SSRF / header injection。
import re as _re_outhan
_OUTHAN_VOICE_ID_RE = _re_outhan.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def _outhan_strip_md_fence(content: str) -> str:
    """[AI-NOTE] 2026-05-08: 健壮地剥 ```json ... ``` 围栏。
    支持 Gemini 偶尔单行返回 ```json[...]``` 的情况（之前用 split("\\n",1) 会 IndexError）。
    """
    text = (content or "").strip()
    if text.startswith("```"):
        text = text[3:]
        # 可能紧跟 lang 标签：json / javascript / py 等
        for lang in ("json", "javascript", "js", "python", "py"):
            if text.lower().startswith(lang):
                text = text[len(lang):]
                break
        text = text.lstrip("\n").lstrip()
        # 剥尾部 ```
        if "```" in text:
            text = text.rsplit("```", 1)[0]
        text = text.strip()
    return text


def _outhan_validate_voice_id(v: str) -> tuple[bool, str]:
    """返回 (ok, normalized_or_err). 空字符串视为合法（=用 S1 默认）。"""
    v = (v or "").strip()
    if not v:
        return True, ""
    if not _OUTHAN_VOICE_ID_RE.match(v):
        return False, "voice_id 格式非法（只允许 1-64 位字母/数字/-/_）"
    return True, v


@app.post("/api/outhan/{video_id}/save-topic")
async def api_outhan_save_topic(video_id: str, request: Request):
    """[AI-NOTE] 2026-05-08: 仅更新 body 中显式给出的字段；voice_id 在 step 4 单独维护，
    step 1 autoSave 不应清掉它，所以严格按 key in body 判断。"""
    proj = _state.get_video(video_id)
    if not proj:
        return JSONResponse({"error": "项目不存在"}, status_code=404)
    body = await request.json()
    title = (body.get("title") or "").strip()
    if not title:
        return JSONResponse({"error": "标题必填"}, status_code=400)
    from pipeline import outhan_meta as _outhan_meta
    _state.update_video_fields(video_id, title=title)

    fields: dict[str, object] = {}
    if "theme" in body:
        fields["theme"] = (body.get("theme") or "").strip()
    if "target_duration" in body:
        try:
            fields["target_duration"] = int(body.get("target_duration") or 60)
        except (TypeError, ValueError):
            return JSONResponse({"error": "目标时长必须是数字"}, status_code=400)
    if "video_engine" in body:
        eng = (body.get("video_engine") or "jimeng").strip()
        if eng not in ("jimeng", "vidu"):
            eng = "jimeng"
        fields["video_engine"] = eng
    if "video_kind" in body:
        kind = (body.get("video_kind") or "dialogue").strip()
        if kind not in ("dialogue", "silent"):
            kind = "dialogue"
        fields["video_kind"] = kind
    if "voice_id" in body:
        ok, v = _outhan_validate_voice_id(str(body.get("voice_id") or ""))
        if not ok:
            return JSONResponse({"error": v}, status_code=400)
        fields["voice_id"] = v
    fields["current_step"] = max(_outhan_meta.get_outhan_data(video_id)["current_step"], 2)
    _outhan_meta.update_outhan_data(video_id, **fields)
    return JSONResponse({"ok": True})


@app.post("/api/outhan/{video_id}/save-voice")
async def api_outhan_save_voice(video_id: str, request: Request):
    """[AI-NOTE] 2026-05-08: 项目默认音色专用路由（避免 step 4 保存音色顺手覆盖 step 1 字段）。"""
    proj = _state.get_video(video_id)
    if not proj:
        return JSONResponse({"error": "项目不存在"}, status_code=404)
    body = await request.json()
    ok, v = _outhan_validate_voice_id(str(body.get("voice_id") or ""))
    if not ok:
        return JSONResponse({"error": v}, status_code=400)
    from pipeline import outhan_meta as _outhan_meta
    _outhan_meta.update_outhan_data(video_id, voice_id=v)
    return JSONResponse({"ok": True, "voice_id": v})


# [AI-NOTE] 2026-05-08: 图片上传统一限制：mime 白名单 + 10MB 上限。
# 之前完全无校验，攻击面是任何文件都能落盘。
_OUTHAN_IMG_MIME = {"image/png", "image/jpeg", "image/webp"}
_OUTHAN_IMG_EXT_BY_MIME = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
_OUTHAN_IMG_MAX_BYTES = 10 * 1024 * 1024  # 10MB


async def _outhan_read_image_upload(request: Request) -> tuple[bytes, str, str] | JSONResponse:
    """共用上传校验：mime 白名单 + size 上限。返回 (data, ext, original_name)。"""
    form = await request.form()
    file = form.get("file")
    if file is None or not hasattr(file, "read"):
        return JSONResponse({"error": "缺 file"}, status_code=400)
    mime = (getattr(file, "content_type", "") or "").lower()
    if mime not in _OUTHAN_IMG_MIME:
        return JSONResponse(
            {"error": f"只支持 png/jpeg/webp 图片，收到 {mime or '(unknown)'}"},
            status_code=400,
        )
    data = await file.read()
    if len(data) > _OUTHAN_IMG_MAX_BYTES:
        return JSONResponse(
            {"error": f"图片过大（{len(data)//1024} KB），上限 {_OUTHAN_IMG_MAX_BYTES//1024} KB"},
            status_code=400,
        )
    if not data:
        return JSONResponse({"error": "图片为空"}, status_code=400)
    ext = _OUTHAN_IMG_EXT_BY_MIME[mime]
    return data, ext, (file.filename or "upload")


@app.post("/api/outhan/{video_id}/upload-reference")
async def api_outhan_upload_reference(video_id: str, request: Request):
    proj = _state.get_video(video_id)
    if not proj:
        return JSONResponse({"error": "项目不存在"}, status_code=404)
    parsed = await _outhan_read_image_upload(request)
    if isinstance(parsed, JSONResponse):
        return parsed
    data, ext, original_name = parsed
    from pipeline import outhan_storage as _outhan_storage
    saved = _outhan_storage.save_asset(
        video_id,
        "reference",
        data,
        ext=ext,
        metadata={"original_name": original_name, "size_bytes": len(data)},
    )
    return JSONResponse({"id": saved["id"], "url": saved["url"]})


# [AI-NOTE] 2026-05-08: 用户上传图片直接当首帧候选（不走 Seedream 生成）。
# 落 type=first_frame，与 generate-first-frame 落同一种资产，自动进 candidates。
@app.post("/api/outhan/{video_id}/upload-first-frame")
async def api_outhan_upload_first_frame(video_id: str, request: Request):
    proj = _state.get_video(video_id)
    if not proj:
        return JSONResponse({"error": "项目不存在"}, status_code=404)
    parsed = await _outhan_read_image_upload(request)
    if isinstance(parsed, JSONResponse):
        return parsed
    data, ext, original_name = parsed
    from pipeline import outhan_storage as _outhan_storage
    from pipeline import outhan_meta as _outhan_meta
    saved = _outhan_storage.save_asset(
        video_id,
        "first_frame",
        data,
        ext=ext,
        base_name=f"upload-{int(__import__('time').time())}",
        metadata={"original_name": original_name, "size_bytes": len(data), "source": "upload"},
    )
    # 上传即设为当前首帧（用户主动上传的，意图明确）
    _outhan_meta.update_outhan_data(video_id, first_frame_asset_id=saved["id"])
    return JSONResponse({"id": saved["id"], "url": saved["url"], "set_as_current": True})


@app.delete("/api/outhan/{video_id}/asset/{asset_id}")
async def api_outhan_asset_delete(video_id: str, asset_id: str):
    from pipeline import outhan_storage as _outhan_storage
    asset = _outhan_storage.get_asset(video_id, asset_id)
    if not asset:
        return JSONResponse({"error": "asset 不存在"}, status_code=404)
    # 从 meta 列表里移除（文件本身懒删——保留以备后续可能引用）
    from pipeline import state as _st
    rec = _st.get_video(video_id)
    if rec is None:
        return JSONResponse({"error": "项目不存在"}, status_code=404)
    meta = dict(rec.meta) if rec.meta else {}
    meta["outhan_assets"] = [a for a in meta.get("outhan_assets", []) if a.get("id") != asset_id]
    import json as _json
    import time as _time
    with _st.get_conn() as conn:
        conn.execute(
            "UPDATE videos SET meta_json = ?, updated_at = ? WHERE video_id = ?",
            (_json.dumps(meta, ensure_ascii=False), _time.time(), video_id),
        )
    # 顺手删文件
    try:
        from pathlib import Path as _Path
        _Path(asset["path"]).unlink(missing_ok=True)
    except Exception:
        pass
    return JSONResponse({"ok": True})


# [AI-NOTE] 2026-05-08: 参考图角色 PATCH——content/texture/style/palette/composition
# 改的是 outhan_assets[i].metadata.role；不影响图本身或 first_frame 关系
@app.patch("/api/outhan/{video_id}/reference/{asset_id}/role")
async def api_outhan_reference_role(video_id: str, asset_id: str, request: Request):
    body = await request.json()
    role = (body.get("role") or "content").strip()
    if role not in {"content", "texture", "style", "palette", "composition"}:
        return JSONResponse({"error": f"非法 role: {role}"}, status_code=400)
    from pipeline import state as _st
    rec = _st.get_video(video_id)
    if rec is None:
        return JSONResponse({"error": "项目不存在"}, status_code=404)
    import json as _json
    import time as _time
    meta = dict(rec.meta) if rec.meta else {}
    found = False
    for a in meta.get("outhan_assets", []):
        if a.get("id") == asset_id and a.get("type") == "reference":
            a.setdefault("metadata", {})
            a["metadata"]["role"] = role
            found = True
            break
    if not found:
        return JSONResponse({"error": "参考图不存在"}, status_code=404)
    with _st.get_conn() as conn:
        conn.execute(
            "UPDATE videos SET meta_json = ?, updated_at = ? WHERE video_id = ?",
            (_json.dumps(meta, ensure_ascii=False), _time.time(), video_id),
        )
    return JSONResponse({"ok": True, "role": role})


@app.get("/api/outhan/{video_id}/asset/{asset_id}")
async def api_outhan_asset_serve(video_id: str, asset_id: str):
    from fastapi.responses import Response as _Response
    from pipeline import outhan_storage as _outhan_storage
    asset = _outhan_storage.get_asset(video_id, asset_id)
    if not asset:
        return JSONResponse({"error": "not found"}, status_code=404)
    try:
        body = _outhan_storage.read_asset_bytes(asset)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    mime = _outhan_storage.mime_for_asset_type(asset["type"], asset.get("path"))
    return _Response(content=body, media_type=mime, headers={"Cache-Control": "private, max-age=3600"})


_VIDEO_SUMMARY_MAX_BYTES = 19 * 1024 * 1024
_VIDEO_SUMMARY_MIMES = {
    "video/mp4", "video/quicktime", "video/webm", "video/x-msvideo",
    "video/mpeg", "video/x-matroska",
}
_VIDEO_SUMMARY_SYSTEM = """你是 AI 视频选题分析师。看完一段参考视频，提炼成"下一条新视频的主题参考"。

要求：
- 中文输出，100-200 字
- 必须包含：参考视频的核心主题（讲什么）、风格特点（写实/电影感/动画/写意/纪录等）、节奏（快/慢/中）、画面氛围与色调倾向
- 是"借鉴而非照搬"：抽出值得参考的元素，不要逐镜头复述
- 直接输出主题描述本身，不要前缀、引号、Markdown、解释"""


@app.post("/api/outhan/{video_id}/summarize-video")
async def api_outhan_summarize_video(video_id: str, request: Request):
    proj = _state.get_video(video_id)
    if not proj:
        return JSONResponse({"error": "项目不存在"}, status_code=404)
    form = await request.form()
    file = form.get("file")
    if file is None or not hasattr(file, "read"):
        return JSONResponse({"error": "缺 file"}, status_code=400)
    mime = file.content_type or "video/mp4"
    if mime not in _VIDEO_SUMMARY_MIMES:
        return JSONResponse(
            {"error": f"不支持的视频格式：{mime}。支持：mp4/mov/webm/avi/mpeg/mkv"},
            status_code=400,
        )
    data = await file.read()
    if len(data) > _VIDEO_SUMMARY_MAX_BYTES:
        return JSONResponse(
            {"error": f"视频太大（{len(data)/1024/1024:.1f}MB > 19MB）。请截短或压缩。"},
            status_code=400,
        )
    import base64 as _base64
    b64 = _base64.b64encode(data).decode("ascii")
    from pipeline.tools import llm_client as _llm
    result = _llm.call_gemini_multimodal_text(
        _VIDEO_SUMMARY_SYSTEM,
        "请根据这段参考视频，输出新视频的主题参考描述。",
        media=[{"mimeType": mime, "data": b64}],
        timeout=300,
    )
    if result is None:
        return JSONResponse(
            {"error": "Gemini 多模态调用失败（密钥未配置 / 网络 / 内容审核）"},
            status_code=502,
        )
    summary, _cost = result
    return JSONResponse({
        "summary": summary,
        "source_file_name": file.filename,
        "source_size_bytes": len(data),
    })


# ==================== Outhan 步骤 1（合并）：首帧 API ====================
# 注：GET /first-frame 已被 project_outhan_first_frame_legacy（topic 那块）重定向；
# 首帧生成 API 仍独立保留，由合并后的 topic 页面调用。

@app.post("/api/outhan/{video_id}/generate-first-frame")
async def api_outhan_generate_first_frame(video_id: str, request: Request):
    """[AI-NOTE] 2026-05-08 重构：参考图 + 主题 + 用户描述 → Gemini 多模态融合 prompt → Seedream 出图。

    Body:
      reference_asset_ids: list[str]   最多 5 张已上传/已搜来的参考图资产 id
      user_desc:           str         用户对首帧的补充描述（可空，空则完全跟主题）
      size:                str         Seedream 输出尺寸（默认 2K）
      prompt:              str         （旧字段）若给了，跳过融合直接用此 prompt + 旧 enhancer 走纯文本路径
    """
    import base64 as _b64
    proj = _state.get_video(video_id)
    if not proj:
        return JSONResponse({"error": "项目不存在"}, status_code=404)
    body = await request.json()
    from pipeline import outhan_meta as _outhan_meta
    from pipeline import outhan_storage as _outhan_storage
    from pipeline import outhan_prompt_enhancer as _enhancer
    data = _outhan_meta.get_outhan_data(video_id)
    theme = (data.get("theme") or "").strip() or proj.title
    user_desc = (body.get("user_desc") or "").strip()
    legacy_prompt = (body.get("prompt") or "").strip()
    ref_ids = body.get("reference_asset_ids") or []
    if not isinstance(ref_ids, list):
        ref_ids = []
    ref_ids = [str(x) for x in ref_ids if x][:5]  # 上限 5 张
    size = body.get("size") or "2K"

    # 加载参考图为 base64，同时按相同顺序收集 role 标签
    # [AI-NOTE] 2026-05-08: role ∈ {content, texture, style, palette, composition}
    # 默认 content；图按上传顺序传给 Gemini + Seedream，图1/图2/图3 含义自然对齐
    _ALLOWED_ROLES = {"content", "texture", "style", "palette", "composition"}
    ref_media: list[dict[str, str]] = []  # 给 Gemini 看的（mimeType + base64）
    ref_data_urls: list[str] = []          # 给 Seedream 看的（data URL）
    ref_roles: list[str] = []
    for rid in ref_ids:
        a = _outhan_storage.get_asset(video_id, rid)
        if not a:
            continue
        try:
            raw = _outhan_storage.read_asset_bytes(a)
            ext = (a.get("path") or "").lower().rsplit(".", 1)[-1]
            mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                    "webp": "image/webp"}.get(ext, "image/jpeg")
            b64 = _b64.b64encode(raw).decode("ascii")
            ref_media.append({"mimeType": mime, "data": b64})
            ref_data_urls.append(f"data:{mime};base64,{b64}")
            meta = a.get("metadata") or {}
            r = meta.get("role") or "content"
            ref_roles.append(r if r in _ALLOWED_ROLES else "content")
        except Exception:
            continue

    # 决定最终 prompt
    fused_prompt: str | None = None
    if legacy_prompt:
        # 旧路径：用户已经手写好 prompt（覆盖一切）
        actual_prompt = legacy_prompt
    elif ref_media:
        # 新路径：参考图 + 主题 + 用户描述 → Gemini 按角色分配 + 融合
        fused_prompt = _enhancer.fuse_first_frame_prompt(
            theme, user_desc, ref_media, reference_roles=ref_roles
        )
        actual_prompt = fused_prompt or (
            f"{theme}。{user_desc}".rstrip("。") if user_desc else theme
        )
    else:
        # 无参考图：fallback 到旧的纯文本 enhancer
        base_text = f"{theme}。{user_desc}".rstrip("。") if user_desc else theme
        fused_prompt = _enhancer.enhance("seedream", base_text)
        actual_prompt = fused_prompt or base_text

    # [AI-NOTE] 2026-05-08: Seedream 4 多图组图编辑——把所有参考图也喂给它，
    # 配合 Gemini prompt 文本里的角色标注，让 Seedream 能"看着图改"而不是"看着字描"。
    # legacy_prompt 路径不传图（用户手写覆盖时保持原行为）。
    seedream_images = ref_data_urls if (ref_data_urls and not legacy_prompt) else None
    res = _ark_client.generate_image(
        prompt=actual_prompt, size=size, watermark=False, image=seedream_images,
    )
    if not res.get("ok"):
        return JSONResponse({"error": res.get("error") or "Seedream 生成失败"}, status_code=502)
    url = res.get("url")
    if not url:
        return JSONResponse({"error": "Seedream 返回空 url"}, status_code=502)
    if res.get("provider") == "stub":
        return JSONResponse({"error": "ARK_API_KEY 未配置（当前是 stub 占位图）"}, status_code=400)
    # [AI-NOTE] 2026-05-08: Volces 出图 URL 偶尔 SSL EOF（防火墙/握手抖动），3 次退避重试
    import urllib.request as _ur
    import time as _time_dl
    img: bytes | None = None
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            with _ur.urlopen(url, timeout=60) as r:
                img = r.read()
            break
        except Exception as e:
            last_err = e
            if attempt < 2:
                _time_dl.sleep(2 + attempt * 3)  # 2s, 5s
    if img is None:
        return JSONResponse({"error": f"下载图失败（3 次重试后）：{last_err}"}, status_code=502)
    saved = _outhan_storage.save_asset(
        video_id, "first_frame", img,
        base_name=f"first-{int(__import__('time').time())}",
        metadata={
            "prompt": actual_prompt,
            "user_desc": user_desc,
            "size": size,
            "source_url": url,
            "reference_count": len(ref_media),
            "fused_via_gemini": fused_prompt is not None,
            "seedream_saw_images": seedream_images is not None,
            "seedream_image_count": len(seedream_images) if seedream_images else 0,
        },
    )
    _usage.log(
        "ark_seedream", model=_ark_client.MODEL_IMAGE, video_id=video_id,
        agent="generate_first_frame",
        cost_cny=_usage.cost_seedream(1),
        meta={"size": size, "ref_count": len(ref_media)},
    )
    _outhan_meta.update_outhan_data(
        video_id, first_frame_asset_id=saved["id"], first_frame_prompt=actual_prompt
    )
    return JSONResponse({
        "asset_id": saved["id"],
        "url": saved["url"],
        "fused_prompt": fused_prompt,        # 让前端展示 Gemini 融合后的 prompt
        "reference_count": len(ref_media),
    })


@app.post("/api/outhan/{video_id}/set-first-frame")
async def api_outhan_set_first_frame(video_id: str, request: Request):
    body = await request.json()
    from pipeline import outhan_meta as _outhan_meta
    _outhan_meta.update_outhan_data(video_id, first_frame_asset_id=body.get("asset_id"))
    return JSONResponse({"ok": True})


@app.post("/api/outhan/{video_id}/advance-step")
async def api_outhan_advance(video_id: str, request: Request):
    body = await request.json()
    to = int(body.get("to") or 1)
    from pipeline import outhan_meta as _outhan_meta
    cur = _outhan_meta.get_outhan_data(video_id)["current_step"]
    if to > cur:
        _outhan_meta.set_current_step(video_id, to)
    return JSONResponse({"ok": True})


# ==================== Outhan 步骤 2：台本 ====================

@app.get("/project/{video_id}/outhan/script", response_class=HTMLResponse)
async def project_outhan_script(request: Request, video_id: str):
    proj = _state.get_video(video_id)
    if not proj:
        return RedirectResponse(url="/", status_code=303)
    from pipeline import outhan_meta as _outhan_meta
    data = _outhan_meta.get_outhan_data(video_id)
    segments = _outhan_meta.get_segments(video_id)
    return TEMPLATES.TemplateResponse(
        "step_outhan_script.html",
        {
            "request": request, "video_id": video_id,
            "outhan_title": proj.title, "outhan_data": data,
            "segments": segments,
            "current_step": 2, "reached_step": data["current_step"],
        },
    )


# [AI-NOTE] 2026-05-08: dialogue 类型的台本同时出画面 prompt + Fish S1 配音稿，
# 让画面与配音同源，避免后续补配音时画面/语音不匹配。silent 类型只出画面 prompt。
_OUTHAN_SCRIPT_SYSTEM_DIALOGUE = """你是视频分段台本作者，同时为 Fish S1 写配音稿。给定主题、首帧图、目标总时长，输出 N 段镜头 + 多行配音。

================================
关键概念：一段视频 ≠ 一段配音
================================
一段 8 秒视频里，可以有多条配音 line（lines），按节奏分布在视频时间轴上。
例如 8 秒段：line1 在 1.0s 说一句、line2 在 4.5s 说另一句、line2 之间静默。
也可以一段 5 秒视频只配 1 条 line，或者甚至 0 条 line（纯画面段）。

================================
要求
================================
- 按内容节奏自由分段，**不要固定每段时长**：可短（3 秒紧凑剪辑）、可长（10 秒慢镜表达），3-12 秒区间内
- 总段数控制在 3-12 段；各段时长加起来应接近用户给的目标总时长（容差 ±20%）
- 🎬 关于音视频协同：视频生成模型（Seedance/Vidu）**只出画面，不生成任何音频**。最终成片的所有声音都来自 Fish 配音，在装配阶段叠到无声视频上。所以视频提示词的核心任务是让画面里**有清晰的嘴型动作**，节奏与 lines 对得上 lip-sync。
- 🎬 **videoPrompt 必须把对白文字嵌进画面叙述**：让视频模型按这段叙述渲染对应嘴型动作（按节奏开口），即使没声音也行——音轨由 Fish 在装配时盖上。
    · 自然叙述「谁先说什么、停顿多久、谁接着说」——对白文字嵌进画面里
    · 强调嘴型/口型/眼神/手势细节（让 lip-sync 节奏与 lines 数组对齐）
    · 同时覆盖：景别、运镜、光线、风格
    · 反例 ❌ "近景两只玩偶坐桌前，眼神交流"（缺对白，模型不知道说啥，会一直闭嘴）
    · 正例 ✅ "近景两只毛绒玩偶坐桌前。小熊先开口说『你好啊』（嘴部清晰张合，眼神温柔），短暂停顿后，小兔回应『好久不见』（视线瞥向小熊，嘴角扬起）"
- 画面与配音同源：videoPrompt 里的对白文字（去掉情绪标签）必须与 lines[].text 一致，start_at 与 videoPrompt 里描述的开口顺序对应——这样 lip-sync 才对得上

🚨 **角色身份一致性铁律**（多角色视频必读）：
  视频生成模型不知道角色名字背后的外形，prompt 里只写"布布说"它会随机分配到任意一只——
  跨段就会**身份对调**（段 1 棕熊=布布，段 2 突然棕熊变一二）。

  **正确做法**：从主题 + 首帧图识别每个角色的外形特征（颜色/物种/位置/服装），在 **每段 videoPrompt 里每次提到角色都带「外形锚点 + 名字」双重标注**。

  示例（项目主角是棕熊布布 + 熊猫一二）：
    ❌ "布布说：『你好』，一二回答..."（外形锚点缺失，下一段就身份对调）
    ✅ "棕色小熊布布开口说：『你好』。黑白熊猫一二回应..."
    ✅ "左侧棕熊（布布）转头看向右侧熊猫（一二）..."
    ✅ "镜头特写棕熊布布的脸，眨眼后说..."

  关键：**所有段必须用同一套「外形→名字」绑定**。第一段怎么写，后续段就保持同样写法。
  即便 lines.text 里只用名字（如「(happy) 你好啊」OK），**videoPrompt 必须始终用「外形+名字」双标注**。
- 每条 line 是 Fish S1 规范配音稿：
  * 情绪标签用 Fish S1 官方英文语法（圆括号小写英文，放句首），每句最多 1 个：
    (happy) (sad) (excited) (calm) (confident) (curious) (empathetic) (serious) (proud) (whispering) (soft tone)
  * 在需要明显停顿的地方单独插入 (break) 表示短停、(long-break) 表示长停
  * 笑声/叹气用「标签+拟声词」组合：(laughing) 哈哈哈 / (sighing) 唉
  * 中文为主，英文专有名词保留原文；数字/年份/金额写成中文大写（如 二零二四年、三百万）
  * 单句不超过 40 字，长句拆短句方便朗读换气
  * 每句末尾必须有句号/问号/感叹号；句中停顿用逗号
  * 不要破折号、省略号、emoji、Markdown、舞台指示
  * 字数与 duration_hint 匹配（约 2.5 字/秒，2 秒 ≈ 5 字，4 秒 ≈ 10 字）

  🚨 **关键**：Fish S1 + 自定义音色组合下情感标签识别有时不稳定，所以**文本本身也要带情感**——
  即使标签被忽略，纯听文本也要能听出情绪。技巧：
    · 用语气词增强：哇、啊、嗯、哎、呀、呢、嘛、哼、啧
    · 用叠词强化：太好了 → 太棒太棒啦 / 真的真的好开心
    · 用标点叠加：感叹用「！！」，疑问用「？！」，停顿用「，」
    · 反例 ❌ (excited) 听起来太棒了。（标签依赖、文本平淡）
    · 正例 ✅ (excited) 哇！太棒啦！超喜欢！（语气词+叠加叹号自带兴奋感）

  * 例 1：(calm) 春天来了。万物悄悄苏醒。
  * 例 2：(curious) 咦？这是什么呀？(break) (excited) 哇！原来是这样啊！
  * 例 3：(serious) 这件事，可没那么简单。(long-break) (sighing) 唉，难办喽。
- 每条 line 的 start_at（在该段视频里的起始时间，秒）：
  * 0 ≤ start_at ≤ 段 duration - duration_hint
  * 同段内 lines 不能重叠（line[i].start_at + duration_hint ≤ line[i+1].start_at）
  * 多角色对话场景，用 voice_role 标识不同角色（"A" / "B" / "narrator"），系统会按 role 自动分配音色
- 严格遵循用户给的「补充指引」（如有）——优先级高于你的自由发挥

================================
输出 JSON 数组，每个元素 = 一段
================================
{
  "index": 数字（0-based）,
  "duration": 整数秒（3-12）,
  "scene": 中文场景描述,
  "videoPrompt": 中文视频提示词（主体动作/景别/运镜/光线/风格）,
  "lines": [
    {"text": "(calm) 你好啊。", "start_at": 0.5, "duration_hint": 1.5, "voice_role": "A"},
    {"text": "(happy) 好久不见。", "start_at": 4.0, "duration_hint": 1.5, "voice_role": "B"}
  ],
  "transitionToNext": 与下段接力关系（最后段空字符串）
}

直接返回 JSON 数组，无前缀、解释、Markdown 围栏"""

_OUTHAN_SCRIPT_SYSTEM_SILENT = """你是视频分段台本作者。给定主题、首帧图、目标总时长，输出 N 段镜头描述。

这是纯画面无配音的视频，只关注画面本身的视觉叙事。

要求：
- 按内容节奏自由分段，**不要固定每段时长**：可短（3 秒）、可长（10 秒慢镜），3-12 秒区间内
- 总段数控制在 3-12 段；各段时长加起来应接近用户给的目标总时长（容差 ±20%）
- 严格遵循用户给的「补充指引」（如有）——这是用户对台本走向的核心约束，优先级高于你的自由发挥
- 输出 JSON 数组，每元素：{"index": 数字（0-based）, "duration": 整数秒（3-12）, "scene": 中文场景描述, "videoPrompt": 中文视频提示词（主体动作/景别/运镜/光线/风格）, "lines": [], "transitionToNext": 与下段接力关系（最后段空字符串）}
  · lines 字段必须是 [] 空数组（纯画面无配音）
- videoPrompt 要让模型一看就知道主体动作
- 直接返回 JSON 数组，无前缀、解释、Markdown 围栏"""


# [AI-NOTE] 2026-05-08: 台本步骤的"补充指引"持久化——用户在台本页输入的关键词/方向/避免点，
# 下次进来还在；生成台本时 Gemini 会读它并按其约束发散性。
@app.post("/api/outhan/{video_id}/save-script-hint")
async def api_outhan_save_script_hint(video_id: str, request: Request):
    proj = _state.get_video(video_id)
    if not proj:
        return JSONResponse({"error": "项目不存在"}, status_code=404)
    body = await request.json()
    hint = (body.get("script_hint") or "").strip()
    from pipeline import outhan_meta as _outhan_meta
    _outhan_meta.update_outhan_data(video_id, script_hint=hint)
    return JSONResponse({"ok": True, "saved_chars": len(hint)})


@app.post("/api/outhan/{video_id}/generate-script")
async def api_outhan_generate_script(video_id: str):
    import json as _json
    proj = _state.get_video(video_id)
    if not proj:
        return JSONResponse({"error": "项目不存在"}, status_code=404)
    from pipeline import outhan_meta as _outhan_meta
    from pipeline import outhan_storage as _outhan_storage
    from pipeline.tools import llm_client as _llm
    data = _outhan_meta.get_outhan_data(video_id)
    if not data.get("first_frame_asset_id"):
        return JSONResponse({"error": "请先在步骤 2 选定首帧"}, status_code=400)
    target_duration = data.get("target_duration") or 60
    kind = data.get("video_kind") or "dialogue"
    script_hint = (data.get("script_hint") or "").strip()

    ff = _outhan_storage.get_asset(video_id, data["first_frame_asset_id"])
    media = []
    if ff:
        try:
            import base64 as _b64
            png = _outhan_storage.read_asset_bytes(ff)
            media.append({"mimeType": "image/png", "data": _b64.b64encode(png).decode("ascii")})
        except Exception:
            pass

    kind_hint = "（含对白/旁白配音稿）" if kind == "dialogue" else "（纯画面无配音）"
    parts = [
        f"主题：{data.get('theme') or proj.title}",
        f"目标总时长：{target_duration} 秒（请按节奏自由分段，每段 3-12 秒）",
        f"视频类型：{kind_hint}",
    ]
    if script_hint:
        parts.append(f"补充指引（用户重点关注的方向/关键词/避免点）：\n{script_hint}")
    parts.append("\n请基于主题、首帧图与上面的指引，输出 3-12 段台本（JSON 数组）。")
    user_prompt = "\n".join(parts)
    sys_prompt = _OUTHAN_SCRIPT_SYSTEM_DIALOGUE if kind == "dialogue" else _OUTHAN_SCRIPT_SYSTEM_SILENT
    result = _llm.call_gemini_multimodal_text(
        sys_prompt, user_prompt, media=media or None,
        model="gemini-2.5-pro", timeout=300,
    )
    if result is None:
        return JSONResponse({"error": "Gemini 多模态台本生成失败"}, status_code=502)
    content, _cost = result
    _usage.log(
        "gemini", model="gemini-2.5-pro", video_id=video_id,
        agent="generate_script",
        cost_cny=float(_cost or 0),
        meta={"kind": kind, "has_image": bool(media)},
    )
    text = _outhan_strip_md_fence(content)
    try:
        parsed = _json.loads(text)
        if not isinstance(parsed, list):
            raise ValueError("非数组")
    except Exception as e:
        return JSONResponse({"error": f"Gemini 返回的不是合法 JSON 数组：{e}"}, status_code=502)
    if len(parsed) < 2 or len(parsed) > 15:
        return JSONResponse(
            {"error": f"Gemini 输出段数异常（{len(parsed)}），期望 2-15 段"},
            status_code=502,
        )
    # [AI-NOTE] 2026-05-08: Gemini 偶尔乱序输出，按它自己的 index 升序排好再 enumerate；
    # 没给 index 的 fallback 0（保持原相对顺序）。
    parsed = sorted(parsed, key=lambda x: x.get("index", 0) if isinstance(x, dict) else 0)

    segments = []
    for i, s in enumerate(parsed):
        if not isinstance(s, dict):
            continue
        # [AI-NOTE] 2026-05-08: 段长 3-12 夹紧；缺失 fallback 5
        try:
            seg_duration = int(s.get("duration") or 5)
            seg_duration = max(3, min(12, seg_duration))
        except (TypeError, ValueError):
            seg_duration = 5

        # lines: dialogue 时从 Gemini 输出解析；silent 时强制空数组
        seg_lines: list[dict] = []
        if kind == "dialogue":
            raw_lines = s.get("lines") or []
            if isinstance(raw_lines, list):
                cur_t = 0.0
                for j, ln in enumerate(raw_lines):
                    if not isinstance(ln, dict):
                        continue
                    text = (ln.get("text") or "").strip()
                    if not text:
                        continue
                    try:
                        sa = float(ln.get("start_at", cur_t))
                        dh = float(ln.get("duration_hint") or 2.0)
                    except (TypeError, ValueError):
                        sa, dh = cur_t, 2.0
                    # 夹紧：start_at 不能超出段尾、不能负
                    sa = max(0.0, min(sa, max(0.0, seg_duration - 0.1)))
                    dh = max(0.5, min(dh, seg_duration))
                    seg_lines.append({
                        "id": f"L{j}",
                        "text": text,
                        "voice_id": "",  # 空 → 跟段级
                        "voice_role": (ln.get("voice_role") or "").strip(),
                        "audio_asset_id": None,
                        "start_at": sa,
                        "duration_hint": dh,
                    })
                    cur_t = sa + dh
            # 老兼容：如果 Gemini 给了顶层 script 但没给 lines
            if not seg_lines:
                top_script = (s.get("script") or "").strip()
                if top_script:
                    seg_lines = [{
                        "id": "L0", "text": top_script, "voice_id": "",
                        "voice_role": "", "audio_asset_id": None,
                        "start_at": 0.0, "duration_hint": float(seg_duration),
                    }]

        segments.append({
            "index": i,
            "scene": s.get("scene") or s.get("sceneDescription") or "",
            "prompt": s.get("videoPrompt") or s.get("prompt") or "",
            "duration": seg_duration,
            "engine": data.get("video_engine") or "jimeng",
            "first_frame_asset_id": data["first_frame_asset_id"] if i == 0 else None,
            "tail_frame_asset_id": None,
            "video_asset_id": None, "video_path": None,
            "task_id": None, "status": "pending",
            "lines": seg_lines,
            "voice_id": "",  # 段级默认音色，line voice_id 为空时回退到这里
            "transition_to_next": s.get("transitionToNext") or "",
            # 旧字段保留 None 兼容（assemble_mp4 老路径检查这俩）
            "script": None, "audio_asset_id": None,
        })
    _outhan_meta.replace_segments(video_id, segments)
    return JSONResponse({"count": len(segments), "segments": segments})


@app.patch("/api/outhan/{video_id}/segment/{index}")
async def api_outhan_segment_patch(video_id: str, index: int, request: Request):
    body = await request.json()
    allowed = {"prompt", "engine", "duration", "script", "scene", "first_frame_asset_id", "voice_id"}
    fields = {k: v for k, v in body.items() if k in allowed}
    if not fields:
        return JSONResponse({"error": "无可更新字段"}, status_code=400)
    # 引擎白名单
    if "engine" in fields and fields["engine"] not in ("jimeng", "vidu"):
        return JSONResponse({"error": "engine 只能是 jimeng 或 vidu"}, status_code=400)
    # voice_id 格式校验
    if "voice_id" in fields:
        ok, v = _outhan_validate_voice_id(str(fields["voice_id"] or ""))
        if not ok:
            return JSONResponse({"error": v}, status_code=400)
        fields["voice_id"] = v
    from pipeline import outhan_meta as _outhan_meta
    res = _outhan_meta.update_segment(video_id, index, **fields)
    if res is None:
        return JSONResponse({"error": "段不存在"}, status_code=404)
    return JSONResponse({"ok": True})


# ===== line CRUD（段内多句配音） =====
# [AI-NOTE] 2026-05-08: 段级 voice_id 仍由 segment patch 改；line 级只在这里改

@app.post("/api/outhan/{video_id}/segment/{index}/line")
async def api_outhan_line_add(video_id: str, index: int, request: Request):
    """段尾追加一条 line。Body: {text?, voice_id?, start_at?, duration_hint?}"""
    body = await request.json()
    text = (body.get("text") or "").strip()
    voice_id = (body.get("voice_id") or "").strip()
    if voice_id:
        ok, v = _outhan_validate_voice_id(voice_id)
        if not ok:
            return JSONResponse({"error": v}, status_code=400)
        voice_id = v
    try:
        start_at = float(body.get("start_at") or 0.0)
        duration_hint = float(body.get("duration_hint") or 2.0)
    except (TypeError, ValueError):
        return JSONResponse({"error": "start_at/duration_hint 必须是数字"}, status_code=400)
    from pipeline import outhan_meta as _outhan_meta
    line = _outhan_meta.add_line(
        video_id, index, text=text, voice_id=voice_id,
        start_at=start_at, duration_hint=duration_hint,
    )
    if line is None:
        return JSONResponse({"error": "段不存在"}, status_code=404)
    return JSONResponse({"ok": True, "line": line})


@app.patch("/api/outhan/{video_id}/segment/{index}/line/{line_id}")
async def api_outhan_line_patch(video_id: str, index: int, line_id: str, request: Request):
    """更新某条 line 字段。Body: {text?, voice_id?, start_at?, duration_hint?, voice_role?}"""
    body = await request.json()
    allowed = {"text", "voice_id", "start_at", "duration_hint", "voice_role"}
    fields = {k: v for k, v in body.items() if k in allowed}
    if not fields:
        return JSONResponse({"error": "无可更新字段"}, status_code=400)
    if "voice_id" in fields:
        ok, v = _outhan_validate_voice_id(str(fields["voice_id"] or ""))
        if not ok:
            return JSONResponse({"error": v}, status_code=400)
        fields["voice_id"] = v
    if "start_at" in fields:
        try: fields["start_at"] = max(0.0, float(fields["start_at"]))
        except (TypeError, ValueError):
            return JSONResponse({"error": "start_at 必须是数字"}, status_code=400)
    if "duration_hint" in fields:
        try: fields["duration_hint"] = max(0.5, float(fields["duration_hint"]))
        except (TypeError, ValueError):
            return JSONResponse({"error": "duration_hint 必须是数字"}, status_code=400)
    if "text" in fields:
        fields["text"] = (fields["text"] or "").strip()
    from pipeline import outhan_meta as _outhan_meta
    line = _outhan_meta.update_line(video_id, index, line_id, **fields)
    if line is None:
        return JSONResponse({"error": "段或 line 不存在"}, status_code=404)
    # text 改了 → 旧 audio_asset_id 作废（让用户重新合成）
    if "text" in fields:
        _outhan_meta.update_line(video_id, index, line_id, audio_asset_id=None)
    return JSONResponse({"ok": True, "line": line})


@app.delete("/api/outhan/{video_id}/segment/{index}/line/{line_id}")
async def api_outhan_line_delete(video_id: str, index: int, line_id: str):
    from pipeline import outhan_meta as _outhan_meta
    ok = _outhan_meta.remove_line(video_id, index, line_id)
    if not ok:
        return JSONResponse({"error": "段或 line 不存在"}, status_code=404)
    return JSONResponse({"ok": True})


# [AI-NOTE] 2026-05-08: 台本页用——把多行 plain text 重建为 lines 数组。
# 每行（非空）= 一条 line。
# 如果行数与现有 lines 数相同，**逐行更新 text 保留 start_at/voice_id/audio_asset_id**
# （用户只是改文字时不丢配音元数据）；如果行数变化，删所有重建（start_at 按段时长均分）。
@app.put("/api/outhan/{video_id}/segment/{index}/lines-text")
async def api_outhan_lines_from_text(video_id: str, index: int, request: Request):
    body = await request.json()
    text = (body.get("text") or "").strip()
    new_texts = [ln.strip() for ln in text.split("\n") if ln.strip()]

    from pipeline import outhan_meta as _outhan_meta
    seg = next((s for s in _outhan_meta.get_segments(video_id) if s.get("index") == index), None)
    if not seg:
        return JSONResponse({"error": "段不存在"}, status_code=404)
    seg_duration = float(seg.get("duration") or 5)
    cur_lines = list(seg.get("lines") or [])

    if len(new_texts) == len(cur_lines) and len(new_texts) > 0:
        # 行数一致 → 仅更新每行 text（保留 start_at/voice_id；text 改了的清 audio_asset_id）
        for i, nt in enumerate(new_texts):
            old = cur_lines[i]
            patch_fields: dict = {"text": nt}
            if old.get("text") != nt:
                patch_fields["audio_asset_id"] = None  # 文本改了 → 旧音频作废
            _outhan_meta.update_line(video_id, index, old["id"], **patch_fields)
        return JSONResponse({"ok": True, "mode": "in_place", "count": len(new_texts)})

    # 行数变化 → 重建：先删所有，再添加（start_at 按段时长均分）
    for ln in cur_lines:
        _outhan_meta.remove_line(video_id, index, ln["id"])

    if not new_texts:
        return JSONResponse({"ok": True, "mode": "cleared", "count": 0})

    n = len(new_texts)
    # 段时长均分给 N 行；每行 duration_hint = seg_duration / n（夹紧到 [0.5, seg_duration]）
    slot = max(0.5, min(seg_duration, seg_duration / n))
    for i, nt in enumerate(new_texts):
        _outhan_meta.add_line(
            video_id, index,
            text=nt,
            voice_id="",
            start_at=round(i * slot, 2),
            duration_hint=round(slot * 0.85, 2),  # 留 15% 作呼吸停顿
        )
    return JSONResponse({"ok": True, "mode": "rebuilt", "count": n})


@app.get("/api/outhan/{video_id}/segments")
async def api_outhan_segments(video_id: str):
    from pipeline import outhan_meta as _outhan_meta
    return JSONResponse({"segments": _outhan_meta.get_segments(video_id)})


# ==================== Outhan 步骤 3：分段视频 ====================

@app.get("/project/{video_id}/outhan/video", response_class=HTMLResponse)
async def project_outhan_video(request: Request, video_id: str):
    proj = _state.get_video(video_id)
    if not proj:
        return RedirectResponse(url="/", status_code=303)
    from pipeline import outhan_meta as _outhan_meta
    data = _outhan_meta.get_outhan_data(video_id)
    segments = _outhan_meta.get_segments(video_id)
    return TEMPLATES.TemplateResponse(
        "step_outhan_video.html",
        {
            "request": request, "video_id": video_id,
            "outhan_title": proj.title, "outhan_data": data,
            "segments": segments,
            "current_step": 3, "reached_step": data["current_step"],
        },
    )


@app.post("/api/outhan/{video_id}/reset-all-segments")
async def api_outhan_reset_all_segments(video_id: str):
    """全部重来：清所有段视频/音频/尾帧/任务，第 0 段首帧恢复项目首帧。"""
    proj = _state.get_video(video_id)
    if not proj:
        return JSONResponse({"error": "项目不存在"}, status_code=404)
    from pipeline import outhan_meta as _outhan_meta
    _outhan_meta.reset_all_segments(video_id)
    return JSONResponse({"ok": True})


# [AI-NOTE] 2026-05-08: 取消生成中段的任务——只把状态拨回 pending + 清 task_id，
# 不动 prompt/scene/duration/engine/first_frame_asset_id。
# 用于用户在视频生成页点「取消」按钮时，避免段卡在 polling 让前端轮询空转。
@app.post("/api/outhan/{video_id}/cancel-running-segments")
async def api_outhan_cancel_running_segments(video_id: str):
    proj = _state.get_video(video_id)
    if not proj:
        return JSONResponse({"error": "项目不存在"}, status_code=404)
    from pipeline import outhan_meta as _outhan_meta
    segs = _outhan_meta.get_segments(video_id)
    cancelled: list[int] = []
    for s in segs:
        if s.get("status") in ("creating", "polling", "extracting"):
            _outhan_meta.update_segment(
                video_id, s["index"], status="pending", task_id=None,
            )
            cancelled.append(s["index"])
    return JSONResponse({"ok": True, "cancelled_indices": cancelled})


@app.post("/api/outhan/{video_id}/segment/{index}/generate-video")
async def api_outhan_seg_gen_video(video_id: str, index: int):
    import time as _time
    from pipeline import outhan_meta as _outhan_meta
    from pipeline import outhan_storage as _outhan_storage
    seg = next((s for s in _outhan_meta.get_segments(video_id) if s.get("index") == index), None)
    if not seg:
        return JSONResponse({"error": "段不存在"}, status_code=404)
    if not seg.get("prompt"):
        return JSONResponse({"error": "段没有提示词"}, status_code=400)

    # 级联重置 N+1+
    _outhan_meta.cascade_reset_after(video_id, index)
    _outhan_meta.update_segment(video_id, index, status="creating", task_id=None)

    engine = seg.get("engine") or "jimeng"
    duration = seg.get("duration") or 5

    # 拿首帧 dataUrl 或 url
    ff_id = seg.get("first_frame_asset_id")
    if not ff_id:
        # 兜底：用项目首帧
        ff_id = _outhan_meta.get_outhan_data(video_id).get("first_frame_asset_id")
    image_url = None
    if ff_id:
        ff_asset = _outhan_storage.get_asset(video_id, ff_id)
        if ff_asset:
            try:
                import base64 as _b64
                img = _outhan_storage.read_asset_bytes(ff_asset)
                image_url = f"data:image/png;base64,{_b64.b64encode(img).decode('ascii')}"
            except Exception:
                pass

    # [AI-NOTE] 2026-05-07: Gemini 自动按目标视频模型偏好改写 prompt（v0.2.5 特性）
    from pipeline import outhan_prompt_enhancer as _enhancer
    ref_image_part = None
    if image_url and image_url.startswith("data:"):
        try:
            mime_part, b64_part = image_url.split(";base64,", 1)
            ref_image_part = {"mimeType": mime_part[5:], "data": b64_part}
        except Exception:
            pass
    target = "seedance" if engine == "jimeng" else "vidu"
    enhanced = _enhancer.enhance(target, seg["prompt"], reference_image=ref_image_part)
    actual_prompt = enhanced or seg["prompt"]
    if enhanced and enhanced != seg["prompt"]:
        _outhan_meta.update_segment(video_id, index, enhanced_prompt=enhanced)

    if engine == "jimeng":
        if not image_url:
            _outhan_meta.update_segment(video_id, index, status="failed")
            return JSONResponse({"error": "Seedance 需要首帧图"}, status_code=400)
        r = _ark_client.create_video_task(
            image_url=image_url, motion_prompt=actual_prompt,
            duration=duration, resolution="720p", watermark=False,
        )
    else:
        from pipeline.tools import vidu_client as _vidu
        r = _vidu.vidu_create(
            prompt=actual_prompt, model="viduq3-pro",
            images=[image_url] if image_url else None,
            duration=duration, resolution="720p", watermark=False,
        )
    if not r.get("ok"):
        _outhan_meta.update_segment(video_id, index, status="failed")
        return JSONResponse({"error": r.get("error") or "创建任务失败"}, status_code=502)
    _outhan_meta.update_segment(video_id, index, status="polling", task_id=r["task_id"])
    return JSONResponse({"task_id": r["task_id"], "engine": engine})


@app.get("/api/outhan/{video_id}/segment/{index}/poll-video")
async def api_outhan_seg_poll(video_id: str, index: int):
    import urllib.request as _ur
    from pipeline import outhan_meta as _outhan_meta
    from pipeline import outhan_storage as _outhan_storage
    seg = next((s for s in _outhan_meta.get_segments(video_id) if s.get("index") == index), None)
    if not seg:
        return JSONResponse({"error": "段不存在"}, status_code=404)
    if seg.get("status") == "completed":
        return JSONResponse({"status": "completed"})
    task_id = seg.get("task_id")
    if not task_id:
        return JSONResponse({"error": "段没有 task_id"}, status_code=400)
    engine = seg.get("engine") or "jimeng"

    if engine == "jimeng":
        r = _ark_client.get_video_task(task_id)
        state = r.get("status")
        url = r.get("video_url")
        is_done = state == "succeeded"
        is_fail = state in ("failed", "cancelled")
    else:
        from pipeline.tools import vidu_client as _vidu
        r = _vidu.vidu_query(task_id)
        state = r.get("state")
        url = r.get("video_url")
        is_done = state == "success"
        is_fail = state == "failed"

    if is_fail:
        _outhan_meta.update_segment(video_id, index, status="failed")
        return JSONResponse({"status": "failed", "task_state": state, "error": r.get("error")})
    if not is_done or not url:
        return JSONResponse({"status": seg.get("status"), "task_state": state})

    # 下载视频（[AI-NOTE] 2026-05-07: 三次重试，应对 SSL UNEXPECTED_EOF / 临时网络断流）
    _outhan_meta.update_segment(video_id, index, status="extracting")
    import time as _time2
    video_bytes: bytes | None = None
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            with _ur.urlopen(url, timeout=180) as resp:
                video_bytes = resp.read()
            break
        except Exception as e:
            last_err = e
            if attempt < 2:
                _time2.sleep(2 + attempt * 3)  # 2s, 5s
    if video_bytes is None:
        _outhan_meta.update_segment(video_id, index, status="failed")
        return JSONResponse(
            {"error": f"下载视频失败（已重试 3 次）：{last_err}"},
            status_code=502,
        )

    saved = _outhan_storage.save_asset(
        video_id, "segment_video", video_bytes,
        base_name=f"seg-{index}-{engine}-{int(__import__('time').time())}",
        segment_index=index,
        metadata={"source_url": url},
    )
    # 记账：在视频成功落盘时埋点（不在 create-video 时埋，避免失败计费）
    duration = float(seg.get("duration") or 5)
    if engine == "jimeng":
        _usage.log("ark_seedance", model="seedance", video_id=video_id,
                   agent="generate_segment_video",
                   cost_cny=_usage.cost_seedance(duration),
                   meta={"seg_index": index, "duration_sec": duration, "task_id": task_id})
    else:
        _usage.log("vidu", model="viduq3-pro", video_id=video_id,
                   agent="generate_segment_video",
                   cost_cny=_usage.cost_vidu(duration),
                   meta={"seg_index": index, "duration_sec": duration, "task_id": task_id})

    # 抽尾帧
    from pipeline.tools import ffmpeg_client as _ffmpeg
    from pathlib import Path as _Path
    import tempfile as _tempfile
    tail_asset_id = None
    try:
        tmp_png = _Path(_tempfile.gettempdir()) / f"outhan_tail_{video_id}_{index}.png"
        rr = _ffmpeg.extract_last_frame(saved["path"], tmp_png)
        if rr.get("ok"):
            png_bytes = tmp_png.read_bytes()
            tail = _outhan_storage.save_asset(
                video_id, "tail_frame", png_bytes,
                base_name=f"seg-{index}-tail",
                segment_index=index,
            )
            tail_asset_id = tail["id"]
            try: tmp_png.unlink(missing_ok=True)
            except Exception: pass
    except Exception:
        pass

    _outhan_meta.update_segment(
        video_id, index,
        status="completed",
        video_asset_id=saved["id"],
        video_path=saved["path"],
        tail_frame_asset_id=tail_asset_id,
    )
    if tail_asset_id:
        # 把尾帧设为 N+1 段的首帧
        _outhan_meta.update_segment(video_id, index + 1, first_frame_asset_id=tail_asset_id)

    return JSONResponse({"status": "completed", "video_asset_id": saved["id"], "tail_asset_id": tail_asset_id})


# ==================== Outhan 步骤 4：配音 ====================

@app.get("/project/{video_id}/outhan/audio", response_class=HTMLResponse)
async def project_outhan_audio(request: Request, video_id: str):
    proj = _state.get_video(video_id)
    if not proj:
        return RedirectResponse(url="/", status_code=303)
    from pipeline import outhan_meta as _outhan_meta
    data = _outhan_meta.get_outhan_data(video_id)
    segments = _outhan_meta.get_segments(video_id)
    return TEMPLATES.TemplateResponse(
        "step_outhan_audio.html",
        {
            "request": request, "video_id": video_id,
            "outhan_title": proj.title, "outhan_data": data,
            "segments": segments,
            "current_step": 4, "reached_step": data["current_step"],
        },
    )


_OUTHAN_AUDIO_SYSTEM = """你为视频生成 Fish S1 配音稿。

Fish Audio S1 规范（严格遵守）：
- 情感标签用官方英文语法（圆括号小写英文，放句首），每句最多 1 个：
  (happy) (sad) (excited) (calm) (confident) (curious) (empathetic) (serious) (proud) (whispering) (soft tone)
- 在需要明显停顿的地方单独插入 (break) 表示短停、(long-break) 表示长停
- 笑声/叹气用「标签+拟声词」组合：(laughing) 哈哈哈 / (sighing) 唉
- 中文为主，英文专有名词保留原文；数字/年份/金额写成中文大写（如 二零二四年、三百万）
- 单句不超过 40 字，长句拆短句方便朗读换气
- 每句末尾必须有句号/问号/感叹号；句中停顿用逗号
- 不要破折号、省略号、emoji、Markdown、舞台指示
- 字数与该段时长匹配（约 2.5 字/秒）

🚨 **关键**：Fish S1 + 自定义音色组合下情感标签识别有时不稳定，所以**文本本身也要带情感**——
技巧：用语气词（哇/啊/嗯/哎/呀/呢/嘛）+ 叠词 + 标点叠加（！！ ？！）增强，
反例 ❌ (excited) 听起来太棒了。（依赖标签）  正例 ✅ (excited) 哇！太棒啦！超喜欢！

例 1：(calm) 今天真是好日子。 (happy) 我慢慢往前走，心里暖暖的。
例 2：(curious) 咦？这是什么呀？ (break) (excited) 哇！原来是这样啊！

任务：根据每段镜头描述，写一段配音稿（中文，与时长匹配）。
输出 JSON 数组，每元素：{"segmentIndex": 数字, "script": "S1 规范配音稿"}
直接输出 JSON 数组，无前缀、解释、Markdown。"""


@app.post("/api/outhan/{video_id}/generate-all-scripts")
async def api_outhan_gen_all_scripts(video_id: str):
    import json as _json
    proj = _state.get_video(video_id)
    if not proj:
        return JSONResponse({"error": "项目不存在"}, status_code=404)
    from pipeline import outhan_meta as _outhan_meta
    from pipeline.tools import llm_client as _llm
    data = _outhan_meta.get_outhan_data(video_id)
    if data.get("video_kind") == "silent":
        return JSONResponse(
            {"error": "纯画面项目不需要配音稿（去步骤 1 切换为「有对白」类型再来）"},
            status_code=400,
        )
    segs = _outhan_meta.get_segments(video_id)
    if not segs:
        return JSONResponse({"error": "还没有台本"}, status_code=400)

    seg_inputs = [
        {"segmentIndex": s["index"], "duration": s.get("duration") or 5,
         "scene": s.get("scene") or s.get("prompt") or ""}
        for s in segs
    ]
    user_prompt = f"""项目主题：{data.get("theme") or proj.title}

各段：
{_json.dumps(seg_inputs, ensure_ascii=False, indent=2)}

请输出 Fish S1 配音稿（JSON 数组）。"""
    res = _llm.call_gemini_json(_OUTHAN_AUDIO_SYSTEM, user_prompt, timeout=240)
    if res is not None:
        _usage.log(
            "gemini", model="gemini-2.5-flash", video_id=video_id,
            agent="generate_all_scripts",
            cost_cny=float(res[1] or 0) if isinstance(res, tuple) else 0,
            meta={"segs_count": len(seg_inputs)},
        )
    if res is None:
        return JSONResponse({"error": "Gemini 配音稿生成失败"}, status_code=502)
    content, _cost = res
    text = _outhan_strip_md_fence(content)
    try:
        parsed = _json.loads(text)
        if not isinstance(parsed, list):
            raise ValueError("非数组")
    except Exception as e:
        return JSONResponse({"error": f"Gemini 返回非 JSON 数组：{e}"}, status_code=502)
    for item in parsed:
        idx = item.get("segmentIndex")
        scr = item.get("script") or ""
        if isinstance(idx, int):
            _outhan_meta.update_segment(video_id, idx, script=scr)
    return JSONResponse({"count": len(parsed)})


async def _synthesize_one_line(
    video_id: str, seg_index: int, line: dict, *,
    seg_voice: str, proj_voice: str,
):
    """[AI-NOTE] 2026-05-08: 合成一条 line 的音频，落 segment_audio 资产，回写 line.audio_asset_id。
    音色优先级：line.voice_id > seg_voice > proj_voice > S1 默认（无 ref）"""
    from pipeline import outhan_meta as _outhan_meta
    from pipeline import outhan_storage as _outhan_storage
    text = (line.get("text") or "").strip()
    if not text:
        return {"ok": False, "error": "空文本"}
    line_voice = (line.get("voice_id") or "").strip()
    chosen_voice = line_voice or seg_voice or proj_voice
    # [AI-NOTE] 2026-05-08 修 bug：之前把 Fish 临时文件落到 audio/ 目录，与 save_asset 写入位置撞名，
    # save_asset 后 unlink 反而把刚存好的资产文件删了 → audio_asset_id 指向不存在的文件。
    # 修法：临时文件用项目根目录下的 _tmp_synth/ 子目录，与 save_asset 的目标 audio/ 隔离。
    tmp_dir = _outhan_storage.project_dir(video_id) / "_tmp_synth"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    ts = int(__import__('time').time())
    tmp_path = tmp_dir / f"seg-{seg_index}-{line.get('id','L?')}-{ts}.mp3"
    try:
        try:
            if chosen_voice:
                # use_s1=True 让 (curious)(happy) 等情感标签生效，跳过 _preprocess_text
                r = await _fish_audio.synthesize_async(text, chosen_voice, tmp_path, use_s1=True)
            else:
                r = await _fish_audio.synthesize_s1_async(text, tmp_path)
            if not r.get("ok"):
                return {"ok": False, "error": r.get("error") or "Fish 合成失败"}
            audio_bytes = tmp_path.read_bytes()
            saved = _outhan_storage.save_asset(
                video_id, "segment_audio", audio_bytes,
                base_name=f"seg-{seg_index}-{line.get('id','L?')}-{ts}",
                segment_index=seg_index,
            )
            # 记账
            _usage.log(
                "fish_audio", model="s1", video_id=video_id,
                agent="synthesize_line",
                cost_cny=_usage.cost_fish_s1(text),
                meta={"chars": len(text), "voice_id": chosen_voice or "default", "line_id": line.get("id")},
            )
            _outhan_meta.update_line(
                video_id, seg_index, line.get("id"),
                audio_asset_id=saved["id"],
            )
            return {
                "ok": True, "audio_asset_id": saved["id"], "url": saved["url"],
                "voice_used": chosen_voice or "(S1 默认)",
            }
        except Exception as e:
            return {"ok": False, "error": str(e)[:300]}
    finally:
        # [AI-NOTE] 2026-05-08 审计修：所有路径（含 Fish 合成异常、读 bytes 失败、save_asset 异常）
        # 都清临时文件，避免 _tmp_synth/ 累积垃圾
        try: tmp_path.unlink(missing_ok=True)
        except Exception: pass


@app.post("/api/outhan/{video_id}/segment/{index}/synthesize")
async def api_outhan_seg_synth(video_id: str, index: int, request: Request):
    """[AI-NOTE] 2026-05-08: Body 可选 {line_id}：
       · 给 line_id：仅合成该条 line
       · 不给 line_id：批量合成段内所有未合成的 lines
       老路径（无 lines 字段，但 segment.script 存在）按 segment.script 单条合成（兼容）
    """
    body: dict = {}
    try: body = await request.json()
    except Exception: body = {}
    target_line_id = (body.get("line_id") or "").strip() or None

    from pipeline import outhan_meta as _outhan_meta
    seg = next((s for s in _outhan_meta.get_segments(video_id) if s.get("index") == index), None)
    if not seg:
        return JSONResponse({"error": "段不存在"}, status_code=404)
    proj_data = _outhan_meta.get_outhan_data(video_id)
    seg_voice = (seg.get("voice_id") or "").strip()
    proj_voice = (proj_data.get("voice_id") or "").strip()

    lines = seg.get("lines") or []
    # 老路径：没 lines 但有 segment.script，wrap 处理
    if not lines and (seg.get("script") or "").strip():
        # 老兼容路径——直接合成 segment.script，写到 segment.audio_asset_id（保持老 schema）
        from pipeline import outhan_storage as _outhan_storage
        chosen_voice = seg_voice or proj_voice
        # 临时目录避免撞 save_asset 落盘位置
        tmp_dir = _outhan_storage.project_dir(video_id) / "_tmp_synth"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = tmp_dir / f"seg-{index}-{int(__import__('time').time())}.mp3"
        try:
            try:
                if chosen_voice:
                    r = await _fish_audio.synthesize_async(seg["script"], chosen_voice, tmp_path, use_s1=True)
                else:
                    r = await _fish_audio.synthesize_s1_async(seg["script"], tmp_path)
                if not r.get("ok"):
                    return JSONResponse(
                        {"error": f"Fish 合成失败：{r.get('error') or '未知错误'}"},
                        status_code=502,
                    )
                audio_bytes = tmp_path.read_bytes()
                saved = _outhan_storage.save_asset(
                    video_id, "segment_audio", audio_bytes,
                    base_name=f"seg-{index}-final-{int(__import__('time').time())}",
                    segment_index=index,
                )
                _usage.log(
                    "fish_audio", model="s1", video_id=video_id,
                    agent="synthesize_legacy_script",
                    cost_cny=_usage.cost_fish_s1(seg["script"]),
                    meta={"chars": len(seg["script"] or ""), "voice_id": chosen_voice or "default"},
                )
            except Exception as e:
                return JSONResponse({"error": f"Fish 合成失败：{e}"}, status_code=502)
        finally:
            # [AI-NOTE] 2026-05-08 审计修：try/finally 保证临时文件总是清理
            try: tmp_path.unlink(missing_ok=True)
            except Exception: pass
        _outhan_meta.update_segment(video_id, index, audio_asset_id=saved["id"])
        return JSONResponse({
            "audio_asset_id": saved["id"], "url": saved["url"],
            "voice_used": chosen_voice or "(S1 默认)",
        })

    if not lines:
        return JSONResponse({"error": "段内没有 line（请先生成台本或手动加 line）"}, status_code=400)

    # [AI-NOTE] 2026-05-08: 单条 line（用户手动改某行后单独重合成）走 _synthesize_one_line。
    # 批量（不指定 line_id）→ 智能判断：所有非空 line 解析后 voice 相同 → 整段一次合成（连贯！）；
    # 不同 → fallback 逐行合成（保留多角色场景）
    if target_line_id:
        target = next((ln for ln in lines if ln.get("id") == target_line_id), None)
        if not target:
            return JSONResponse({"error": f"line {target_line_id} 不存在"}, status_code=404)
        if not (target.get("text") or "").strip():
            return JSONResponse({"error": "line 文本为空"}, status_code=400)
        r = await _synthesize_one_line(
            video_id, index, target,
            seg_voice=seg_voice, proj_voice=proj_voice,
        )
        # 单条合成会顺便解除整段 audio（防止段+行同存）
        if r.get("ok"):
            _outhan_meta.update_segment(video_id, index, audio_asset_id=None)
        return JSONResponse({
            "results": [{"line_id": target.get("id"), **r}],
            "ok_count": 1 if r.get("ok") else 0,
            "fail_count": 0 if r.get("ok") else 1,
        })

    # 批量合成：检测是否可以"整段一次合成"
    non_empty = [ln for ln in lines if (ln.get("text") or "").strip()]
    if not non_empty:
        return JSONResponse({"error": "段内所有 line 文本都为空"}, status_code=400)

    def _resolve_voice(ln):
        return (ln.get("voice_id") or "").strip() or seg_voice or proj_voice

    voices_used = set(_resolve_voice(ln) for ln in non_empty)
    if len(voices_used) == 1:
        # ✨ 整段连贯合成：所有行同 voice，拼成一段一次调用 Fish
        from pipeline import outhan_storage as _outhan_storage
        chosen_voice = next(iter(voices_used))
        # 用 (break) 分句让 Fish 自然停顿（保留每行的情感标签）
        joined_text = " (break) ".join(ln["text"].strip() for ln in non_empty)

        tmp_dir = _outhan_storage.project_dir(video_id) / "_tmp_synth"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        ts = int(__import__('time').time())
        tmp_path = tmp_dir / f"seg-{index}-grouped-{ts}.mp3"
        try:
            try:
                if chosen_voice:
                    r = await _fish_audio.synthesize_async(
                        joined_text, chosen_voice, tmp_path, use_s1=True,
                    )
                else:
                    r = await _fish_audio.synthesize_s1_async(joined_text, tmp_path)
                if not r.get("ok"):
                    return JSONResponse(
                        {"error": f"Fish 整段合成失败：{r.get('error') or '未知错误'}"},
                        status_code=502,
                    )
                audio_bytes = tmp_path.read_bytes()
                saved = _outhan_storage.save_asset(
                    video_id, "segment_audio", audio_bytes,
                    base_name=f"seg-{index}-final-{ts}",
                    segment_index=index,
                )
                _usage.log(
                    "fish_audio", model="s1", video_id=video_id,
                    agent="synthesize_segment_grouped",
                    cost_cny=_usage.cost_fish_s1(joined_text),
                    meta={"chars": len(joined_text), "voice_id": chosen_voice or "default",
                          "lines_count": len(non_empty)},
                )
            except Exception as e:
                return JSONResponse({"error": f"Fish 整段合成异常：{e}"}, status_code=502)
        finally:
            # [AI-NOTE] 2026-05-08 审计修：finally 保证 _tmp_synth/ 清理
            try: tmp_path.unlink(missing_ok=True)
            except Exception: pass

        # 清掉每行的旧 audio_asset_id（否则装配会同时看到段+行音频，混乱）
        for ln in non_empty:
            _outhan_meta.update_line(video_id, index, ln["id"], audio_asset_id=None)
        _outhan_meta.update_segment(video_id, index, audio_asset_id=saved["id"])
        return JSONResponse({
            "mode": "grouped",
            "audio_asset_id": saved["id"], "url": saved["url"],
            "voice_used": chosen_voice or "(S1 默认)",
            "lines_count": len(non_empty),
        })

    # 多角色 fallback：保留逐行合成
    targets = non_empty
    results = []
    fail_count = 0
    for ln in targets:
        r = await _synthesize_one_line(
            video_id, index, ln,
            seg_voice=seg_voice, proj_voice=proj_voice,
        )
        results.append({"line_id": ln.get("id"), **r})
        if not r.get("ok"):
            fail_count += 1
    # 多角色逐行后清掉段级合成（避免装配混乱）
    _outhan_meta.update_segment(video_id, index, audio_asset_id=None)
    return JSONResponse({
        "mode": "per_line",
        "results": results,
        "ok_count": sum(1 for x in results if x.get("ok")),
        "fail_count": fail_count,
    })


# [AI-NOTE] 2026-05-08: Fish 音色列表暴露给 Outhan 配音页。
# 排序：自有（_OWN_VOICES）→ 用户自定义（user_voices.json）→ 公共热门
# 启动时 list_voices_sync() 拉一次；抖动失败时此路由 lazy 重试。
# ?refresh=1 强制重拉（点"添加自定义音色"后调一次让新音色立即出现）
# [AI-NOTE] 2026-05-08: Outhan 用量汇报端点
# 用户每次说"今天工作结束"时调用：返回自上次汇报以来的消耗 + 累计总消耗
# - GET /api/outhan/usage/report?mark=1     生成报告 + 更新上次汇报时间戳
# - GET /api/outhan/usage/report?mark=0     仅查看不更新（peek）
# - GET /api/outhan/usage/report?since=ts   自定义起点
@app.get("/api/outhan/usage/report")
async def api_outhan_usage_report(mark: int = 1, since: float = 0):
    last_ts = float(since) if since > 0 else _usage.get_last_report_ts()
    period = _usage.query_summary(last_ts)
    total = _usage.query_total()
    if mark == 1 and since == 0:
        _usage.update_last_report_ts()
    return JSONResponse({
        "period": period,
        "total": total,
    })


@app.get("/api/outhan/voices")
async def api_outhan_voices(refresh: bool = False):
    global _FISH_VOICE_PRESETS
    base_count = len(_fish_audio._OWN_VOICES) + len(_fish_audio._load_user_voices())
    if refresh or len(_FISH_VOICE_PRESETS) <= base_count:
        try:
            fresh = _fish_audio.list_voices_sync(page_size=30)
            if len(fresh) >= len(_FISH_VOICE_PRESETS):
                _FISH_VOICE_PRESETS = fresh
        except Exception as e:
            print(f"[outhan] lazy refresh voices 失败：{e}",
                  file=__import__('sys').stderr)
    return JSONResponse({"voices": _FISH_VOICE_PRESETS})


# [AI-NOTE] 2026-05-08: 用户自定义音色 CRUD —— 持久化到 config/user_voices.json
@app.post("/api/outhan/voices/custom")
async def api_outhan_voices_add_custom(request: Request):
    body = await request.json()
    voice_id = (body.get("id") or "").strip()
    name = (body.get("name") or "").strip()
    desc = (body.get("desc") or "").strip()
    if not voice_id:
        return JSONResponse({"error": "voice_id 必填"}, status_code=400)
    res = _fish_audio.add_user_voice(voice_id, name=name, desc=desc)
    if res.get("error"):
        return JSONResponse({"error": res["error"]}, status_code=400)
    # 立即重拉全局列表让新音色生效
    global _FISH_VOICE_PRESETS
    try:
        _FISH_VOICE_PRESETS = _fish_audio.list_voices_sync(page_size=30)
    except Exception:
        pass
    return JSONResponse({"ok": True, "voice": res["voice"]})


@app.delete("/api/outhan/voices/custom/{voice_id}")
async def api_outhan_voices_remove_custom(voice_id: str):
    ok = _fish_audio.remove_user_voice(voice_id)
    if not ok:
        return JSONResponse({"error": "音色不存在"}, status_code=404)
    global _FISH_VOICE_PRESETS
    try:
        _FISH_VOICE_PRESETS = _fish_audio.list_voices_sync(page_size=30)
    except Exception:
        pass
    return JSONResponse({"ok": True})


# ==================== Outhan 步骤 5：打包 ====================

@app.get("/project/{video_id}/outhan/package", response_class=HTMLResponse)
async def project_outhan_package(request: Request, video_id: str):
    proj = _state.get_video(video_id)
    if not proj:
        return RedirectResponse(url="/", status_code=303)
    from pipeline import outhan_meta as _outhan_meta
    data = _outhan_meta.get_outhan_data(video_id)
    segs = _outhan_meta.get_segments(video_id)
    # [AI-NOTE] 2026-05-08 lines 架构升级后修：
    # 段被认为"音频齐全" = 所有非空 line 都已合成 + 至少有一条 line（或老 schema fallback）
    # 老段（无 lines 但有 segment.audio_asset_id）也算齐全，向后兼容
    def _seg_audio_ready(s: dict) -> bool:
        # 整段合成模式：segment.audio_asset_id 存在 → 齐
        if s.get("audio_asset_id"):
            return True
        # 多角色 / 逐行模式：所有非空 line 都已合成
        lines = s.get("lines") or []
        non_empty = [ln for ln in lines if (ln.get("text") or "").strip()]
        if non_empty:
            return all(ln.get("audio_asset_id") for ln in non_empty)
        return False

    def _seg_has_script(s: dict) -> bool:
        lines = s.get("lines") or []
        if any((ln.get("text") or "").strip() for ln in lines):
            return True
        return bool((s.get("script") or "").strip())

    stats = {
        "total": len(segs),
        "with_video": sum(1 for s in segs if s.get("video_asset_id")),
        "with_audio": sum(1 for s in segs if _seg_audio_ready(s)),
        "with_script": sum(1 for s in segs if _seg_has_script(s)),
    }
    checks = {"first_frame": bool(data.get("first_frame_asset_id"))}
    return TEMPLATES.TemplateResponse(
        "step_outhan_package.html",
        {
            "request": request, "video_id": video_id,
            "outhan_title": proj.title, "outhan_data": data,
            "stats": stats, "checks": checks,
            "current_step": 5, "reached_step": data["current_step"],
        },
    )


@app.post("/api/outhan/{video_id}/assemble-mp4")
async def api_outhan_assemble_mp4(video_id: str):
    """[AI-NOTE] 2026-05-07: 一键合成成片——每段视频用 Fish 音频替换音轨，N 段串接。

    同步执行（5-12 段大约 1-3 分钟）。详细的 ffmpeg 调用在 pipeline/outhan_video_assembly.py。
    """
    proj = _state.get_video(video_id)
    if not proj:
        return JSONResponse({"error": "项目不存在"}, status_code=404)
    from pipeline import outhan_video_assembly as _assembly
    result = _assembly.assemble_mp4(video_id)
    if not result.get("ok"):
        # [AI-NOTE] 2026-05-08: assembly 模块返回 error_type ∈ {"input","ffmpeg"}；
        # 缺素材 400，ffmpeg/系统错 502。之前用中文字符串匹配做分类，文案漂移会失效。
        is_input_error = result.get("error_type") == "input"
        return JSONResponse(
            {"error": result.get("error", ""), "fail_index": result.get("fail_index"),
             "log": result.get("log", [])},
            status_code=400 if is_input_error else 502,
        )
    return JSONResponse({
        "asset_id": result["asset_id"],
        "url": f"/api/outhan/{video_id}/asset/{result['asset_id']}",
        "duration": result.get("duration", 0),
        "log": result.get("log", []),
    })


@app.post("/api/outhan/{video_id}/build-package")
async def api_outhan_build_package(video_id: str):
    import io as _io
    import zipfile as _zipfile
    proj = _state.get_video(video_id)
    if not proj:
        return JSONResponse({"error": "项目不存在"}, status_code=404)
    from pipeline import outhan_meta as _outhan_meta
    from pipeline import outhan_storage as _outhan_storage
    data = _outhan_meta.get_outhan_data(video_id)
    segs = _outhan_meta.get_segments(video_id)

    safe_title_for_file = (proj.title or video_id).translate(
        str.maketrans({c: "_" for c in '\\/:*?"<>|'})
    )[:60]

    buf = _io.BytesIO()
    with _zipfile.ZipFile(buf, "w", _zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        # 成片（如果有）—— 顶层，最显眼
        final_asset_id = data.get("final_video_asset_id")
        final_asset = None
        if final_asset_id:
            final_asset = _outhan_storage.get_asset(video_id, final_asset_id)
            if final_asset:
                try:
                    zf.writestr(f"{safe_title_for_file}-final.mp4",
                                _outhan_storage.read_asset_bytes(final_asset))
                except Exception: pass
        # references
        for a in _outhan_storage.list_assets(video_id, "reference"):
            try:
                zf.writestr(f"references/{__import__('os').path.basename(a['path'])}",
                            _outhan_storage.read_asset_bytes(a))
            except Exception: pass
        # first frame
        if data.get("first_frame_asset_id"):
            ff = _outhan_storage.get_asset(video_id, data["first_frame_asset_id"])
            if ff:
                try: zf.writestr("frames/first.png", _outhan_storage.read_asset_bytes(ff))
                except Exception: pass
        # segments
        for s in segs:
            idx = f"{s['index']:02d}"
            if s.get("video_asset_id"):
                a = _outhan_storage.get_asset(video_id, s["video_asset_id"])
                if a:
                    try:
                        zf.writestr(f"segments/segment-{idx}-{s.get('engine','jimeng')}.mp4",
                                    _outhan_storage.read_asset_bytes(a))
                    except Exception: pass
            if s.get("tail_frame_asset_id"):
                a = _outhan_storage.get_asset(video_id, s["tail_frame_asset_id"])
                if a:
                    try: zf.writestr(f"frames/segment-{idx}-tail.png", _outhan_storage.read_asset_bytes(a))
                    except Exception: pass
            if s.get("audio_asset_id"):
                a = _outhan_storage.get_asset(video_id, s["audio_asset_id"])
                if a:
                    try: zf.writestr(f"audio/segment-{idx}.mp3", _outhan_storage.read_asset_bytes(a))
                    except Exception: pass
            if s.get("script"):
                zf.writestr(f"scripts/segment-{idx}-script.txt", s["script"])

        # manifest
        final_filename = f"{safe_title_for_file}-final.mp4" if final_asset else None
        kind_label = "纯画面（无配音）" if data.get("video_kind") == "silent" else "有对白 / 旁白"
        lines = [
            f"# {proj.title}", "",
            f"**主题**：{data.get('theme') or '（未填）'}",
            f"**视频类型**：{kind_label}",
            f"**目标总时长**：{data.get('target_duration')} 秒",
            f"**默认引擎**：{data.get('video_engine') or 'jimeng'}",
            f"**生成时间**：{__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
        ]
        if final_filename:
            lines += [
                f"## 🎬 成片：`{final_filename}`",
                "",
                "已通过一键合成（Fish 音频替换 + 段间 concat）。可直接发布；如需精修，下面 segments/audio 是分段原料。",
                "",
            ]
        lines += [
            "## 文件夹结构",
            "- `references/` 用户上传的参考图",
            "- `frames/first.png` 项目首帧",
            "- `frames/segment-NN-tail.png` 各段尾帧",
            "- `segments/segment-NN-<engine>.mp4` 各段视频（原始，未替换音轨）",
            "- `audio/segment-NN.mp3` 各段 Fish 配音",
            "- `scripts/segment-NN-script.txt` 各段 Fish S1 配音稿",
            "",
            "## 分段清单", "",
        ]
        for s in segs:
            idx = f"{s['index']:02d}"
            lines.append(f"### 段 {idx}（{s.get('engine','jimeng')} · {s.get('duration',5)}s）")
            if s.get("scene"): lines.append(f"**场景**：{s['scene']}")
            if s.get("prompt"): lines.append(f"**提示词**：{s['prompt']}")
            if s.get("script"): lines.append(f"**配音稿**：{s['script']}")
            lines.append(f"**视频**：{'segments/segment-' + idx + '-' + s.get('engine','jimeng') + '.mp4' if s.get('video_asset_id') else '（未生成）'}")
            lines.append(f"**音频**：{'audio/segment-' + idx + '.mp3' if s.get('audio_asset_id') else '（未生成）'}")
            lines.append("")
        lines += ["## 进剪辑前检查", "",
                  "- [ ] 所有段视频齐全",
                  "- [ ] 所有段音频齐全（或决定纯素材）",
                  "- [ ] 段顺序按文件名排好",
                  "- [ ] 接力帧衔接自然",
                  "", "---", "", "由 Outhan（one-shot 集成版）自动生成。"]
        zf.writestr("manifest.md", "\n".join(lines))

    safe_name = (proj.title or video_id).translate(str.maketrans({c: "_" for c in '\\/:*?"<>|'}))[:60]
    # [AI-NOTE] 2026-05-07: 中文文件名走 RFC 5987 filename*=UTF-8''xxx 格式；
    # 同时保留 ASCII 兜底 filename= 给老浏览器（用 video_id）。
    import urllib.parse as _urlp
    encoded_name = _urlp.quote(f"{safe_name}.zip", safe="")
    fallback_name = f"{video_id}.zip"
    from fastapi.responses import Response as _R
    return _R(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{fallback_name}"; '
                f"filename*=UTF-8''{encoded_name}"
            )
        },
    )


@app.delete("/api/project/{video_id}")
async def api_project_delete(video_id: str):
    """删除项目：清 DB 表 + 清 audio/outputs 文件。MOCK 项目不允许删（前端会过滤）。"""
    if _state.get_video(video_id) is None:
        raise HTTPException(404, "project not found in DB (mock projects can't be deleted)")
    tables = ["project_topic", "project_topic_pool", "project_sources",
              "project_style", "project_style_bible", "project_shot_table",
              "script_drafts", "script_working", "script_archives",
              "script_full", "snippet_library", "tasks", "videos"]
    with _state.get_conn() as conn:
        for t in tables:
            try:
                conn.execute(f"DELETE FROM {t} WHERE video_id = ?", (video_id,))
            except Exception:
                pass
    # 清文件
    for d in (_DATA_DIR / "audio" / video_id,
              _DATA_DIR / "outputs" / "_clips" / video_id):
        _shutil.rmtree(d, ignore_errors=True)
    mp4 = _DATA_DIR / "outputs" / f"{video_id}.mp4"
    if mp4.exists():
        mp4.unlink()
    return JSONResponse({"ok": True})


@app.get("/project/{video_id}", response_class=HTMLResponse)
async def project_redirect(video_id: str):
    proj = get_project(video_id)
    if not proj:
        return RedirectResponse(url="/", status_code=303)
    return RedirectResponse(url=f"/project/{video_id}/{proj['next_step']}", status_code=303)


# ==================== ① 选题相关 Mock 数据 ====================

# 5 个风格预设
STYLE_PRESETS = [
    {"id": "serious", "emoji": "🎓", "name": "严肃科普",
     "desc": "央视纪录片风 · 准确严谨",
     "color": "#1E40AF", "bg": "#EEF4FF",
     "dims": {"formal": 85, "humor": 10, "pace": 30, "density": 75, "emotion": 25, "speak": 20},
     "sample": "根据国家统计局 2024 年数据，居民消费价格指数同比上涨 0.2%，这意味着……",
     "voice": "青年男声 · 沉稳",
     "visual": "冷色调 · 数据图表为主"},
    {"id": "humor", "emoji": "😎", "name": "幽默段子",
     "desc": "网感金融 · 自带段子",
     "color": "#F48A0C", "bg": "#FFF8EC",
     "dims": {"formal": 25, "humor": 90, "pace": 80, "density": 45, "emotion": 85, "speak": 90},
     "sample": "股票跌的时候，你哭的死去活来，别人却在偷着乐？",
     "voice": "青年男声 · 活泼",
     "visual": "暖色调 · 表情包+梗图"},
    {"id": "healing", "emoji": "🌿", "name": "治愈温柔",
     "desc": "陪伴式讲解 · 减焦虑",
     "color": "#047857", "bg": "#ECFDF5",
     "dims": {"formal": 45, "humor": 35, "pace": 35, "density": 40, "emotion": 70, "speak": 65},
     "sample": "别急，钱的事情慢慢来。今天我们只聊一件小事——为什么你的存款好像越来越不耐用？",
     "voice": "女声 · 温柔",
     "visual": "柔和渐变 · 手绘风"},
    {"id": "analyst", "emoji": "📊", "name": "华尔街派",
     "desc": "分析师视角 · 数据说话",
     "color": "#0D0D0B", "bg": "#F4F3EE",
     "dims": {"formal": 75, "humor": 20, "pace": 55, "density": 85, "emotion": 40, "speak": 35},
     "sample": "从 risk-reward ratio 的角度看，当前做空 TSLA 的赔率并不理想——原因有三个。",
     "voice": "青年男声 · 理性",
     "visual": "深色背景 · 金融图表"},
    {"id": "counter", "emoji": "🎭", "name": "反常识",
     "desc": "认知颠覆 · 钩子炸裂",
     "color": "#DC2626", "bg": "#FEE2E2",
     "dims": {"formal": 40, "humor": 55, "pace": 75, "density": 60, "emotion": 80, "speak": 70},
     "sample": "你以为你的钱是你的？错。银行存款的法律所有权其实属于银行。",
     "voice": "青年男声 · 悬疑",
     "visual": "高对比 · 戏剧化镜头"},
]

# 风格 DNA 的 6 个维度定义
STYLE_DIMS = [
    {"key": "formal",  "label": "人设口吻", "left": "幽默段子", "right": "严肃学术", "icon": "user-round"},
    {"key": "humor",   "label": "幽默感",   "left": "克制",     "right": "段子密集", "icon": "smile"},
    {"key": "pace",    "label": "节奏",     "left": "平稳",     "right": "炸裂快切", "icon": "zap"},
    {"key": "density", "label": "信息密度", "left": "浅科普",   "right": "烧脑深度", "icon": "brain"},
    {"key": "emotion", "label": "情绪强度", "left": "冷静",     "right": "激昂",     "icon": "flame"},
    {"key": "speak",   "label": "口语化",   "left": "书面",     "right": "大白话",   "icon": "mic"},
]

# Tab 2: 参考模仿 · 模拟粘贴了一个 B 站 URL 的解析结果
MOCK_REFERENCE = {
    "url": "https://www.bilibili.com/video/BV1xxx",
    "platform": "bilibili", "platform_label": "B 站",
    "title": "3分钟讲清做空，熊市也能赚大钱！",
    "author": "财经小白脱盲",
    "duration": "3:24",
    "views": "128.3 万",
    "likes": "4.2 万",
    "cover_emoji": "📈",
    "cover_bg": "linear-gradient(135deg,#DC2626,#0D0D0B)",
    "style_extracted": {
        "tone": "幽默口语",
        "hook_type": "反常识提问",
        "structure": "钩子→类比→案例→金句→CTA",
        "pace": "快节奏·短句",
        "density": "中等",
    },
    "imitation_topics": [
        {"title": "期权是什么？看懂之后你会觉得股票太弱了", "score": 15, "why": "继承原片'反常识认知'钩子"},
        {"title": "融资融券：散户的财富放大器还是绞肉机？", "score": 14, "why": "沿用'利弊双面'结构"},
        {"title": "量化交易为什么能稳赚？一个被误解的真相", "score": 14, "why": "保留'一个真相'标题句式"},
        {"title": "私募基金门槛 100 万，凭什么赚钱比公募多", "score": 13, "why": "沿用数字+反差的钩子"},
        {"title": "VIX 恐慌指数：华尔街的情绪温度计", "score": 12, "why": "学习'专业术语→生活化类比'"},
    ],
}

# Tab 3: 大纲导入 · 模拟用户粘贴了一段笔记
MOCK_OUTLINE = {
    "raw": """最近研究了一下期权。发现大家对期权有几个误解：
1. 期权不是赌博，是对冲工具
2. Delta/Gamma/Vega/Theta 四个希腊字母决定一切
3. 卖方赚 80% 的钱，但一旦崩就是灾难（那个交易员的故事）
4. 散户只玩看涨看跌期权，但其实可以做 covered call 稳赚
还有一些零散的点：时间价值衰减、隐含波动率、波动率微笑……""",
    "extracted_topics": [
        {"title": "期权的4个希腊字母，90% 的人只看懂一个", "from": "笔记第2点", "score": 14},
        {"title": "Covered Call 策略：散户忽视的稳赚方法", "from": "笔记第4点", "score": 13},
        {"title": "期权卖方真的稳赚不赔？那个赔掉 10 亿的交易员", "from": "笔记第3点", "score": 15},
        {"title": "为什么说期权是对冲工具而非赌博", "from": "笔记第1点", "score": 12},
        {"title": "时间价值衰减：期权买方的隐形杀手", "from": "笔记零散点", "score": 11},
    ],
}

# Tab 4: 系列规划 · 模拟规划了 10 集
MOCK_SERIES = {
    "name": "金融小白脱盲 10 讲",
    "subject": "金融入门科普",
    "total_episodes": 10,
    "target_duration": "2-3 分钟",
    "avg_difficulty": 2.6,
    "style_id": "humor",  # 继承风格
    "episodes": [
        {"ep": 1,  "title": "什么是股票？一张纸凭什么值钱",                  "difficulty": 1, "mood": "轻快", "status": "locked"},
        {"ep": 2,  "title": "做空是什么？3 分钟看懂熊市赚钱逻辑",            "difficulty": 2, "mood": "惊讶", "status": "locked"},
        {"ep": 3,  "title": "通货膨胀：为什么你的钱越来越不值钱",              "difficulty": 2, "mood": "焦虑", "status": "locked"},
        {"ep": 4,  "title": "复利的力量：每天存 10 块 20 年后能变多少",        "difficulty": 2, "mood": "惊喜", "status": "draft"},
        {"ep": 5,  "title": "基金 vs 股票：到底该买哪个",                    "difficulty": 3, "mood": "理性", "status": "draft"},
        {"ep": 6,  "title": "ETF 是什么？指数投资的正确打开方式",             "difficulty": 3, "mood": "轻快", "status": "draft"},
        {"ep": 7,  "title": "为什么 A 股散户 7 赔 2 平 1 赚",                "difficulty": 3, "mood": "沉重", "status": "draft"},
        {"ep": 8,  "title": "期权：被误解的金融工具",                        "difficulty": 4, "mood": "好奇", "status": "draft"},
        {"ep": 9,  "title": "期货与期权的区别，一张图讲清",                   "difficulty": 4, "mood": "专注", "status": "draft"},
        {"ep": 10, "title": "从小白到入门：如何搭建你的第一个投资组合",         "difficulty": 3, "mood": "激励", "status": "draft"},
    ],
    "coverage": [
        {"topic": "基础概念", "eps": [1, 3]},
        {"topic": "交易工具", "eps": [2, 5, 6, 8, 9]},
        {"topic": "市场现象", "eps": [7]},
        {"topic": "投资理财", "eps": [4, 10]},
    ],
}

# ============== 灵感 Lab 混搭模式 ==============

# 预置的 5 个灵感源（用户一进页面就看到有内容，便于演示）
MOCK_SOURCES = [
    {
        "sid": "src_kw",
        "kind": "keywords",
        "icon": "hash",
        "color": "#F48A0C", "bg": "#FFF8EC",
        "title": "关键词",
        "summary": "做空 · 熊市 · 金融工具",
        "tags": ["做空", "熊市", "金融工具"],
        "weight": 60,
        "extracted": ["卖空", "空头", "做空机构", "融券", "裸卖空", "VIX", "反指标"],
    },
    {
        "sid": "src_ref1",
        "kind": "reference",
        "icon": "link",
        "color": "#1E40AF", "bg": "#EEF4FF",
        "title": "参考视频 · B站",
        "summary": "3分钟讲清做空，熊市也能赚大钱！",
        "meta": {"platform": "B站", "author": "财经小白脱盲", "duration": "3:24", "views": "128.3万"},
        "weight": 80,
        "extracted": ["反常识提问钩子", "生活化类比", "快节奏短句", "5 段式结构", "段子感收尾"],
        # 真实处理管线 (4 步) — 暂用 mock 但结构完全对应未来真实 API
        "pipeline": [
            {"step": 1, "tool": "yt-dlp",    "label": "抓取元数据+下载视频", "icon": "cloud-download", "status": "done",       "detail": "下载 8.2MB · 原生字幕: 无", "pct": 100, "dur_ms": 3200},
            {"step": 2, "tool": "ffmpeg",    "label": "提取音轨",         "icon": "music-2",        "status": "done",       "detail": "输出 mp3 · 16kHz · 单声道",  "pct": 100, "dur_ms": 850},
            {"step": 3, "tool": "Whisper",   "label": "ASR 转录字幕",     "icon": "mic",            "status": "done",       "detail": "识别 826 字 · 置信度 94%",    "pct": 100, "dur_ms": 14200},
            {"step": 4, "tool": "DeepSeek",  "label": "结构化风格抽取",    "icon": "sparkles",       "status": "done",       "detail": "产出 6 个风格维度 JSON",       "pct": 100, "dur_ms": 2100},
        ],
        # 用户"继承哪些维度"的复选框
        "dimensions": [
            {"key": "hook_type",     "label": "钩子类型",   "value": "反常识提问",  "checked": True},
            {"key": "structure",     "label": "叙事结构",   "value": "钩→类→案→句→CTA", "checked": True},
            {"key": "pace",          "label": "节奏",       "value": "快节奏·短句", "checked": True},
            {"key": "topic",         "label": "题材话题",   "value": "做空/熊市",    "checked": False},
            {"key": "golden",        "label": "具体金句",   "value": "\"牛市靠胆熊市靠脑\"", "checked": False},
            {"key": "cover",         "label": "封面风格",   "value": "红色数字强调",  "checked": True},
        ],
    },
    {
        "sid": "src_note",
        "kind": "note",
        "icon": "file-text",
        "color": "#047857", "bg": "#ECFDF5",
        "title": "我的笔记 · 期权研究",
        "summary": "4 个希腊字母 / Covered Call / 卖方赔掉 10 亿...",
        "raw_excerpt": "期权不是赌博而是对冲工具。Delta/Gamma/Vega/Theta 四个希腊字母决定一切。卖方赚 80% 的钱...",
        "weight": 45,
        "extracted": ["希腊字母定价体系", "Covered Call 策略", "期权卖方风险", "时间价值衰减", "隐含波动率"],
    },
    {
        "sid": "src_prompt",
        "kind": "prompt",
        "icon": "sliders",
        "color": "#6B7280", "bg": "#F4F3EE",
        "title": "额外指令",
        "summary": "避免荐股用语 · 带一个真实人物故事",
        "raw": "避免任何可能被视为荐股的表述；每个选题如果涉及案例，必须带一个真实人物或机构的故事。",
        "weight": 70,
    },
]

# 灵感源类型的元数据（添加栏用） — 规划已提升到顶层模式切换，不再是 Source
SOURCE_TYPES = [
    {"kind": "keywords",  "icon": "hash",       "label": "关键词",   "desc": "3-5 个核心词",     "color": "#F48A0C"},
    {"kind": "reference", "icon": "link",       "label": "参考",    "desc": "URL / 账号 / 文稿", "color": "#1E40AF"},
    {"kind": "note",      "icon": "file-text",  "label": "笔记",    "desc": "粘贴你的大纲/想法", "color": "#047857"},
    {"kind": "prompt",    "icon": "sliders",    "label": "指令",    "desc": "附加 Prompt 约束",  "color": "#6B7280"},
]

# AI Fusion 的"识别与融合"可视化 (阶段 A：题材探索，还未锁风格)
MOCK_FUSION_INSIGHTS = {
    "recognized_intents": [
        {"icon": "📌", "label": "单集创作 · 90 秒左右的金融概念科普"},
        {"icon": "🎯", "label": "核心方向：做空 → 可延伸到期权、融券、做空机制等"},
        {"icon": "🎭", "label": "参考片提取维度：反常识钩子 / 结构 / 节奏（题材+金句未勾选）"},
        {"icon": "📚", "label": "笔记中的期权希腊字母有潜力做单独选题"},
        {"icon": "⚠️", "label": "硬约束：不荐股 · 必带真实案例"},
    ],
    "source_influence": [
        {"sid": "src_kw",     "name": "关键词",       "pct": 28, "color": "#F48A0C"},
        {"sid": "src_ref1",   "name": "参考视频",     "pct": 38, "color": "#1E40AF"},
        {"sid": "src_note",   "name": "笔记",         "pct": 20, "color": "#047857"},
        {"sid": "src_prompt", "name": "指令",         "pct": 14, "color": "#6B7280"},
    ],
    "model": "DeepSeek-V3",
    "latency_ms": 3420,
    "tokens": 6824,
    "cost_cny": 0.18,
    # 阶段说明：当前只生成了"题材候选"，还没定风格
    "stage_note": "仅探索题材 · 风格在下一步定向",
}

# 融合后的题材候选池（阶段 A：纯题材，附"风格光谱"— 告诉用户此题材在各风格下的适配度）
# 每个候选的 style_spectrum 对应 STYLE_PRESETS 的 5 个预设（严肃/幽默/治愈/华尔街派/反常识）
MOCK_FUSED_TOPICS = [
    {"id": "F01", "title": "做空是什么？3 分钟看懂熊市赚钱逻辑",
     "category": "概念", "pain": 5, "surprise": 4, "hook": 5, "difficulty": 3, "score": 14,
     "origins": [
        {"sid": "src_kw",   "label": "关键词: 做空"},
        {"sid": "src_ref1", "label": "参考片·反常识钩子"},
     ],
     "why": "关键词主方向 + 继承参考片的钩子结构",
     "style_spectrum": {"serious": 75, "humor": 92, "healing": 45, "analyst": 68, "counter": 88},
     "best_style": "humor"},
    {"id": "F02", "title": "做空大师贝瑞：2008 年他凭什么赚了 100 亿",
     "category": "人物", "pain": 4, "surprise": 5, "hook": 5, "difficulty": 3, "score": 15,
     "origins": [
        {"sid": "src_kw",     "label": "关键词: 做空"},
        {"sid": "src_ref1",   "label": "参考片·段子感结构"},
        {"sid": "src_prompt", "label": "指令·带真实人物"},
     ],
     "why": "满足指令的真实案例要求 + 钩子炸裂",
     "style_spectrum": {"serious": 88, "humor": 72, "healing": 30, "analyst": 95, "counter": 82},
     "best_style": "analyst"},
    {"id": "F03", "title": "期权的 4 个希腊字母，90% 的人只看懂一个",
     "category": "进阶", "pain": 4, "surprise": 5, "hook": 5, "difficulty": 4, "score": 14,
     "origins": [
        {"sid": "src_note",   "label": "笔记·希腊字母"},
        {"sid": "src_ref1",   "label": "参考片·反常识钩子"},
     ],
     "why": "直接复用你笔记中的核心概念",
     "style_spectrum": {"serious": 82, "humor": 62, "healing": 40, "analyst": 88, "counter": 90},
     "best_style": "counter"},
    {"id": "F04", "title": "Covered Call：散户也能稳赚的期权策略",
     "category": "策略", "pain": 3, "surprise": 5, "hook": 4, "difficulty": 4, "score": 13,
     "origins": [
        {"sid": "src_note",   "label": "笔记·Covered Call"},
        {"sid": "src_prompt", "label": "指令·不荐股"},
     ],
     "why": "笔记中的策略 + 中性表述",
     "style_spectrum": {"serious": 78, "humor": 55, "healing": 72, "analyst": 85, "counter": 60},
     "best_style": "analyst"},
    {"id": "F05", "title": "融券做空被禁，A 股散户为什么做空最难",
     "category": "市场", "pain": 5, "surprise": 4, "hook": 4, "difficulty": 3, "score": 13,
     "origins": [
        {"sid": "src_kw",     "label": "关键词: 做空/金融工具"},
     ],
     "why": "切入 A 股本地化痛感",
     "style_spectrum": {"serious": 70, "humor": 65, "healing": 80, "analyst": 60, "counter": 78},
     "best_style": "healing"},
    {"id": "F06", "title": "期权卖方赚 80% 的钱，那个 10 亿赔光的交易员",
     "category": "人物", "pain": 5, "surprise": 5, "hook": 5, "difficulty": 4, "score": 15,
     "origins": [
        {"sid": "src_note",   "label": "笔记·卖方崩盘"},
        {"sid": "src_ref1",   "label": "参考片·段子感"},
        {"sid": "src_prompt", "label": "指令·真实人物"},
     ],
     "why": "所有 source 最佳融合 · 强烈推荐",
     "style_spectrum": {"serious": 82, "humor": 88, "healing": 35, "analyst": 92, "counter": 95},
     "best_style": "counter"},
]

# 阶段 B：风格定向 · 双栏对比
# 给"做空是什么？"在 2 种不同风格下的预览
MOCK_STYLE_PREVIEW = {
    "topic_title": "做空是什么？3 分钟看懂熊市赚钱逻辑",
    "previews": {
        "humor": {
            "preset_id": "humor",
            "hook": "股票跌的时候，你哭的死去活来，别人却在偷着乐？",
            "opening": "对，他们在做空。说白了就是：高价先借来卖了，等跌了再买回来还，差价白赚。",
            "tone_sample": "就像你借邻居一件 2000 的羽绒服，拿去卖 2000，等到夏天 500 收回来，1500 装进自己口袋。",
            "golden": "牛市靠胆子，熊市靠脑子。",
            "visual_hint": "暖橙色调 · 表情包+梗图",
            "voice_hint": "青年男声 · 活泼",
            "estimated_viral": 85,
        },
        "serious": {
            "preset_id": "serious",
            "hook": "根据证监会融券业务数据，2023 年散户做空参与率不足 3%。为什么？",
            "opening": "做空是投资者预期股价下跌时，向券商借入股票卖出，待股价下跌后买回还券、赚取差价的交易行为。",
            "tone_sample": "以 2008 年次贷危机为例，对冲基金经理迈克尔·贝瑞通过做空次级抵押贷款证券，获利约 1 亿美元。",
            "golden": "做空不是看空，是一种风险对冲工具。",
            "visual_hint": "冷蓝色调 · 数据图表为主",
            "voice_hint": "青年男声 · 沉稳",
            "estimated_viral": 62,
        },
    },
}

# 10 集规划画布 (当创作模式为 series 时)
MOCK_SERIES_CANVAS = {
    "series_name": "金融小白脱盲 10 讲",
    "episodes": [
        {"ep": 1,  "title": "什么是股票？一张纸凭什么值钱",       "difficulty": 1, "mood": {"label": "轻快", "emoji": "🎉", "color": "#F48A0C"}, "dominant": "src_kw",     "topic_area": "基础"},
        {"ep": 2,  "title": "做空是什么？熊市赚钱逻辑",           "difficulty": 2, "mood": {"label": "惊讶", "emoji": "😲", "color": "#DC2626"}, "dominant": "src_kw+ref", "topic_area": "工具"},
        {"ep": 3,  "title": "通胀：为什么你的钱越来越不值钱",      "difficulty": 2, "mood": {"label": "焦虑", "emoji": "😨", "color": "#7C3AED"}, "dominant": "src_kw",     "topic_area": "宏观"},
        {"ep": 4,  "title": "复利的力量：每天 10 块 20 年后",     "difficulty": 2, "mood": {"label": "惊喜", "emoji": "💡", "color": "#059669"}, "dominant": "src_kw",     "topic_area": "投资"},
        {"ep": 5,  "title": "基金 vs 股票，该买哪个",             "difficulty": 3, "mood": {"label": "理性", "emoji": "🧐", "color": "#1E40AF"}, "dominant": "src_kw",     "topic_area": "工具"},
        {"ep": 6,  "title": "ETF 指数投资入门",                  "difficulty": 3, "mood": {"label": "轻快", "emoji": "🎉", "color": "#F48A0C"}, "dominant": "src_kw",     "topic_area": "工具"},
        {"ep": 7,  "title": "A 股散户 7 赔 2 平 1 赚",          "difficulty": 3, "mood": {"label": "沉重", "emoji": "😓", "color": "#6B7280"}, "dominant": "src_ref1",   "topic_area": "市场"},
        {"ep": 8,  "title": "期权的 4 个希腊字母",                "difficulty": 4, "mood": {"label": "好奇", "emoji": "🤔", "color": "#047857"}, "dominant": "src_note",   "topic_area": "进阶"},
        {"ep": 9,  "title": "期权 vs 期货，一图看懂",             "difficulty": 4, "mood": {"label": "专注", "emoji": "🎯", "color": "#1E40AF"}, "dominant": "src_note",   "topic_area": "进阶"},
        {"ep": 10, "title": "搭建你的第一个投资组合",             "difficulty": 3, "mood": {"label": "激励", "emoji": "🔥", "color": "#DC2626"}, "dominant": "src_prompt", "topic_area": "实操"},
    ],
    "coverage": [
        {"area": "基础",  "count": 1, "eps": [1]},
        {"area": "工具",  "count": 3, "eps": [2, 5, 6]},
        {"area": "宏观",  "count": 1, "eps": [3]},
        {"area": "投资",  "count": 1, "eps": [4]},
        {"area": "市场",  "count": 1, "eps": [7]},
        {"area": "进阶",  "count": 2, "eps": [8, 9]},
        {"area": "实操",  "count": 1, "eps": [10]},
    ],
    "callbacks": [
        {"from_ep": 2,  "to_ep": 7,  "note": "贝瑞案例 → A 股散户对比"},
        {"from_ep": 4,  "to_ep": 10, "note": "复利概念 → 投资组合实操"},
        {"from_ep": 8,  "to_ep": 9,  "note": "希腊字母 → 期权 vs 期货"},
    ],
    "difficulty_curve": [1, 2, 2, 2, 3, 3, 3, 4, 4, 3],
}

# Tab 5: Prompt 工程 · 默认模板
MOCK_PROMPT = {
    "system": """你是一二布布金融科普视频的选题 AI。
根据用户输入的关键词和节目风格，生成 20 个候选选题。
每个选题需要：
1. 包含一个"钩子词"（反常识/提问/数字）
2. 符合节目风格设定（{style_name}）
3. 难度适合目标受众（{audience}）
4. 给出 4 维评分（痛感/反常识/钩子/难度）

输出格式：JSON 数组，每项 {id, title, tags, pain, surprise, hook, difficulty, score}""",
    "user_template": "关键词: {keywords}\n风格: {style_name}\n受众: {audience}\n请生成 20 个候选选题",
    "temperature": 0.85,
    "max_candidates": 20,
    "model": "DeepSeek-V3",
    "variables": [
        {"name": "keywords",  "desc": "用户输入的 3-5 个关键词"},
        {"name": "style_name", "desc": "当前锁定的节目风格名称"},
        {"name": "audience",  "desc": "目标受众画像（从风格面板读取）"},
    ],
}


# ==================== ① 选题 ====================
# phase: explore(阶段A·题材探索)  |  direct(阶段B·风格定向)
@app.get("/project/{video_id}/topic", response_class=HTMLResponse)
async def page_topic(request: Request, video_id: str,
                     phase: str = "explore"):
    # 真实数据：从 DB 读出之前保存的 sources / 候选池 / 已选题
    real_sources = _state.load_project_sources(video_id) or DEFAULT_SOURCES.copy()
    real_pool = _state.load_project_topic_pool(video_id) or []
    real_picked = _state.load_project_topic(video_id)
    # 热点工坊带过来的立意（在 banner 渲染）
    _fb = (real_sources or {}).get("forge_brief") if isinstance(real_sources, dict) else None
    _fs = (real_sources or {}).get("forge_supplements") if isinstance(real_sources, dict) else None
    _fg = (real_sources or {}).get("forge_signal") if isinstance(real_sources, dict) else None
    return TEMPLATES.TemplateResponse("step_topic.html", build_ctx(
        request, "topic", video_id,
        topic_pool=MOCK_TOPIC_POOL,
        selected_topic_id="F01",
        active_tab="keywords",
        style_presets=STYLE_PRESETS,
        style_dims=STYLE_DIMS,
        active_style_id="humor",
        reference=MOCK_REFERENCE,
        outline=MOCK_OUTLINE,
        series=MOCK_SERIES,
        prompt_config=MOCK_PROMPT,
        # 灵感 Lab
        sources=MOCK_SOURCES,
        source_types=SOURCE_TYPES,
        fusion=MOCK_FUSION_INSIGHTS,
        fused_topics=MOCK_FUSED_TOPICS,
        # 选题阶段（A 探索 / B 定向）
        phase=phase,
        style_preview=MOCK_STYLE_PREVIEW,
        series_canvas=MOCK_SERIES_CANVAS,
        # 真实持久化数据（覆盖 mock）
        real_sources=real_sources,
        real_pool=real_pool,
        real_picked=real_picked,
        forge_brief=_fb,
        forge_supplements=_fs,
        forge_signal=_fg,
    ))


# ==================== ① 选题 · 真实输入 + AI 生成 ====================

DEFAULT_SOURCES = {
    "keywords": "",
    "notes": "",
    "extra": "",
    "references": [],  # 后续接 yt-dlp / Whisper 真实管线
}


@app.get("/api/source/load")
async def api_source_load(video_id: str):
    s = _state.load_project_sources(video_id) or DEFAULT_SOURCES.copy()
    return JSONResponse({"sources": s})


@app.post("/api/source/save")
async def api_source_save(payload: dict):
    video_id = payload.get("video_id")
    sources = payload.get("sources")
    if not video_id or sources is None:
        raise HTTPException(400, "video_id and sources required")
    _state.save_project_sources(video_id, sources)
    return JSONResponse({"ok": True})


@app.post("/api/topic/generate")
async def api_topic_generate(payload: dict):
    video_id = payload.get("video_id")
    keywords = (payload.get("keywords") or "").strip()
    extra = (payload.get("extra") or "").strip()
    n_topics = int(payload.get("n_topics") or 6)
    intent = (payload.get("intent") or "搞懂概念").strip()
    audience = (payload.get("audience") or "完全小白").strip()
    if not video_id or not keywords:
        raise HTTPException(400, "video_id and keywords required")
    # 把当前输入也持久化（这样刷新页面不丢）
    notes = (payload.get("notes") or "").strip()
    _state.save_project_sources(video_id, {
        "keywords": keywords, "notes": notes, "extra": extra, "references": []
    })
    # 把 notes 也拼进 extra 给 AI
    full_extra = extra
    if notes:
        full_extra = (extra + "\n参考笔记: " + notes).strip()
    result = _topic_generator.generate_fused_topics(video_id, keywords, full_extra, n_topics, intent=intent, audience=audience)
    if result.get("topics"):
        _state.save_project_topic_pool(video_id, result["topics"])
    return JSONResponse(result)


@app.get("/api/topic/pool")
async def api_topic_pool(video_id: str):
    pool = _state.load_project_topic_pool(video_id) or []
    picked = _state.load_project_topic(video_id)
    return JSONResponse({"pool": pool, "picked": picked})


# ── 灵感市集 · 题材库 ──
_TOPIC_LIB_PATH = Path(__file__).resolve().parent.parent / "config" / "topic_library.json"


def _load_topic_library() -> dict:
    """读取并返回题材库 JSON。文件不存在时返回空骨架。"""
    if not _TOPIC_LIB_PATH.exists():
        return {"version": 0, "domains": []}
    try:
        return _json.loads(_TOPIC_LIB_PATH.read_text(encoding="utf-8"))
    except _json.JSONDecodeError:
        return {"version": 0, "domains": [], "error": "topic_library.json 解析失败"}


@app.get("/api/topic_library")
async def api_topic_library():
    return JSONResponse(_load_topic_library())


@app.post("/api/topic/pick")
async def api_topic_pick(payload: dict):
    video_id = payload.get("video_id")
    topic = payload.get("topic")
    if not video_id or not topic:
        raise HTTPException(400, "video_id and topic required")
    _state.save_project_topic(video_id, topic)
    # 同时更新 videos 表的 title，让其他页面能继承
    title = topic.get("title", "")
    if title:
        # 确保 videos 表有这条记录
        if _state.get_video(video_id) is None:
            _state.create_video(video_id, title=title, topic=title)
        else:
            _state.update_video_fields(video_id, title=title, topic=title)
    # 同步把 topic.best_style 作为风格的初值（如果用户还没主动选风格）
    if not _state.load_project_style(video_id):
        best = topic.get("best_style", "humor")
        preset = next((p for p in STYLE_PRESETS if p["id"] == best), STYLE_PRESETS[1])
        _state.save_project_style(video_id, preset)
    # 题材一变，旧文案稿就过期 → 清掉，避免下一页看到陈旧稿
    with _state.get_conn() as conn:
        conn.execute("DELETE FROM script_drafts WHERE video_id = ?", (video_id,))
        conn.execute("DELETE FROM script_working WHERE video_id = ?", (video_id,))
    return JSONResponse({"ok": True, "next": f"/project/{video_id}/script"})


@app.post("/api/style/pick")
async def api_style_pick(payload: dict):
    video_id = payload.get("video_id")
    style_id = payload.get("style_id")
    if not video_id or not style_id:
        raise HTTPException(400, "video_id and style_id required")
    preset = next((p for p in STYLE_PRESETS if p["id"] == style_id), None)
    if not preset:
        raise HTTPException(400, f"unknown style_id: {style_id}")
    _state.save_project_style(video_id, preset)
    # 风格变化 → 同样清旧稿
    with _state.get_conn() as conn:
        conn.execute("DELETE FROM script_drafts WHERE video_id = ?", (video_id,))
        conn.execute("DELETE FROM script_working WHERE video_id = ?", (video_id,))
    return JSONResponse({"ok": True, "style": preset, "next": f"/project/{video_id}/script"})


@app.get("/api/style/load")
async def api_style_load(video_id: str):
    style = _state.load_project_style(video_id)
    return JSONResponse({"style": style})


# ==================== ② 文案（三稿并排 + 工作稿） ====================

def _script_style_for(video_id: str) -> dict:
    """从 DB 读用户选定的风格预设；没选过就默认幽默段子。"""
    style = _state.load_project_style(video_id)
    return style if style else STYLE_PRESETS[1]


def _script_topic_for(video_id: str) -> str:
    """从 DB 读用户选定的题材标题；没选过就用 mock 项目的 title。"""
    picked = _state.load_project_topic(video_id)
    if picked and picked.get("title"):
        return picked["title"]
    proj = get_project(video_id)
    return proj["title"] if proj else video_id


def _script_topic_meta_for(video_id: str) -> dict:
    """返回完整的 topic picked 对象（含 intent/perspective/audience），供文案 agent 继承。"""
    picked = _state.load_project_topic(video_id)
    return picked if isinstance(picked, dict) else {}


@app.get("/project/{video_id}/script", response_class=HTMLResponse)
async def page_script(request: Request, video_id: str):
    style = _script_style_for(video_id)
    drafts = _state.load_script_drafts(video_id) or []
    working = _state.load_script_working(video_id)
    archives = _state.list_script_archives(video_id)
    # 完整文稿状态卡：检测 script_full 是否已生成
    full = _state.load_script_full(video_id)
    full_summary = None
    if full:
        paragraphs = full.get("paragraphs") or []
        char_total = sum(len((p.get("text") or "")) for p in paragraphs)
        full_summary = {
            "duration_sec": full.get("duration_sec"),
            "paragraphs_count": len(paragraphs),
            "char_total": char_total,
            "title": full.get("title") or full.get("framework", {}).get("one_liner", ""),
            "updated_at": full.get("updated_at"),
        }
    topic_meta = _script_topic_meta_for(video_id)
    return TEMPLATES.TemplateResponse("step_script.html", build_ctx(
        request, "script", video_id,
        style_preset=style,
        style_presets=STYLE_PRESETS,
        initial_drafts=drafts,
        initial_working=working,
        archives=archives,
        topic_title=_script_topic_for(video_id),
        topic_meta=topic_meta,
        full_summary=full_summary,
    ))


@app.post("/api/script/generate")
async def api_script_generate(payload: dict):
    video_id = payload.get("video_id")
    if not video_id:
        raise HTTPException(400, "video_id required")
    style = _script_style_for(video_id)
    topic = _script_topic_for(video_id)
    topic_meta = _script_topic_meta_for(video_id)
    result = _script_writer.generate_three_drafts(video_id, topic, style, topic_meta=topic_meta)
    _state.save_script_drafts(video_id, result["drafts"])
    # 首次生成：若没有工作稿，默认用 d1 初始化
    if _state.load_script_working(video_id) is None and result["drafts"]:
        working = _script_writer.build_working_from_draft(result["drafts"][0])
        _state.save_script_working(video_id, working)
    return JSONResponse({
        "drafts": result["drafts"],
        "provider": result["provider"],
        "cost": result["cost"],
        "working": _state.load_script_working(video_id),
    })


@app.post("/api/script/refine")
async def api_script_refine(payload: dict):
    video_id = payload.get("video_id")
    field_label = payload.get("field_label") or payload.get("field") or "字段"
    current_text = payload.get("current_text", "")
    instruction = payload.get("instruction", "")
    if not video_id or not current_text:
        raise HTTPException(400, "video_id and current_text required")
    style = _script_style_for(video_id)
    context = payload.get("context", {"topic": _script_topic_for(video_id)})
    result = _script_writer.refine_field(
        video_id, field_label, current_text, instruction, style, context
    )
    return JSONResponse(result)


@app.get("/api/script/working")
async def api_script_working_get(video_id: str):
    w = _state.load_script_working(video_id)
    return JSONResponse({"working": w})


@app.post("/api/script/working")
async def api_script_working_post(payload: dict):
    video_id = payload.get("video_id")
    working = payload.get("working")
    if not video_id or working is None:
        raise HTTPException(400, "video_id and working required")
    _state.save_script_working(video_id, working)
    return JSONResponse({"ok": True})


@app.post("/api/script/archive")
async def api_script_archive(payload: dict):
    video_id = payload.get("video_id")
    name = payload.get("name") or "未命名存档"
    if not video_id:
        raise HTTPException(400, "video_id required")
    drafts = _state.load_script_drafts(video_id) or []
    working = _state.load_script_working(video_id) or {}
    archive_id = _state.archive_script(video_id, name, drafts, working)
    return JSONResponse({"archive_id": archive_id, "name": name})


@app.post("/api/script/full/generate")
async def api_script_full_generate(payload: dict):
    video_id = payload.get("video_id")
    outline = payload.get("outline")
    duration_sec = int(payload.get("duration_sec") or 60)
    pace = float(payload.get("pace_chars_per_sec") or 5.5)
    if not video_id or not outline:
        raise HTTPException(400, "video_id and outline required")
    style = _script_style_for(video_id)
    topic = _script_topic_for(video_id)
    topic_meta = _script_topic_meta_for(video_id)
    result = _script_writer.generate_full_script(video_id, topic, style, outline, duration_sec, pace,
                                                  topic_meta=topic_meta)
    if result.get("full"):
        # 给每段加 marked 字段（前端勾选/标记用）
        for p in result["full"].get("paragraphs", []):
            p.setdefault("marked", None)  # null | 'delete' | 'rewrite'
            p.setdefault("edited", False)
        _state.save_script_full(video_id, outline.get("draft_id", "?"), duration_sec, result["full"])
    return JSONResponse(result)


@app.get("/api/script/full/load")
async def api_script_full_load(video_id: str, duration_sec: Optional[int] = None):
    full = _state.load_script_full(video_id, duration_sec)
    fulls = _state.list_script_fulls(video_id)
    return JSONResponse({"full": full, "available": fulls})


@app.post("/api/script/full/save")
async def api_script_full_save(payload: dict):
    video_id = payload.get("video_id")
    full = payload.get("full")
    if not video_id or not full:
        raise HTTPException(400, "video_id and full required")
    duration_sec = int(full.get("duration_sec") or 60)
    outline_id = full.get("outline_id") or "?"
    _state.save_script_full(video_id, outline_id, duration_sec, full)
    return JSONResponse({"ok": True})


@app.post("/api/script/full/refine")
async def api_script_full_refine(payload: dict):
    video_id = payload.get("video_id")
    full = payload.get("full")
    idx = int(payload.get("paragraph_idx", -1))
    instruction = payload.get("instruction", "")
    if not video_id or not full or idx < 0:
        raise HTTPException(400, "video_id, full, paragraph_idx required")
    style = _script_style_for(video_id)
    result = _script_writer.refine_paragraph(video_id, full, idx, instruction, style)
    return JSONResponse(result)


# ---------- 断句审核 ----------

@app.post("/api/segments/generate")
async def api_segments_generate(payload: dict):
    """LLM 语义切分 full_script.paragraphs → 写回 units 字段。"""
    video_id = payload.get("video_id")
    duration_sec = payload.get("duration_sec")
    if not video_id:
        raise HTTPException(400, "video_id required")
    result = _segmenter.run(video_id, duration_sec=duration_sec)
    return JSONResponse(result)


@app.post("/api/segments/save")
async def api_segments_save(payload: dict):
    """用户在 UI 编辑 units 后落盘，同时把 subtitle_units 同步回分镜表。"""
    video_id = payload.get("video_id")
    paragraphs = payload.get("paragraphs")   # list[{id, units, text, ...}]
    if not video_id or not paragraphs:
        raise HTTPException(400, "video_id and paragraphs required")
    ok = _segmenter.persist(video_id, paragraphs)

    # 保存后自动把最新 units → 重新打包 → 更新分镜表 subtitle_units
    # 不重新生成分镜（表情/背景/元素不变），只刷新字幕切分信息
    try:
        _sync_subtitle_units_to_shot_table(video_id, paragraphs)
    except Exception:
        pass   # 非致命，失败静默；下次重生成分镜时自然修复

    return JSONResponse({"ok": ok})


def _sync_subtitle_units_to_shot_table(video_id: str, paragraphs: list[dict]) -> None:
    """把 script_full 里保存好的 units 重新打包，写回现有分镜表的 subtitle_units。
    不触动任何其他字段（表情、背景、元素、audio_path 全部保留）。
    """
    table_dict = _state.load_project_shot_table(video_id)
    if not table_dict:
        return

    shots = table_dict.get("shots") or []
    if not shots:
        return

    # 对每个段落重新打包，得到 (packed_text, [units]) 列表
    all_packed: list[list[str]] = []   # 每个元素是 [unit1, unit2, ...]
    for para in paragraphs:
        para_text = (para.get("text") or "").strip()
        llm_units = [u.strip() for u in (para.get("units") or []) if str(u).strip()]
        if llm_units:
            packed = _shot_table_agent._pack_units_with_tracking(llm_units)
        elif para_text:
            sents = _shot_table_agent._split_sentences(para_text)
            packed = _shot_table_agent._pack_units_with_tracking(sents)
        else:
            continue
        for _, unit_list in packed:
            all_packed.append(unit_list)

    # 按索引对应写回（打包数量和 shot 数量不一致时只更新能对应的）
    for i, unit_list in enumerate(all_packed):
        if i < len(shots):
            shots[i]["subtitle_units"] = unit_list

    table_dict["shots"] = shots
    _state.save_project_shot_table(video_id, table_dict)


# ---------- 灵感片段库 ----------

@app.post("/api/snippet/save")
async def api_snippet_save(payload: dict):
    video_id = payload.get("video_id")
    kind = payload.get("kind")  # hook / golden / cta / case / paragraph
    text = (payload.get("text") or "").strip()
    source_note = payload.get("source_note") or ""
    if not video_id or not kind or not text:
        raise HTTPException(400, "video_id, kind, text required")
    snippet_id = _state.save_snippet(video_id, kind, text, source_note)
    return JSONResponse({"ok": True, "snippet_id": snippet_id})


@app.get("/api/snippet/list")
async def api_snippet_list(video_id: str, kind: Optional[str] = None):
    snippets = _state.list_snippets(video_id, kind)
    return JSONResponse({"snippets": snippets})


@app.delete("/api/snippet/{snippet_id}")
async def api_snippet_delete(snippet_id: int):
    _state.delete_snippet(snippet_id)
    return JSONResponse({"ok": True})


@app.post("/api/script/restore")
async def api_script_restore(payload: dict):
    archive_id = payload.get("archive_id")
    if not archive_id:
        raise HTTPException(400, "archive_id required")
    snap = _state.load_script_archive(int(archive_id))
    if not snap:
        raise HTTPException(404, "archive not found")
    _state.save_script_drafts(snap["video_id"], snap["drafts"])
    _state.save_script_working(snap["video_id"], snap["working"])
    return JSONResponse({"ok": True, "drafts": snap["drafts"], "working": snap["working"]})


@app.get("/project/{video_id}/script/full", response_class=HTMLResponse)
async def page_script_full(request: Request, video_id: str, duration: int = 60):
    style = _script_style_for(video_id)
    full = _state.load_script_full(video_id, duration)
    available = _state.list_script_fulls(video_id)
    snippets = _state.list_snippets(video_id)
    return TEMPLATES.TemplateResponse("step_script_full.html", build_ctx(
        request, "script", video_id,
        style_preset=style,
        topic_title=_script_topic_for(video_id),
        full=full,
        available=available,
        snippets=snippets,
        duration=duration,
    ))


# ==================== ③ 画风锁定 ====================
def _ensure_video_row(video_id: str) -> None:
    if _state.get_video(video_id) is None:
        picked = _state.load_project_topic(video_id)
        title = (picked or {}).get("title") or video_id
        _state.create_video(video_id, title=title, topic=title)


def _collect_anchors(bible: dict) -> list[dict]:
    """从 asset_library 按 bible.style_anchors 取锚点素材。"""
    out = []
    for aid in (bible.get("style_anchors") or []):
        a = _ASSET_LIB.get(aid)
        if a is not None:
            out.append({
                "id": a.id, "file": a.file, "description": a.description,
                "tags": a.tags, "kind": a.kind,
            })
    return out


# 配音 / 字幕 预设
# Edge-TTS（免费）+ Fish Audio 热门中文音色
_EDGE_VOICE_PRESETS: list[dict] = [
    {"id": v["id"], "name": v["name"], "desc": v["desc"], "provider": "edge_tts"}
    for v in _edge_tts.list_voices()
]
_FISH_VOICE_PRESETS: list[dict] = _fish_audio.list_voices_sync()   # 启动时拉取，失败静默
VOICE_PRESETS: list[dict] = _EDGE_VOICE_PRESETS + _FISH_VOICE_PRESETS

SUBTITLE_PRESETS = [
    {"id": "classic_white", "name": "经典白描", "desc": "白字黑描边 · 通用",
     "sample": "AaBb",  "bg": "#1C1B18", "color": "#FAFAF7"},
    {"id": "bold_orange",   "name": "金句大字", "desc": "粗金橙填充 · 冲击",
     "sample": "AaBb",  "bg": "#F48A0C", "color": "#1C1B18"},
    {"id": "popup_red",     "name": "综艺弹窗", "desc": "红底白字 · 夸张",
     "sample": "AaBb",  "bg": "#D93A2C", "color": "#FFFFFF"},
]

def _scan_bgm_presets() -> list[dict]:
    """扫 /bgm/ 目录下所有 mp3，第一个永远是"无 BGM"占位。
    每次画风页加载都重扫，user 增删 mp3 立即生效。"""
    presets: list[dict] = [
        {"id": "none", "name": "无 BGM", "desc": "干声不加背景音乐 · 适合严肃科普", "url": ""},
    ]
    if _BGM_DIR.exists():
        for f in sorted(_BGM_DIR.glob("*.mp3")):
            stem = f.stem  # "1" / "2" 等
            presets.append({
                "id": f"bgm_{stem}",
                "name": f"BGM {stem}",
                "desc": f"{f.name} · {f.stat().st_size // 1024} KB",
                "url": f"/bgm/{f.name}",
                "filename": f.name,
            })
    return presets


VOICE_ID_MIGRATION = {
    "female_warm":   "zh-CN-XiaoxiaoNeural",
    "male_steady":   "zh-CN-YunjianNeural",
    "neutral_light": "zh-CN-YunxiNeural",
}


def _merge_default_bible(saved_bible: dict | None) -> dict:
    """旧 bible（第二批之前存的）缺少 identity 字段时，用默认值补齐。"""
    default = _style_agent.load_style_bible()
    if saved_bible is None:
        return default
    # 补齐缺字段
    for k in ("ip_library_id", "element_library_ids", "background_source",
              "bg_id", "bg_title_overlay", "bg_title_text", "bg_title_url",
              "voice_id", "subtitle_style", "bgm_id", "test_intent",
              "visual_style_tags", "palette", "composition",
              "negative_prompt_global", "aspect_ratio", "size"):
        saved_bible.setdefault(k, default.get(k))
    # 旧 voice_id（批 3a 的占位）迁移到 Edge-TTS id
    if saved_bible.get("voice_id") in VOICE_ID_MIGRATION:
        saved_bible["voice_id"] = VOICE_ID_MIGRATION[saved_bible["voice_id"]]
    if "confirmed" not in saved_bible or not isinstance(saved_bible["confirmed"], dict):
        saved_bible["confirmed"] = default.get("confirmed", {}).copy()
    for key in ("ip_library", "element_library", "background", "voice_subtitle", "bgm"):
        saved_bible["confirmed"].setdefault(key, False)
    return saved_bible


@app.get("/project/{video_id}/style", response_class=HTMLResponse)
async def page_style(request: Request, video_id: str):
    saved = _state.load_project_style_bible(video_id)
    bible = _merge_default_bible(saved["bible"] if saved else None)

    anchors = _collect_anchors(bible)
    # 从 saved.images 里拿最近一次 L3 测试图（第一张）
    test_image = None
    if saved and saved.get("images"):
        first = saved["images"][0]
        test_image = {
            "ok": first.get("ok", False),
            "url": first.get("url"),
            "intent": first.get("intent", ""),
            "prompt": first.get("prompt", ""),
            "cost": first.get("cost", 0.0),
            "provider": first.get("provider", ""),
            "error": first.get("error"),
        }
    return TEMPLATES.TemplateResponse("step_style.html", build_ctx(
        request, "style", video_id,
        bible=bible,
        anchors=anchors,
        test_image=test_image,
        ip_libraries=_ASSET_LIB.list_ip_libraries(),
        element_libraries=_ASSET_LIB.list_element_libraries(),
        voice_presets=VOICE_PRESETS,
        subtitle_presets=SUBTITLE_PRESETS,
        bgm_presets=_scan_bgm_presets(),
    ))


@app.post("/api/style/bible/save")
async def api_style_bible_save(payload: dict):
    """保存 bible（visual_style_tags / test_intent 编辑后落盘到 project_style_bible）。"""
    video_id = payload.get("video_id")
    bible = payload.get("bible")
    if not video_id or not bible:
        raise HTTPException(400, "video_id and bible required")
    _ensure_video_row(video_id)
    prev = _state.load_project_style_bible(video_id) or {"images": [], "total_cost_cny": 0.0, "locked": False}
    _state.save_project_style_bible(
        video_id, bible, prev.get("images", []),
        total_cost_cny=prev.get("total_cost_cny", 0.0),
        locked=prev.get("locked", False),
    )
    return JSONResponse({"ok": True})


@app.post("/api/style/test_element")
async def api_style_test_element(payload: dict):
    """生一张 L3 测试图，检验画风约束是否到位。"""
    video_id = payload.get("video_id")
    if not video_id:
        raise HTTPException(400, "video_id required")
    _ensure_video_row(video_id)
    bible = payload.get("bible") or _style_agent.load_style_bible()
    override_intent = (payload.get("intent") or "").strip() or None
    result = _style_agent.generate_test_element(
        video_id, bible=bible, override_intent=override_intent, library=_ASSET_LIB,
    )
    # 把 test image 写进 project_style_bible.images[0]，方便下次加载
    prev = _state.load_project_style_bible(video_id) or {}
    img_record = {
        "slot": "test",
        "intent": result["intent"],
        "prompt": result["prompt"],
        "url": result.get("url"),
        "cost": result["cost"],
        "provider": result["provider"],
        "ok": result["ok"],
        "error": result.get("error"),
    }
    _state.save_project_style_bible(
        video_id, bible, [img_record],
        total_cost_cny=result["cost"],
        locked=prev.get("locked", False),
    )
    return JSONResponse(result)


@app.post("/api/style/bible/lock")
async def api_style_bible_lock(payload: dict):
    video_id = payload.get("video_id")
    locked = bool(payload.get("locked", True))
    if not video_id:
        raise HTTPException(400, "video_id required")
    ok = _state.set_project_style_bible_locked(video_id, locked)
    if not ok:
        raise HTTPException(404, "style bible not found; test first")
    return JSONResponse({"ok": True, "locked": locked})


# ==================== ④ 分镜 ====================
def _slot_to_view(slot: _shot_table.BeatSlot) -> dict:
    a = _ASSET_LIB.get(slot.asset_id) if slot.asset_id and not slot.asset_id.startswith("MISSING:") else None
    raw_file = (a.file if a else None) or slot.image_url
    return {
        "asset_id": slot.asset_id,
        "asset_kind": slot.asset_kind,
        "file": _absolute_asset_url(raw_file) if raw_file else None,
        "description": (a.description if a else None) or "",
        "character": (a.meta.get("character") if a else None) or "",
        "missing": slot.asset_id.startswith("MISSING:") if slot.asset_id else False,
        "image_url": slot.image_url,
    }


def _shot_to_view(s: _shot_table.Shot, *, bible: dict | None = None) -> dict:
    """Shot 序列化为前端视图字典（含缩略 URL + beats 解析）。

    若 bible 传入且 bg_title_url 存在 + shot.background_id 等于 bible.bg_id，
    背景缩略用"已 P 标题"的版本（与最终视频渲染保持一致）。
    """
    bg_a = _ASSET_LIB.get(s.background_id) if s.background_id else None
    expr_a = _ASSET_LIB.get(s.expression_id) if s.expression_id else None
    bg_url = bg_a.file if bg_a else None
    if bible and (bible.get("bg_title_url") or "").strip() and \
       s.background_id and s.background_id == (bible.get("bg_id") or "").strip():
        bg_url = bible["bg_title_url"]
    # 自动 ensure beats（旧 shot 没 beats 字段时兜底成 1 center beat）
    s = _shot_table.ensure_beats(s)
    beats_view = [{
        "duration_ratio": b.duration_ratio,
        "layout": b.layout,
        "left": _slot_to_view(b.left),
        "right": _slot_to_view(b.right),
    } for b in s.beats]
    return {
        "shot_id": s.shot_id,
        "section": s.section,
        "text": s.text,
        "estimated_duration": s.estimated_duration,
        "scene_tags": list(s.scene_tags),
        # 背景（整 shot 共用，画风锁定）
        "background_id": s.background_id,
        "background_file": bg_url,
        "background_desc": bg_a.description if bg_a else None,
        # 双 beat 视觉（新主路径）
        "beats": beats_view,
        # 兼容字段（旧 picker / 老 UI 引用，可逐步删）
        "expression_id": s.expression_id,
        "expression_file": expr_a.file if expr_a else None,
        "expression_desc": expr_a.description if expr_a else None,
        "expression_character": expr_a.meta.get("character") if expr_a else None,
        "visual_form": getattr(s, "visual_form", "stock"),
        "insets_count": len(s.element_insets),
        "has_missing": any(x.id.startswith("MISSING:") for x in s.element_insets),
        "has_image": any(bool(x.image_url) for x in s.element_insets),
        "has_video": any(bool(x.video_url) for x in s.element_insets),
    }


def _backfill_default_bg(video_id: str, table: _shot_table.ShotTable, bible: dict) -> None:
    """老 shot table 留下的 BG_OFFICE_01 / BG_HOME_01 等乱跳值 → 兜底回写为
    bible.bg_id。仅当 shot.background_id 不在当前素材库或为空时才改；用户
    手动切到合法的另一张 bg 不会被覆盖。"""
    locked = (bible.get("bg_id") or "").strip()
    if not locked:
        return
    valid_ids = {a.id for a in _ASSET_LIB.all("backgrounds")}
    changed = False
    for s in table.shots:
        bid = (s.background_id or "").strip()
        if not bid or bid not in valid_ids:
            s.background_id = locked
            changed = True
    if changed:
        _state.save_project_shot_table(video_id, table.to_dict())


@app.get("/project/{video_id}/shots", response_class=HTMLResponse)
async def page_shots(request: Request, video_id: str):
    raw = _state.load_project_shot_table(video_id)
    table = _shot_table.ShotTable.from_dict(raw) if raw else None
    saved_bible = _state.load_project_style_bible(video_id)
    bible = _merge_default_bible((saved_bible or {}).get("bible"))
    if table is not None:
        _backfill_default_bg(video_id, table, bible)
    full_script = _state.load_script_full(video_id)
    has_full_script = bool(full_script)

    # Stale 检测：分镜的 source_full_id 与当前最新 full_script.full_id 比对
    is_stale = False
    stale_reason = ""
    if table and full_script:
        current_full_id = full_script.get("full_id")
        bound_full_id = getattr(table, "source_full_id", None)
        if current_full_id is not None:
            if bound_full_id is None:
                is_stale = True
                stale_reason = "分镜表创建时未绑定文案版本（建议重新生成以同步）"
            elif bound_full_id != current_full_id:
                is_stale = True
                stale_reason = f"文案已更新（当前 #{current_full_id}，分镜基于旧版 #{bound_full_id}）"

    shots_view = [_shot_to_view(s, bible=bible) for s in (table.shots if table else [])]
    return TEMPLATES.TemplateResponse("step_shots.html", build_ctx(
        request, "shots", video_id,
        shots_view=shots_view,
        has_shot_table=table is not None,
        has_full_script=has_full_script,
        total_duration=(table.total_duration_estimated if table else 0.0),
        scene_tags_closed=sorted(list(_shot_table.SCENE_TAGS)),
        is_stale=is_stale,
        stale_reason=stale_reason,
    ))


@app.post("/api/shots/generate")
async def api_shots_generate(payload: dict):
    """从 full_script 生成 shot_table。"""
    video_id = payload.get("video_id")
    duration_sec = payload.get("duration_sec")  # 可选
    if not video_id:
        raise HTTPException(400, "video_id required")
    _ensure_video_row(video_id)
    full = _state.load_script_full(video_id, duration_sec=duration_sec)
    if not full:
        raise HTTPException(400, "找不到 full_script，请先到文案页生成完整文稿")

    proj = get_project(video_id) or {}
    title = proj.get("title") or video_id
    topic = (_state.load_project_topic(video_id) or {}).get("title") or title
    saved_bible = _state.load_project_style_bible(video_id)
    bible = _merge_default_bible((saved_bible or {}).get("bible"))

    table = _shot_table_agent.generate_shot_table(
        video_id=video_id, title=title, topic=topic,
        full_script=full, bible=bible, library=_ASSET_LIB,
    )
    _state.save_project_shot_table(video_id, table.to_dict())
    return JSONResponse({
        "ok": True,
        "shots": [_shot_to_view(s, bible=bible) for s in table.shots],
        "total_duration": table.total_duration_estimated,
    })


@app.post("/api/shots/update")
async def api_shots_update(payload: dict):
    """局部更新一条 shot（text / scene_tags / visual_form / estimated_duration）."""
    video_id = payload.get("video_id")
    shot_id = payload.get("shot_id")
    updates = payload.get("updates") or {}
    if not video_id or not shot_id:
        raise HTTPException(400, "video_id and shot_id required")
    raw = _state.load_project_shot_table(video_id)
    if not raw:
        raise HTTPException(404, "shot_table 不存在")
    table = _shot_table.ShotTable.from_dict(raw)
    target = next((s for s in table.shots if s.shot_id == shot_id), None)
    if target is None:
        raise HTTPException(404, f"shot {shot_id} not found")

    if "text" in updates:
        target.text = str(updates["text"]).strip()
    if "estimated_duration" in updates:
        target.estimated_duration = float(updates["estimated_duration"])
    if "scene_tags" in updates:
        tags = [t for t in (updates["scene_tags"] or []) if t in _shot_table.SCENE_TAGS]
        target.scene_tags = tags[:3]
    if "visual_form" in updates:
        _shot_table_agent.apply_visual_form(target, updates["visual_form"])
    if "expression_id" in updates:
        new_id = (updates["expression_id"] or "").strip()
        # 校验存在
        if new_id and _ASSET_LIB.get(new_id) is None:
            raise HTTPException(400, f"expression {new_id} not in library")
        target.expression_id = new_id
    if "background_id" in updates:
        new_id = (updates["background_id"] or "").strip()
        if new_id and _ASSET_LIB.get(new_id) is None:
            raise HTTPException(400, f"background {new_id} not in library")
        target.background_id = new_id

    # ---- 双 beat 更新 ----
    # updates 形如：{"beat_slot": {"beat_index": 0, "side": "left"|"right",
    #                              "asset_id": "EXPR_xxx" | "EL_xxx" | "" }}
    # asset_kind 由 asset_library 自动推断（expression / element）
    if "beat_slot" in updates:
        bs = updates["beat_slot"] or {}
        bi = int(bs.get("beat_index", 0))
        side = bs.get("side") or "left"
        new_aid = (bs.get("asset_id") or "").strip()
        target = _shot_table.ensure_beats(target)
        if not (0 <= bi < len(target.beats)):
            raise HTTPException(400, f"beat_index {bi} out of range")
        if side not in ("left", "right"):
            raise HTTPException(400, "side must be 'left' or 'right'")
        # 推断 kind（从 asset 在哪个集合里）
        kind_inferred = ""
        if new_aid:
            a = _ASSET_LIB.get(new_aid)
            if a is None:
                raise HTTPException(400, f"asset {new_aid} not in library")
            # 确定 kind（粗判定：表情 ID 前缀 EXPR_，元素 EL_，BG_）
            if new_aid.startswith("EXPR_") or new_aid.startswith("EXP_"):
                kind_inferred = "expression"
            elif new_aid.startswith("EL_") or new_aid.startswith("ELE_"):
                kind_inferred = "element"
        new_slot = _shot_table.BeatSlot(asset_id=new_aid, asset_kind=kind_inferred)
        if side == "left":
            target.beats[bi].left = new_slot
        else:
            target.beats[bi].right = new_slot
        # 自动调 layout：双槽都有 → left_right；只有一个 → center
        b = target.beats[bi]
        b.layout = "left_right" if (b.left.asset_id and b.right.asset_id) else "center"

    if "beat_layout" in updates:
        # {"beat_layout": {"beat_index": 0, "layout": "center"|"left_right"}}
        bl = updates["beat_layout"] or {}
        bi = int(bl.get("beat_index", 0))
        layout = bl.get("layout")
        if layout not in ("left_right", "center"):
            raise HTTPException(400, "layout must be 'left_right' or 'center'")
        target = _shot_table.ensure_beats(target)
        if 0 <= bi < len(target.beats):
            target.beats[bi].layout = layout

    table.total_duration_estimated = round(
        sum(s.estimated_duration for s in table.shots), 1
    )
    _state.save_project_shot_table(video_id, table.to_dict())
    saved_bible = _state.load_project_style_bible(video_id)
    bible = _merge_default_bible((saved_bible or {}).get("bible"))
    return JSONResponse({
        "ok": True,
        "shot": _shot_to_view(target, bible=bible),
        "total_duration": table.total_duration_estimated,
    })


@app.get("/api/shots/picker_options")
async def api_shots_picker_options(video_id: str, shot_id: str, kind: str):
    """返回当段可选素材候选（带缩略）。

    kind:
      - "expressions" → 当前 IP 库下，按 shot.scene_tags 排序的表情候选 + 全量 fallback
      - "backgrounds" → 全量背景
    """
    if kind not in ("expressions", "backgrounds", "elements"):
        raise HTTPException(400, "kind must be 'expressions' / 'backgrounds' / 'elements'")
    raw = _state.load_project_shot_table(video_id)
    if not raw:
        raise HTTPException(404, "shot_table not found")
    table = _shot_table.ShotTable.from_dict(raw)
    target = next((s for s in table.shots if s.shot_id == shot_id), None)
    if target is None:
        raise HTTPException(404, "shot not found")

    saved_bible = _state.load_project_style_bible(video_id)
    bible = _merge_default_bible((saved_bible or {}).get("bible"))
    raw_ip_id = (bible.get("ip_library_id") or "").strip()
    ip_libs = (_ASSET_LIB.meta or {}).get("ip_libraries", {})
    ip_meta = ip_libs.get(raw_ip_id)
    ip_char = (ip_meta or {}).get("character_name") or raw_ip_id

    # scene_tags → element categories 映射（用于 elements 自动排序）
    _SCENE_TAG_TO_ELEM_CATS: dict[str, list[str]] = {
        "震惊":  ["散户心理", "紧张"],
        "兴奋":  ["行情上涨", "财富盈利", "积极"],
        "担忧":  ["风险陷阱", "消极", "紧张"],
        "崩溃":  ["爆仓清盘", "财富亏损", "消极"],
        "期待":  ["做多入场", "行情上涨", "积极"],
        "调侃":  ["调侃", "散户心理"],
        "困惑":  ["散户心理", "紧张", "数据图表"],
        "沉思":  ["数据图表", "央行政策", "散户心理"],
        "得意":  ["财富盈利", "行情上涨", "积极"],
        "平静":  ["数据图表", "积极", "存款储蓄"],
    }

    if kind == "expressions":
        # 1. 优先：按 scene_tags 命中（character 限定）
        ranked = _ASSET_LIB.search_expressions(target.scene_tags, character=ip_char)
        ranked_ids = {a.id for a in ranked}
        # 2. 补充：character 名下其他表情
        rest = [a for a in _ASSET_LIB.all("expressions")
                if a.meta.get("character") == ip_char and a.id not in ranked_ids]
        items = ranked + rest
        # 3. 其他 IP 兜底
        other_chars = [a for a in _ASSET_LIB.all("expressions")
                       if a.meta.get("character") != ip_char]
        # 收集所有出现的 scene_tags，用于前端筛选栏
        all_expr_tags: list[str] = []
        seen_tags: set[str] = set()
        for a in items + other_chars[:30]:
            for t in (a.meta.get("scene_tags") or []):
                if t not in seen_tags:
                    all_expr_tags.append(t)
                    seen_tags.add(t)
        return JSONResponse({
            "ok": True,
            "current": target.expression_id,
            "shot_tags": list(target.scene_tags),      # 当前段的情绪标签，前端预选用
            "ranked_count": len(ranked),
            "filter_tags": all_expr_tags,              # 可用筛选标签列表
            "items": [{
                "id": a.id, "file": _absolute_asset_url(a.file), "description": a.description,
                "scene_tags": a.meta.get("scene_tags", []),
                "character": a.meta.get("character", ""),
                "intensity": a.meta.get("intensity"),
                "matched": a.id in ranked_ids,
            } for a in items + other_chars[:30]],
        })
    elif kind == "backgrounds":
        bgs = _ASSET_LIB.all("backgrounds")
        return JSONResponse({
            "ok": True,
            "current": target.background_id,
            "items": [{
                "id": a.id, "file": _absolute_asset_url(a.file), "description": a.description,
                "tags": a.tags,
            } for a in bgs],
        })
    else:  # elements — 按 scene_tags → categories 映射自动排序
        # 推导本段应优先的 element categories
        want_cats: set[str] = set()
        for st in (target.scene_tags or []):
            for c in _SCENE_TAG_TO_ELEM_CATS.get(st, []):
                want_cats.add(c)
        els = _ASSET_LIB.all("elements")
        # 排序：命中分类的排前面
        def _el_score(a) -> int:
            cats = set(a.meta.get("categories") or [])
            return len(cats & want_cats)
        ranked_els = sorted(els, key=_el_score, reverse=True)
        ranked_el_ids = {a.id for a in ranked_els if _el_score(a) > 0}
        # 收集所有分类供筛选栏
        all_cats: list[str] = []
        seen_cats: set[str] = set()
        for a in ranked_els:
            for c in (a.meta.get("categories") or []):
                if c not in seen_cats:
                    all_cats.append(c)
                    seen_cats.add(c)
        # want_cats 是由 scene_tags 映射出的「推荐元素分类」，直接作为 shot_tags
        # 让前端筛选栏高亮并自动预选对应分类（而非原始情绪标签）
        # 按 want_cats 中出现顺序排在 filter_tags 最前面，方便自动预选第一个
        ordered_cats: list[str] = []
        for st in (target.scene_tags or []):
            for c in _SCENE_TAG_TO_ELEM_CATS.get(st, []):
                if c not in ordered_cats:
                    ordered_cats.append(c)
        # filter_tags: 推荐分类排前，其余分类随后
        priority = set(ordered_cats)
        rest_cats = [c for c in all_cats if c not in priority]
        final_filter_tags = ordered_cats + rest_cats

        return JSONResponse({
            "ok": True,
            "current": "",
            "shot_tags": ordered_cats,            # 映射后的推荐元素分类（供前端高亮+自动预选）
            "ranked_count": len(ranked_el_ids),
            "filter_tags": final_filter_tags,     # 推荐分类排最前
            "items": [{
                "id": a.id, "file": _absolute_asset_url(a.file),
                "description": a.description,
                "categories": a.meta.get("categories", []),
                "matched": a.id in ranked_el_ids,
            } for a in ranked_els],
        })


def _absolute_asset_url(file_path: str) -> str:
    """规整 catalog 里 file 字段成浏览器可加载的 URL：
    - 已是绝对 URL（http/https）→ 直通（七牛云 / OSS 场景）
    - 已是 / 开头 → 直通（站内绝对路径）
    - 是相对路径（'data/assets/...'）→ 加 / 前缀，让浏览器在任何上下文页都能解析到 /data/...
    """
    if not file_path:
        return ""
    if file_path.startswith(("http://", "https://", "/")):
        return file_path
    return "/" + file_path


@app.post("/api/shots/reset")
async def api_shots_reset(payload: dict):
    """清空 shot_table（下游 L3 生图也作废）."""
    video_id = payload.get("video_id")
    if not video_id:
        raise HTTPException(400, "video_id required")
    with _state.get_conn() as conn:
        conn.execute("DELETE FROM project_shot_table WHERE video_id = ?", (video_id,))
    return JSONResponse({"ok": True})


# ==================== ⑤ 素材（分镜段 × 图层矩阵） ====================
def _seed_demo_shot_table(video_id: str) -> _shot_table.ShotTable | None:
    """为演示项目（V0001）建一张真实 ShotTable，混合库存命中 + MISSING AI 兜底。"""
    if video_id != "V0001":
        return None
    proj = get_project(video_id)
    title = (proj or {}).get("title") or video_id
    t = _shot_table.create_empty(video_id, title=title)

    specs = [
        dict(section="钩子", text="股票跌了，居然也能赚钱？",
             dur=5.0, tags=["震惊"], bg="BG_OFFICE_01", expr="EXPR_SHOCK_01",
             insets=[("EL_ARROW_DOWN_MBE", "polaroid", "top_right")]),
        dict(section="共鸣", text="你可能听过做空，但这次我们拆开看",
             dur=6.0, tags=["困惑"], bg="BG_HOME_01", expr="EXPR_THINK_01",
             insets=[]),
        dict(section="知识点", text="做空 = 先借股票卖出，等跌了再买回来还回去",
             dur=8.0, tags=["沉思"], bg="BG_OFFICE_01", expr="EXPR_THINK_01",
             insets=[
                 ("EL_ARROW_DOWN_MBE", "polaroid", "top_right"),
                 ("MISSING: 一块复古手写价格牌，3000 划掉改成 2000，木质边框", "sticky_note", "bottom_left"),
             ]),
        dict(section="知识点", text="3000 借一股，跌到 2000 买回，净赚 1000",
             dur=9.0, tags=["兴奋"], bg="BG_HOME_01", expr="EXPR_MONEY_EYES_01",
             insets=[
                 ("EL_MONEY_BAG_MBE", "polaroid", "bottom_left"),
                 ("MISSING: 一张股票 K 线图急剧下跌，大红色箭头贯穿", "tv_screen", "top_right"),
             ]),
        dict(section="金句", text="看懂做空的人，才不怕熊市",
             dur=4.0, tags=["得意"], bg="BG_HOME_01", expr="EXPR_MONEY_EYES_01",
             insets=[("EL_ARROW_UP_MBE", "circle_window", "top_right")]),
        dict(section="CTA", text="点关注，下期讲融券成本",
             dur=3.0, tags=["平静"], bg="BG_HOME_01", expr="EXPR_THINK_01",
             insets=[]),
    ]
    for i, s in enumerate(specs):
        insets = [
            _shot_table.ElementInset(id=iid, frame_style=fs, position=pos, enter_offset=0.3)
            for (iid, fs, pos) in s["insets"]
        ]
        t.shots.append(_shot_table.Shot(
            shot_id=f"S{i+1:02d}",
            section=s["section"],
            text=s["text"],
            estimated_duration=s["dur"],
            scene_tags=s["tags"],
            background_id=s["bg"],
            expression_id=s["expr"],
            element_insets=insets,
        ))
    t.total_duration_estimated = sum(s["dur"] for s in specs)
    return t


def _load_or_empty_shot_table(video_id: str) -> _shot_table.ShotTable:
    data = _state.load_project_shot_table(video_id)
    if data:
        return _shot_table.ShotTable.from_dict(data)
    # 首次访问：为演示项目播种一张
    seeded = _seed_demo_shot_table(video_id)
    if seeded is not None:
        _state.save_project_shot_table(video_id, seeded.to_dict())
        return seeded
    proj = get_project(video_id)
    title = (proj or {}).get("title") or video_id
    return _shot_table.create_empty(video_id, title=title)


def _save_shot_table(table: _shot_table.ShotTable) -> None:
    _state.save_project_shot_table(table.video_id, table.to_dict())


@app.get("/project/{video_id}/assets", response_class=HTMLResponse)
async def page_assets(request: Request, video_id: str):
    table = _load_or_empty_shot_table(video_id)
    saved_bible = _state.load_project_style_bible(video_id)
    bible = _merge_default_bible((saved_bible or {}).get("bible"))
    confirmed_all = bool(bible.get("confirmed")) and all(
        bible["confirmed"].get(k) for k in
        ("ip_library", "element_library", "background", "voice_subtitle")
    )
    queue = _material_agent.build_task_queue(table, _ASSET_LIB, bible=bible)
    return TEMPLATES.TemplateResponse("step_assets.html", build_ctx(
        request, "assets", video_id,
        queue=queue,
        bible=bible,
        bible_confirmed=confirmed_all,
        voice_presets=VOICE_PRESETS,
        subtitle_presets=SUBTITLE_PRESETS,
    ))


@app.get("/api/assets/queue")
async def api_assets_queue(video_id: str):
    table = _load_or_empty_shot_table(video_id)
    saved_bible = _state.load_project_style_bible(video_id)
    bible = _merge_default_bible((saved_bible or {}).get("bible"))
    return JSONResponse(_material_agent.build_task_queue(table, _ASSET_LIB, bible=bible))


def _resolve_voice(bible_voice_id: str | None) -> tuple[str, str]:
    """按 bible 的 voice_id 解析真实 TTS 音色，返回 (voice_id, provider)。
    优先级：Edge-TTS → Fish Audio → 兜底晓晓。"""
    if bible_voice_id:
        if _edge_tts.voice_exists(bible_voice_id):
            return bible_voice_id, "edge_tts"
        if _fish_audio.voice_exists(bible_voice_id, _FISH_VOICE_PRESETS):
            return bible_voice_id, "fish_audio"
    return "zh-CN-XiaoxiaoNeural", "edge_tts"


def _audio_output_path(video_id: str, shot_id: str) -> Path:
    dir_ = _DATA_DIR / "audio" / video_id
    dir_.mkdir(parents=True, exist_ok=True)
    return dir_ / f"{shot_id}.mp3"


def _audio_url_for(video_id: str, shot_id: str) -> str:
    return f"/data/audio/{video_id}/{shot_id}.mp3"


async def _synthesize_one_shot(table, shot, bible) -> dict:
    """合成一个 shot 的配音 mp3，成功返回 {ok, audio_url, path, bytes, voice, source}."""
    voice, provider = _resolve_voice((bible or {}).get("voice_id"))
    out = _audio_output_path(table.video_id, shot.shot_id)
    if provider == "fish_audio":
        r = await _fish_audio.synthesize_async(shot.text, voice, out)
    else:
        r = await _edge_tts.synthesize_async(shot.text, voice, out)
    if not r.get("ok"):
        return {"ok": False, "error": r.get("error"), "shot_id": shot.shot_id}
    shot.audio_clip_path = str(out.relative_to(_DATA_DIR.parent))
    return {
        "ok": True, "shot_id": shot.shot_id,
        "audio_url": _audio_url_for(table.video_id, shot.shot_id),
        "bytes": r.get("bytes"), "voice": voice, "source": provider,
    }


@app.post("/api/assets/audio/generate")
async def api_assets_audio_generate(payload: dict):
    """真调 Edge-TTS 合成单段配音 mp3 → data/audio/{video_id}/{shot_id}.mp3。"""
    video_id = payload.get("video_id")
    shot_id = payload.get("shot_id")
    if not video_id or not shot_id:
        raise HTTPException(400, "video_id and shot_id required")
    table = _load_or_empty_shot_table(video_id)
    target = next((s for s in table.shots if s.shot_id == shot_id), None)
    if target is None:
        raise HTTPException(404, f"shot {shot_id} not found")

    saved_bible = _state.load_project_style_bible(video_id)
    bible = _merge_default_bible((saved_bible or {}).get("bible"))
    res = await _synthesize_one_shot(table, target, bible)
    if not res.get("ok"):
        return JSONResponse({"ok": False, "error": res.get("error"), "shot_id": shot_id},
                             status_code=200)
    _save_shot_table(table)
    return JSONResponse({
        "ok": True,
        "shot_id": shot_id,
        "audio_url": res["audio_url"],
        "duration": target.estimated_duration,
        "voice": res["voice"],
        "bytes": res["bytes"],
        "source": res.get("source", "edge_tts"),
    })


@app.post("/api/assets/audio/generate_all")
async def api_assets_audio_generate_all(payload: dict):
    """批量合成所有段配音（跳过已有）。"""
    video_id = payload.get("video_id")
    force = bool(payload.get("force"))
    if not video_id:
        raise HTTPException(400, "video_id required")
    table = _load_or_empty_shot_table(video_id)
    saved_bible = _state.load_project_style_bible(video_id)
    bible = _merge_default_bible((saved_bible or {}).get("bible"))
    results = []
    for s in table.shots:
        if s.audio_clip_path and not force:
            results.append({"ok": True, "shot_id": s.shot_id, "skipped": True,
                            "audio_url": _audio_url_for(video_id, s.shot_id)})
            continue
        res = await _synthesize_one_shot(table, s, bible)
        results.append(res)
    _save_shot_table(table)
    ok_count = sum(1 for r in results if r.get("ok"))
    return JSONResponse({
        "ok": ok_count == len(results),
        "total": len(results),
        "success": ok_count,
        "results": results,
    })


@app.post("/api/tts/preview")
async def api_tts_preview(payload: dict):
    """合成一小段试听 mp3，结果缓存到 data/audio/preview/，重复请求直接返回缓存。"""
    voice_id  = (payload.get("voice_id") or "").strip()
    provider  = (payload.get("provider") or "edge_tts").strip()
    if not voice_id:
        raise HTTPException(400, "voice_id required")

    # 试听文本（短、能体现声线特色）
    PREVIEW_TEXT = "大家好，这是我的声音试听样本，希望你喜欢这个声线。"

    preview_dir = _DATA_DIR / "audio" / "preview"
    preview_dir.mkdir(parents=True, exist_ok=True)
    # voice_id 可能含特殊字符，安全化成文件名
    safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in voice_id)
    out = preview_dir / f"{safe_id}.mp3"

    # 有缓存直接返回
    if out.exists() and out.stat().st_size > 0:
        return JSONResponse({"ok": True, "url": f"/data/audio/preview/{safe_id}.mp3", "cached": True})

    # 合成
    if provider == "fish_audio":
        r = await _fish_audio.synthesize_async(PREVIEW_TEXT, voice_id, out)
    else:
        r = await _edge_tts.synthesize_async(PREVIEW_TEXT, voice_id, out)

    if not r.get("ok"):
        return JSONResponse({"ok": False, "error": r.get("error")}, status_code=200)
    return JSONResponse({"ok": True, "url": f"/data/audio/preview/{safe_id}.mp3", "cached": False})


@app.post("/api/assets/l3/generate")
async def api_assets_l3_generate(payload: dict):
    video_id = payload.get("video_id")
    shot_id = payload.get("shot_id")
    inset_index = payload.get("inset_index")
    intent = (payload.get("intent") or "").strip()
    if not video_id or not shot_id or inset_index is None or not intent:
        raise HTTPException(400, "video_id, shot_id, inset_index, intent required")
    _ensure_video_row(video_id)

    saved_bible = _state.load_project_style_bible(video_id)
    bible = (saved_bible or {}).get("bible") or _style_agent.load_style_bible()
    prompt = _style_agent.compose_image_prompt(intent, bible, library=_ASSET_LIB)

    task_id = _state.start_task(
        video_id, agent="material_l3_image", shot_id=shot_id,
        input_data={"intent": intent, "inset_index": inset_index},
    )
    r = _ark_client.generate_image(prompt, size=bible.get("size", "2K"))
    _state.finish_task(
        task_id, success=r["ok"],
        output={"url": r.get("url"), "provider": r["provider"]},
        cost_cny=r["cost"],
        error=(r.get("error") or "")[:500] if not r["ok"] else None,
    )

    # 回写 shot_table：把生成产物挂到 ElementInset 的 image_url / image_prompt 字段
    # id 保持为 "MISSING: 描述" 不动（描述本身是下次重生的种子意图）
    if r["ok"] and r.get("url"):
        table = _load_or_empty_shot_table(video_id)
        for s in table.shots:
            if s.shot_id == shot_id and 0 <= int(inset_index) < len(s.element_insets):
                ins = s.element_insets[int(inset_index)]
                ins.image_url = r["url"]
                ins.image_prompt = prompt
                ins.image_cost = r["cost"]
                # 重生时清掉老视频
                ins.video_url = None
                ins.video_task_id = None
                ins.video_cost = 0.0
                break
        _save_shot_table(table)

    return JSONResponse({
        "ok": r["ok"],
        "url": r.get("url"),
        "cost": r["cost"],
        "provider": r["provider"],
        "prompt": prompt,
        "error": r.get("error"),
    })


@app.post("/api/assets/l3/upgrade_video")
async def api_assets_l3_upgrade_video(payload: dict):
    video_id = payload.get("video_id")
    shot_id = payload.get("shot_id")
    inset_index = payload.get("inset_index")
    image_url = payload.get("image_url")
    motion_prompt = (payload.get("motion_prompt") or "").strip()
    if not video_id or not shot_id or inset_index is None or not image_url:
        raise HTTPException(400, "video_id, shot_id, inset_index, image_url required")
    _ensure_video_row(video_id)
    if not motion_prompt:
        motion_prompt = "镜头轻微推进或摇动，保持主体清晰不变"
    r = _ark_client.create_video_task(image_url, motion_prompt, duration=5, resolution="1080p")
    if not r["ok"]:
        return JSONResponse({"ok": False, "error": r["error"]}, status_code=200)
    # 落到 shot_table：video_task_id
    table = _load_or_empty_shot_table(video_id)
    for s in table.shots:
        if s.shot_id == shot_id and 0 <= int(inset_index) < len(s.element_insets):
            s.element_insets[int(inset_index)].video_task_id = r["task_id"]
            break
    _save_shot_table(table)

    _state.start_task(
        video_id, agent="material_l3_video", shot_id=shot_id,
        input_data={"inset_index": inset_index, "task_id": r["task_id"]},
        provider=r["provider"],
    )
    return JSONResponse({"ok": True, "task_id": r["task_id"], "provider": r["provider"]})


@app.get("/api/assets/l3/video_status")
async def api_assets_l3_video_status(task_id: str, video_id: Optional[str] = None,
                                      shot_id: Optional[str] = None,
                                      inset_index: Optional[int] = None):
    r = _ark_client.get_video_task(task_id)
    # 若携带上下文且已 succeeded，持久化到 shot_table
    if r.get("status") == "succeeded" and r.get("video_url") and video_id and shot_id and inset_index is not None:
        table = _load_or_empty_shot_table(video_id)
        for s in table.shots:
            if s.shot_id == shot_id and 0 <= int(inset_index) < len(s.element_insets):
                ins = s.element_insets[int(inset_index)]
                ins.video_url = r["video_url"]
                ins.video_cost = r.get("cost", 0.0)
                break
        _save_shot_table(table)
    return JSONResponse(r)


# （旧 ⑤ 配音页已合并进素材页的配音 Tab · 路由删除）
@app.get("/project/{video_id}/audio")
async def page_audio_redirect(video_id: str):
    """旧链接回到素材页配音 Tab。"""
    return RedirectResponse(url=f"/project/{video_id}/assets", status_code=302)


# ==================== ⑥ 剪辑 ====================
# 简单的 compose 进度 · 用 in-memory dict（单进程 server 可用）
_EDIT_PROGRESS: dict[str, dict] = {}


def _edit_status_for(video_id: str) -> dict:
    mp4 = _DATA_DIR / "outputs" / f"{video_id}.mp4"
    has_mp4 = mp4.exists() and mp4.stat().st_size > 0
    progress = _EDIT_PROGRESS.get(video_id)
    return {
        "has_mp4": has_mp4,
        "mp4_url": f"/data/outputs/{video_id}.mp4" if has_mp4 else None,
        "mp4_bytes": mp4.stat().st_size if has_mp4 else 0,
        "progress": progress,
    }


@app.get("/project/{video_id}/edit", response_class=HTMLResponse)
async def page_edit(request: Request, video_id: str):
    table = _load_or_empty_shot_table(video_id)
    saved_bible = _state.load_project_style_bible(video_id)
    bible = _merge_default_bible((saved_bible or {}).get("bible"))
    queue = _material_agent.build_task_queue(table, _ASSET_LIB, bible=bible)
    status = _edit_status_for(video_id)
    publish_meta = _publish_meta_response(table)
    return TEMPLATES.TemplateResponse("step_edit.html", build_ctx(
        request, "edit", video_id,
        queue=queue,
        bible=bible,
        status=status,
        shots_count=len(table.shots),
        publish_meta=publish_meta,
    ))


@app.post("/api/edit/compose")
async def api_edit_compose(payload: dict):
    """同步合成最终 mp4。MVP：阻塞几十秒到几分钟。"""
    video_id = payload.get("video_id")
    if not video_id:
        raise HTTPException(400, "video_id required")
    table = _load_or_empty_shot_table(video_id)
    if not table.shots:
        raise HTTPException(400, "shot_table 为空，先到分镜页确认")

    _EDIT_PROGRESS[video_id] = {"running": True, "steps": []}

    def _on_log(entry):
        state = _EDIT_PROGRESS.setdefault(video_id, {"running": True, "steps": []})
        state["steps"].append(entry)

    r = _video_editor.compose_final(
        video_id, shot_table=table, library=_ASSET_LIB, on_log=_on_log,
    )
    _EDIT_PROGRESS[video_id]["running"] = False
    _EDIT_PROGRESS[video_id]["result"] = r
    return JSONResponse(r)


@app.get("/api/edit/status")
async def api_edit_status(video_id: str):
    return JSONResponse(_edit_status_for(video_id))


# -------- 发布信息（标题/简介/话题）-----------
def _save_publish_meta(video_id: str, table: _shot_table.ShotTable) -> None:
    _state.save_project_shot_table(video_id, table.to_dict())


def _publish_meta_response(table: _shot_table.ShotTable) -> dict:
    """统一格式给前端：当前显示的 + 池子大小 + 索引。"""
    pool = list(table.publish_meta_pool or [])
    idx = max(0, min(table.publish_meta_index or 0, len(pool) - 1)) if pool else 0
    current = pool[idx] if pool else None
    return {
        "ok": True,
        "current": current,
        "pool_size": len(pool),
        "index": idx,
        "exhausted": (len(pool) > 0 and idx >= len(pool) - 1),
    }


@app.get("/api/edit/publish_meta")
async def api_publish_meta_get(video_id: str):
    """读现有发布信息池。池为空时不自动生成（让用户主动点'生成发布信息'，避免无谓 LLM 调用）。"""
    table = _load_or_empty_shot_table(video_id)
    return JSONResponse(_publish_meta_response(table))


@app.post("/api/edit/publish_meta/regenerate")
async def api_publish_meta_regenerate(payload: dict):
    """调 LLM 生新一批备选（覆盖旧池），index 重置到 0。"""
    video_id = payload.get("video_id")
    if not video_id:
        raise HTTPException(400, "video_id required")
    n = int(payload.get("n") or 3)
    table = _load_or_empty_shot_table(video_id)
    if not table.shots:
        raise HTTPException(400, "shot_table 为空，先到分镜页确认")
    result = _publish_meta.generate_meta_batch(video_id, table, n=n)
    if result.get("error"):
        return JSONResponse({"ok": False, "error": result["error"]}, status_code=502)
    table.publish_meta_pool = result["metas"]
    table.publish_meta_index = 0
    # 同步把当前选中的写到 publish_title/description/tags（兼容老字段）
    if table.publish_meta_pool:
        m = table.publish_meta_pool[0]
        table.publish_title = m.get("title") or None
        table.publish_description = m.get("description") or None
        table.publish_tags = m.get("tags") or []
    _save_publish_meta(video_id, table)
    out = _publish_meta_response(table)
    out["provider"] = result.get("provider")
    out["cost"] = result.get("cost")
    return JSONResponse(out)


@app.post("/api/edit/publish_meta/cycle")
async def api_publish_meta_cycle(payload: dict):
    """换下一条备选（pool 内循环）。pool 用尽时返回 exhausted=True，让前端提示重新生成。"""
    video_id = payload.get("video_id")
    if not video_id:
        raise HTTPException(400, "video_id required")
    table = _load_or_empty_shot_table(video_id)
    pool = list(table.publish_meta_pool or [])
    if not pool:
        return JSONResponse({"ok": False, "error": "池为空，先点重新生成"}, status_code=400)
    new_idx = (table.publish_meta_index + 1) % len(pool)
    table.publish_meta_index = new_idx
    m = pool[new_idx]
    table.publish_title = m.get("title") or None
    table.publish_description = m.get("description") or None
    table.publish_tags = m.get("tags") or []
    _save_publish_meta(video_id, table)
    return JSONResponse(_publish_meta_response(table))


# ==================== 素材库 ====================
@app.get("/library", response_class=HTMLResponse)
async def library(request: Request):
    """素材库 · 三大分组：IP库 / 场景元素库 / 背景库 —— 全量真实数据。"""

    # ---- IP 库（按 character 聚合 expressions + characters） ----
    ip_libs_meta = (_ASSET_LIB.meta or {}).get("ip_libraries", {})
    ip_groups: dict[str, dict] = {}
    for ch in _ASSET_LIB.all("characters"):
        char_name = ch.meta.get("character") or ch.id
        ip_groups.setdefault(char_name, {
            "character_name": char_name, "cover_file": ch.file,
            "assets": [],
        })
    for expr in _ASSET_LIB.all("expressions"):
        char_name = expr.meta.get("character") or "其他"
        g = ip_groups.setdefault(char_name, {
            "character_name": char_name, "cover_file": expr.file,
            "assets": [],
        })
        g["assets"].append({
            "id": expr.id, "file": expr.file,
            "description": expr.description or expr.meta.get("alt", ""),
            "scene_tags": expr.meta.get("scene_tags", []),
            "intensity": expr.meta.get("intensity"),
            "duration": expr.meta.get("duration"),
        })
    ip_libraries_view = []
    for name, g in ip_groups.items():
        # meta 反查：lib_id + display_name
        lib_id, display_name, desc = name, name, ""
        for lid, lmeta in ip_libs_meta.items():
            if lmeta.get("character_name") == name:
                lib_id = lid
                display_name = lmeta.get("name", name)
                desc = lmeta.get("desc", "")
                break
        # 收集这个 IP 的所有 scene_tag
        scene_tag_set: set[str] = set()
        for it in g["assets"]:
            for t in it["scene_tags"]:
                scene_tag_set.add(t)
        ip_libraries_view.append({
            "id": lib_id,
            "name": display_name,
            "desc": desc,
            "character_name": name,
            "cover_file": g["cover_file"],
            "count": len(g["assets"]),
            "scene_tags": sorted(scene_tag_set),
            "assets": g["assets"],
        })

    # ---- 场景元素库（按 style group） ----
    el_libs_meta = (_ASSET_LIB.meta or {}).get("element_libraries", {})
    el_groups: dict[str, list] = {}
    for el in _ASSET_LIB.all("elements"):
        key = el.style or "其他"
        el_groups.setdefault(key, []).append(el)
    element_libraries_view = []
    for style_id, elements in el_groups.items():
        lmeta = el_libs_meta.get(style_id, {})
        element_libraries_view.append({
            "id": style_id,
            "name": lmeta.get("name", style_id),
            "desc": lmeta.get("desc", ""),
            "style_tags": lmeta.get("style_tags", []),
            "count": len(elements),
            "assets": [{
                "id": e.id, "file": e.file,
                "description": e.description or e.meta.get("alt", ""),
                "tags": e.tags,
            } for e in elements],
        })

    # ---- 背景库 ----
    backgrounds_view = [{
        "id": b.id, "file": b.file,
        "description": b.description,
        "tags": b.tags,
    } for b in _ASSET_LIB.all("backgrounds")]

    total_count = (sum(lib["count"] for lib in ip_libraries_view)
                   + sum(lib["count"] for lib in element_libraries_view)
                   + len(backgrounds_view))

    return TEMPLATES.TemplateResponse("library.html", build_ctx(
        request, "library",
        ip_libraries=ip_libraries_view,
        element_libraries=element_libraries_view,
        backgrounds=backgrounds_view,
        total_count=total_count,
        scene_tags_closed=sorted(list(_shot_table.SCENE_TAGS)),
    ))


# ==================== 素材库编辑 API ====================

def _serialize_asset(a) -> dict:
    return {
        "id": a.id,
        "kind": a.kind,
        "file": a.file,
        "description": a.description,
        "tags": a.tags,
        "style": a.style,
        "ip": a.meta.get("ip"),
        "format": a.meta.get("format"),
        "has_alpha": a.meta.get("has_alpha"),
        "scene_tags": a.meta.get("scene_tags", []),
        "scene_tags_ai": a.meta.get("scene_tags_ai", []),
        "scene_tags_ai_rationale": a.meta.get("scene_tags_ai_rationale", ""),
        "categories": a.meta.get("categories", []),
        "categories_ai": a.meta.get("categories_ai", []),
        "categories_ai_rationale": a.meta.get("categories_ai_rationale", ""),
        "intensity": a.meta.get("intensity"),
        "duration": a.meta.get("duration"),
        "character": a.meta.get("character"),
        "source": a.meta.get("source"),
        "needs_review": bool(a.meta.get("needs_review")),
        "reviewed_at": a.meta.get("reviewed_at"),
    }


@app.get("/api/library/list")
async def api_library_list():
    """Flat JSON dump of all assets for client-side filtering."""
    items = []
    for kind in ("backgrounds", "characters", "elements", "expressions"):
        for a in _ASSET_LIB.all(kind):
            items.append(_serialize_asset(a))
    return {
        "ok": True,
        "items": items,
        "stats": _ASSET_LIB.stats(),
        "ips": _ASSET_LIB.list_ips(),
        "scene_tags_closed": sorted(list(_shot_table.SCENE_TAGS)),
        "scene_tag_groups": _shot_table.SCENE_TAG_GROUPS,
        "element_categories_closed": sorted(list(_shot_table.ELEMENT_CATEGORIES)),
        "element_category_groups": _shot_table.ELEMENT_CATEGORY_GROUPS,
    }


@app.patch("/api/library/{asset_id}")
async def api_library_update(asset_id: str, request: Request):
    payload = await request.json()
    a = _ASSET_LIB.update(
        asset_id,
        scene_tags=payload.get("scene_tags"),
        description=payload.get("description"),
        ip=payload.get("ip"),
        meta_patch=payload.get("meta_patch"),
        tags=payload.get("tags"),
    )
    if a is None:
        raise HTTPException(404, f"asset {asset_id} not found")
    return {"ok": True, "asset": _serialize_asset(a)}


@app.post("/api/library/bulk_update")
async def api_library_bulk(request: Request):
    payload = await request.json()
    updates = payload.get("updates") or []
    res = _ASSET_LIB.bulk_update(updates)
    return {"ok": True, **res}


@app.post("/api/library/mark_all_review")
async def api_library_mark_all_review(request: Request):
    """把符合条件的素材全部标记 needs_review=true（让人工重审）。

    payload: {kind?, ip?, only_unreviewed?: bool}.  无条件 = 全库。
    """
    payload = await request.json() if (await request.body()) else {}
    kind = payload.get("kind")
    ip = payload.get("ip")
    updates = []
    for k in ("backgrounds", "characters", "elements", "expressions"):
        if kind and k != kind:
            continue
        for a in _ASSET_LIB.all(k):
            if ip and a.meta.get("ip") != ip:
                continue
            updates.append({"asset_id": a.id, "meta_patch": {"needs_review": True}})
    res = _ASSET_LIB.bulk_update(updates)
    return {"ok": True, "marked": res["ok_count"]}


@app.post("/api/library/snapshot")
async def api_library_snapshot(request: Request):
    """把当前已审过的素材冻结成快照 JSON（不可变存档）。

    生成 data/audit_snapshots/approved_<YYYYMMDD_HHMMSS>.json
    供 video_editor / shot_table 在生成时按 ID 引用。
    """
    import json as _json
    import time as _time
    from datetime import datetime
    snap_dir = _DATA_DIR / "audit_snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "snapshot_at": _time.time(),
        "snapshot_iso": datetime.now().isoformat(),
        "stats": _ASSET_LIB.approval_stats(),
        "approved": {},
    }
    for kind in ("backgrounds", "characters", "elements", "expressions"):
        payload["approved"][kind] = [
            _serialize_asset(a) for a in _ASSET_LIB.approved(kind)
        ]
    name = f"approved_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    fp = snap_dir / name
    fp.write_text(_json.dumps(payload, ensure_ascii=False, indent=2),
                  encoding="utf-8")
    total = sum(len(v) for v in payload["approved"].values())
    return {"ok": True, "file": str(fp.relative_to(_ROOT)),
            "filename": name, "total": total,
            "stats": payload["stats"]}


@app.get("/api/library/snapshots")
async def api_library_snapshots():
    """列出所有已存的审核快照。"""
    snap_dir = _DATA_DIR / "audit_snapshots"
    if not snap_dir.exists():
        return {"ok": True, "snapshots": []}
    snaps = []
    for fp in sorted(snap_dir.glob("approved_*.json"), reverse=True):
        try:
            stat = fp.stat()
            import json as _json
            head = _json.loads(fp.read_text(encoding="utf-8"))
            snaps.append({
                "filename": fp.name,
                "url": f"/data/audit_snapshots/{fp.name}",
                "size_kb": round(stat.st_size / 1024, 1),
                "snapshot_iso": head.get("snapshot_iso"),
                "stats": head.get("stats", {}),
                "total": sum(len(v) for v in (head.get("approved") or {}).values()),
            })
        except Exception:
            continue
    return {"ok": True, "snapshots": snaps}


@app.get("/api/library/audit_log")
async def api_library_audit_log(limit: int = 200):
    """已审过的素材按 reviewed_at 倒序，给审核页面板用。"""
    entries = []
    for kind in ("backgrounds", "characters", "elements", "expressions"):
        for a in _ASSET_LIB.all(kind):
            ts = a.meta.get("reviewed_at")
            if not ts:
                continue
            applied = (a.meta.get("categories") if kind == "elements"
                       else a.meta.get("scene_tags")) or []
            entries.append({
                "id": a.id,
                "kind": a.kind,
                "file": a.file,
                "ts": ts,
                "applied": applied,
                "ip": a.meta.get("ip"),
            })
    entries.sort(key=lambda e: -(e["ts"] or 0))
    return {"ok": True, "entries": entries[:limit]}


@app.get("/api/library/audit_log/export.csv")
async def api_library_audit_log_csv():
    """CSV 导出已审记录。"""
    import csv
    import io
    from fastapi.responses import StreamingResponse
    rows = []
    for kind in ("backgrounds", "characters", "elements", "expressions"):
        for a in _ASSET_LIB.all(kind):
            ts = a.meta.get("reviewed_at")
            if not ts:
                continue
            applied = (a.meta.get("categories") if kind == "elements"
                       else a.meta.get("scene_tags")) or []
            rows.append({
                "id": a.id, "kind": a.kind, "file": a.file,
                "ip": a.meta.get("ip") or "",
                "applied_tags": ",".join(applied),
                "reviewed_at": ts,
                "reviewed_at_iso": __import__("datetime").datetime.fromtimestamp(ts).isoformat(),
                "description": a.description or "",
            })
    rows.sort(key=lambda r: -(r["reviewed_at"] or 0))
    buf = io.StringIO()
    if rows:
        writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=audit_log.csv"},
    )


@app.post("/api/library/{asset_id}/confirm")
async def api_library_confirm(asset_id: str, request: Request):
    """确认入库：写最终 scene_tags / categories / description + needs_review=false."""
    import time as _time
    payload = await request.json()
    meta_patch = {"needs_review": False, "reviewed_at": _time.time()}
    if payload.get("intensity") is not None:
        meta_patch["intensity"] = payload.get("intensity")
    if payload.get("categories") is not None:
        meta_patch["categories"] = payload.get("categories")
    a = _ASSET_LIB.update(
        asset_id,
        scene_tags=payload.get("scene_tags"),
        description=payload.get("description"),
        ip=payload.get("ip"),
        meta_patch=meta_patch,
    )
    if a is None:
        raise HTTPException(404)
    return {"ok": True, "asset": _serialize_asset(a)}


@app.post("/api/library/{asset_id}/delete")
async def api_library_delete(asset_id: str):
    ok = _ASSET_LIB.delete(asset_id)
    if not ok:
        raise HTTPException(404)
    return {"ok": True}


# ---- harvest 任务（在后台异步跑）----
_HARVEST_TASKS: dict[str, dict] = {}


def _run_harvest(task_id: str, queries: list[str], limit: int, kind: str):
    import threading  # noqa: F401  reserved
    from scripts import harvest_yierbubu as _h
    task = _HARVEST_TASKS[task_id]
    task["status"] = "running"
    try:
        lib = _ASSET_LIB
        total = 0
        registered = 0
        for q in queries:
            try:
                items = _h.harvest_query(q, limit)
                total += len(items)
                n = _h.register(items, q, lib, kind)
                registered += n
                task["progress"].append({"query": q, "found": len(items), "new": n})
            except Exception as e:
                task["progress"].append({"query": q, "error": str(e)[:200]})
        task["total_files"] = total
        task["registered"] = registered
        task["status"] = "succeeded"
    except Exception as e:
        task["status"] = "failed"
        task["error"] = str(e)[:300]


@app.post("/api/library/harvest")
async def api_library_harvest(request: Request):
    import uuid
    import threading
    payload = await request.json()
    queries = payload.get("queries") or []
    if isinstance(queries, str):
        queries = [queries]
    queries = [q.strip() for q in queries if q and q.strip()]
    limit = int(payload.get("limit") or 20)
    kind = payload.get("kind") or "expressions"
    if not queries:
        raise HTTPException(400, "queries required")
    task_id = uuid.uuid4().hex[:12]
    _HARVEST_TASKS[task_id] = {
        "task_id": task_id, "status": "queued",
        "queries": queries, "limit": limit, "kind": kind,
        "progress": [],
    }
    th = threading.Thread(target=_run_harvest, args=(task_id, queries, limit, kind),
                          daemon=True)
    th.start()
    return {"ok": True, "task_id": task_id}


@app.get("/api/library/harvest/{task_id}")
async def api_library_harvest_status(task_id: str):
    t = _HARVEST_TASKS.get(task_id)
    if not t:
        raise HTTPException(404)
    return t


# ---- auto-label 任务（Step 5/6 实装；这里先占位）----
_LABEL_TASKS: dict[str, dict] = {}


def _run_auto_label(task_id: str, asset_ids: list[str]):
    from pipeline.agents.material_agent import auto_label_expressions  # 后面会实装
    task = _LABEL_TASKS[task_id]
    task["status"] = "running"
    try:
        results = auto_label_expressions(asset_ids, library=_ASSET_LIB,
                                          on_progress=lambda i, n, r:
                                          task["progress"].append(r))
        task["results"] = results
        task["status"] = "succeeded"
    except Exception as e:
        task["status"] = "failed"
        task["error"] = str(e)[:300]


@app.post("/api/library/auto_label")
async def api_library_auto_label(request: Request):
    import uuid
    import threading
    payload = await request.json()
    asset_ids = payload.get("asset_ids") or []
    if not asset_ids:
        raise HTTPException(400, "asset_ids required")
    task_id = uuid.uuid4().hex[:12]
    _LABEL_TASKS[task_id] = {
        "task_id": task_id, "status": "queued",
        "asset_ids": asset_ids, "progress": [],
    }
    th = threading.Thread(target=_run_auto_label, args=(task_id, asset_ids),
                          daemon=True)
    th.start()
    return {"ok": True, "task_id": task_id}


@app.get("/api/library/auto_label/{task_id}")
async def api_library_auto_label_status(task_id: str):
    t = _LABEL_TASKS.get(task_id)
    if not t:
        raise HTTPException(404)
    return t


@app.get("/library/review", response_class=HTMLResponse)
async def library_review(request: Request):
    """全屏单条审核工作台。"""
    return TEMPLATES.TemplateResponse("library_review.html", build_ctx(
        request, "library",
        scene_tags_closed=sorted(list(_shot_table.SCENE_TAGS)),
        scene_tag_groups=_shot_table.SCENE_TAG_GROUPS,
        element_categories_closed=sorted(list(_shot_table.ELEMENT_CATEGORIES)),
        element_category_groups=_shot_table.ELEMENT_CATEGORY_GROUPS,
    ))


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "version": "0.1.0"}
