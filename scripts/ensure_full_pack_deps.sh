#!/usr/bin/env bash
# 完整包 / 离线安装：保证 deps/wheels 覆盖 requirements.txt + get-pip.py
# - 先 ensure_pack_deps（pycryptodome + tos）
# - 若 verify 已通过 → 跳过联网下载（已有完整 wheel）
# - 若未通过 → 执行 prepare_offline.py（需联网）；失败即退出
# - 设 FORCE_PREPARE_OFFLINE=1 可强制重新执行 prepare_offline.py
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="python3"
if ! "$PY" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" 2>/dev/null; then
  PY="python"
fi
echo "==> [full-offline] 1/3 ensure_pack_deps.sh（pycryptodome win + tos）"
bash scripts/ensure_pack_deps.sh

if [ "${FORCE_PREPARE_OFFLINE:-}" = "1" ]; then
  echo "==> [full-offline] FORCE_PREPARE_OFFLINE=1 → prepare_offline.py"
  python3 scripts/prepare_offline.py --target windows
elif "$PY" scripts/verify_offline_wheels.py 2>/dev/null; then
  echo "==> [full-offline] 2/3 deps/wheels 已满足 requirements，跳过 prepare_offline（需强制重下请设 FORCE_PREPARE_OFFLINE=1）"
else
  echo "==> [full-offline] 2/3 prepare_offline.py（补齐 wheel，需联网）"
  "$PY" scripts/prepare_offline.py --target windows
fi

echo "==> [full-offline] 3/3 verify_offline_wheels.py"
"$PY" scripts/verify_offline_wheels.py
echo "==> [full-offline] wheels 校验通过"

# OpenClaw + 微信插件须已在 nodejs/node_modules（由仓库根目录 build_package.sh 的 npm ci/install 写入）
if [ ! -d "$ROOT/nodejs/node_modules/openclaw" ] || [ ! -d "$ROOT/nodejs/node_modules/@tencent-weixin/openclaw-weixin" ]; then
  echo "[ERR] 缺少 nodejs 预装依赖：需要 nodejs/node_modules/openclaw 与 @tencent-weixin/openclaw-weixin"
  echo "      请在仓库根目录执行: bash build_package.sh（制包机需已安装 npm）"
  exit 1
fi
echo "==> [full-offline] nodejs/OpenClaw+微信插件 已就绪"

# Windows 素材剪辑：仅当本地尚无 ffmpeg.exe 时才下载（INCLUDE_FFMPEG=1）
if [ "${INCLUDE_FFMPEG:-}" = "1" ]; then
  if [ -f "deps/ffmpeg/ffmpeg.exe" ]; then
    echo "==> [ffmpeg] deps/ffmpeg/ffmpeg.exe 已存在，跳过下载"
  else
    echo "==> [ffmpeg] 缺少 ffmpeg.exe → ensure_ffmpeg_windows.py（需联网）"
    "$PY" scripts/ensure_ffmpeg_windows.py
  fi
fi
