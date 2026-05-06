#!/usr/bin/env bash
# 一键初始化项目：装依赖 + 建数据目录 + 初始化 sqlite
# 用法：bash scripts/setup.sh

set -e
cd "$(dirname "$0")/.."

echo "[setup] Python 版本检查 ..."
python3 -c "import sys; assert sys.version_info >= (3, 9), '需要 Python 3.9+'"

echo "[setup] 装依赖 ..."
pip install -r requirements.txt

echo "[setup] 检查系统级依赖 ffmpeg ..."
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "  [!] 没找到 ffmpeg。装一下："
  echo "      macOS:  brew install ffmpeg"
  echo "      Ubuntu: sudo apt install -y ffmpeg"
  echo "      Win:    https://ffmpeg.org/download.html"
  exit 1
fi
echo "  ✓ ffmpeg = $(ffmpeg -version | head -1)"

echo "[setup] 建本地数据目录 ..."
mkdir -p data/assets/expressions/yierbubu
mkdir -p data/assets/elements/TENOR_FINANCE
mkdir -p data/assets/elements/TENOR_GENERAL
mkdir -p data/audio data/cache data/outputs data/voice_samples data/shot_tables
mkdir -p bgm

echo "[setup] 初始化 sqlite ..."
python3 -m pipeline.state

echo "[setup] 检查 .env ..."
if [ ! -f config/.env ]; then
  cp config/.env.example config/.env
  echo "  ! 已复制 config/.env.example → config/.env"
  echo "    编辑 config/.env 填你的 API key 后再启动"
fi

echo
echo "[setup] 完成 ✓"
echo "下一步："
echo "  1. 编辑 config/.env 填 DEEPSEEK_API_KEY / ARK_API_KEY"
echo "  2. 从团队网盘拉素材 → data/assets/ 和 bgm/"
echo "  3. uvicorn web.app:app --reload --port 8765"
