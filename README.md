# 视频批量生成 AI 管线

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

---

## 团队协作（新成员上手）

### 1. 克隆 & 安装

```bash
git clone <repo-url>
cd 视频批量生成
bash scripts/setup.sh   # 一键装依赖 + 初始化 sqlite
```

### 2. 配置 API key

```bash
cp config/.env.example config/.env
# 编辑 config/.env，填 DEEPSEEK_API_KEY / ARK_API_KEY 等真值
# .env 在 .gitignore，绝不入 git
```

### 3. 同步素材（关键 · 不在 git 里）

二进制素材（panda 表情 / 场景元素 / BGM mp3）**不入 git**——
体量未来可能超 GB，git 不适合存大二进制。

短期：从团队网盘 / U 盘拉一份到本地：
- `data/assets/expressions/yierbubu/*.webp` — IP 表情库
- `data/assets/elements/TENOR_FINANCE/*.webp` + `TENOR_GENERAL/*.webp` — 场景元素
- `bgm/*.mp3` — 背景音乐

长期：把素材搬到 OSS（阿里云 / 腾讯 COS / 七牛 / Cloudflare R2），catalog 引用 URL，免本地同步。这一步还没做。

### 4. 启动开发服务器

```bash
uvicorn web.app:app --reload --port 8765
# 浏览器开 http://localhost:8765
```

### 5. 协作工作流

**代码改动**：标准 git flow，建分支 → PR → review → merge。

**标签审核**（共审表情 / 元素 scene_tags）：
- 改的是 `config/asset_catalog.json` 里某条目的 `meta.scene_tags` / `meta.character` / `meta.needs_review` 等字段
- JSON 文本，git diff 友好 —— 改 + commit + push
- **冲突预防**：多人同审先**按 ID 段预分工**（A 审 EXP_BUBU_SHOCK_*，B 审 EXP_BUBU_THINKING_*），减少同条目被两人改

**新增素材**（抓 / 上传新表情）：
1. `python3 scripts/harvest_yierbubu.py --queries "..."` 或 `python3 scripts/collect_tenor.py ...` 抓回本地
2. `python3 scripts/clean_expression_bg.py` 清白底（`_orig/` 保留原图）
3. catalog.json 自动加新条目 → commit + push catalog
4. **二进制 webp 不入 git** —— 单独传给团队网盘，或等 OSS 接入后自动同步

### 6. 不要 commit 的东西（.gitignore 已挡住）

- `config/.env` — API key
- `data/pipeline.db` — 各人本地项目状态
- `data/assets/` — 素材二进制
- `data/outputs/`, `data/audio/`, `data/cache/`, `**/_orig/`, `**/_trash/` — 生成产物 / 备份
- `bgm/*.mp3` — BGM 二进制
