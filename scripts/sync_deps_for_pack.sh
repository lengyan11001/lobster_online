#!/usr/bin/env bash
# 仅在 lobster_online 内补齐运行依赖（wheel、python embed、node、openclaw、ffmpeg、vc_redist 等）。
# 不修改、不覆盖：install.bat、start.bat、run_backend.bat、run_mcp.bat。
# 用法：bash scripts/sync_deps_for_pack.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo ""
echo "=========================================="
echo "  sync_deps_for_pack（只拉依赖，不改 bat）"
echo "=========================================="
echo ""

bash "$ROOT/build_package.sh"

mkdir -p "$ROOT/deps"
VC_URL="https://aka.ms/vs/17/release/vc_redist.x64.exe"
if [ ! -f "$ROOT/deps/vc_redist.x64.exe" ]; then
  echo ">>> 下载 VC++ -> deps/vc_redist.x64.exe"
  curl -fL -o "$ROOT/deps/vc_redist.x64.exe" "$VC_URL"
else
  echo ">>> deps/vc_redist.x64.exe 已存在，跳过"
fi

export INCLUDE_FFMPEG=1
unset FORCE_PREPARE_OFFLINE 2>/dev/null || true
bash "$ROOT/scripts/ensure_full_pack_deps.sh"

echo ">>> Chromium 已取消打入完整依赖包，跳过 browser_chromium 准备"

echo ""
echo ">>> 补齐后复检"
python3 "$ROOT/scripts/report_pack_gaps.py"
echo ""
echo "=== sync_deps_for_pack 完成（未执行打包） ==="
echo ""
