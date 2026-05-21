"""热点工坊 · 选题前置模块。

定位：从公开热搜池 + 财经 RSS + 英文长尾源采集信号 → 赛道匹配 + AI 角度生成
     → 立意文档 → 通过现有 /api/inspiration/create-project 接线流向下游 5 步管线。

为什么独立包：避免和现有选题/灵感库逻辑耦合；schema 独立但共用 pipeline.db。

数据流：
    抓取 (sources/) → upsert_signals (storage) → hotspot_signals 表
    UI 读取 → list_recent_signals → 渲染三栏工坊页

施工阶段：
- Phase 1a (本期)：RSS 财经源（ammo 层）单独跑通
- Phase 1b：微博/知乎热搜（timely 层）
- Phase 1c：HN + Google Trends（longtail 层）
- Step 2：赛道匹配 + AI 角度生成
- Step 3：UI 工坊三栏
- Step 4：立意 → notes → create-project 接线

不在本模块的事：
- 自动发布
- 反馈回路（视频数据回流）
- 多语言（先中文 + 英文翻译，不做小语种）
"""
