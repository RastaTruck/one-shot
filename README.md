# 一二布布·视频批量生成 AI 管线

金融科普视频的 AI Agent 自动化管线 · **当前 MVP 阶段**。

目标链路：**选题 → 文案 → 分镜 → 图片/表情/配音 → 字幕 → 剪辑 → 成片**。
实装链路：**选题 ✓ → 文案 ✓ → 其余 Agent 未实装**。

## 当前可跑

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. （可选）配置 LLM API
#    pipeline/tools/llm_client.py 读 DEEPSEEK_API_KEY / ANTHROPIC_API_KEY 环境变量。
#    无 key 会走 stub 兜底（演示数据）。建议用法：
#    export DEEPSEEK_API_KEY=sk-xxx

# 3. 启动审核 UI（当前唯一入口）
uvicorn web.app:app --reload
#    浏览器打开 http://localhost:8000
#    可用页面：选题 / 文案大纲 / 完整文稿。分镜及之后的页面是 Mock 展示。
```

## 实装状态（2026-04-22）

| 模块 | 状态 |
|---|---|
| `pipeline/state.py` · 状态机 + SQLite | ✅ 完整 |
| `pipeline/shot_table.py` · 分镜表脊柱 | ✅ 数据模型完整 |
| `pipeline/asset_library.py` · 素材库 | ✅ 完整 |
| `pipeline/tools/llm_client.py` · LLM 接口 | ✅ DeepSeek + Claude + 无 key 降级 |
| `pipeline/agents/topic_generator.py` · 选题 Agent | ✅ 完整 |
| `pipeline/agents/script_writer.py` · 文案 Agent | ✅ 3 版大纲 / 完整文稿 / refine |
| `web/app.py` · FastAPI 审核 UI | ⚠️ 选题+文案接真 API · 其他 Mock |
| `pipeline/orchestrator.py` · 主调度 | ❌ 未实装 |
| `scripts/run_pipeline.py` · CLI 入口 | ❌ 未实装 |
| `scripts/import_assets.py` · 素材入库 | ❌ 未实装 |
| `pipeline/agents/shot_table_agent.py` · 分镜 LLM | ❌ 未实装 |
| `pipeline/agents/image_agent.py` · 图片/元素 | ❌ 未实装 |
| `pipeline/agents/audio_agent.py` · TTS | ❌ 未实装 |
| `pipeline/agents/subtitle_agent.py` · 字幕 | ❌ 未实装 |
| `pipeline/agents/video_editor_agent.py` · 剪辑 | ❌ 未实装 |

**`data/outputs/` 目前为空——管线从未端到端产出过视频。**

## 目录说明

| 路径 | 说明 |
|---|---|
| `pipeline/shot_table.py` | 分镜表（管线脊柱数据模型） |
| `pipeline/state.py` | 状态机 + SQLite schema |
| `pipeline/asset_library.py` | 素材库（backgrounds / characters / elements / expressions） |
| `pipeline/agents/` | 各专业 Agent（目前只有 topic_generator + script_writer） |
| `pipeline/tools/` | 外部 API 封装（目前只有 llm_client） |
| `web/` | FastAPI 审核 UI（6 个工作台路由） |
| `data/pipeline.db` | 项目生命周期 + 任务日志 |
| `data/shot_tables/` `data/audio/` `data/subtitles/` `data/outputs/` | 各阶段产物（目前多数为空） |
| `config/prompts/` | 各 Agent 的 prompt 模板 |
| `config/asset_catalog.json` | 素材元数据索引 |

## 设计要点

- **分镜表是脊柱**：每个 Agent 读几个字段、写几个字段
- **状态机驱动**：SQLite 里存每条视频的 16 态生命周期
- **3 个人工闸门**（状态已定义，UI 未接线）：文案终审 / 素材抽检 / 成片终审
- **Tools 层隔离供应商**：换 API 只改 `pipeline/tools/` 一个文件

完整设计：`/Users/Zhuanz/.claude/plans/staged-shimmying-lollipop.md`
当前实装诊断：`/Users/Zhuanz/.claude/plans/optimized-questing-fern.md`
