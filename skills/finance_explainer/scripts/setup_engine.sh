#!/bin/bash
# 新建一个解说片工程：复制引擎模板，node_modules 优先从本机已有 Remotion 4.0.408 工程克隆（APFS cp -c 不占空间），找不到再 npm install。
# 用法：bash setup_engine.sh <项目目录>
set -e
DST="${1:?用法: setup_engine.sh <项目目录>}"
SKILL="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$DST"
rsync -a "$SKILL/templates/engine/" "$DST/"
if [ ! -d "$DST/node_modules" ]; then
  for SRC in "$HOME/Desktop/xiaolin-remake/engine" "$HOME/Desktop/workbuddy-oneshot" "$HOME/Desktop/style-lab"; do
    if [ -f "$SRC/node_modules/remotion/package.json" ] && grep -q '"version": "4.0.408"' "$SRC/node_modules/remotion/package.json"; then
      echo "克隆 node_modules ← $SRC"; cp -cR "$SRC/node_modules" "$DST/" && break
    fi
  done
fi
[ -d "$DST/node_modules" ] || (cd "$DST" && npm install)
# 音效 / 占位配乐：仓库不带第三方音频，用 digital-human-koubo 的 make_assets.py 合成（无版权）
if [ ! -f "$DST/public/sfx/pop.wav" ]; then
  MA="$SKILL/../digital-human-koubo/scripts/make_assets.py"
  PY=""; for p in python3 python3.14 python3.13 python3.12 python3.11; do command -v $p >/dev/null 2>&1 && $p -c "import numpy" 2>/dev/null && PY=$p && break; done
  if [ -f "$MA" ] && [ -n "$PY" ]; then "$PY" "$MA" "$DST/public"; else echo "⚠ 缺音效：装好 numpy 后运行 python3 $MA $DST/public，或自己放 public/sfx/*.wav 与 public/music/bgm.mp3"; fi
fi
cd "$DST" && npx tsc --noEmit -p . && echo "引擎就绪：$DST（素材放 public/，timeline.json 由 build_timeline.py 写入）"
