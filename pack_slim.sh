#!/usr/bin/env bash
# 在线客户端 - 精简包：由 scripts/pack_slim_zip.py 生成 zip（GBK .bat、ASCII 文件名、UTF-8 zip）
# LOBSTER_BRAND_MARK（默认 yingshi）写入产物名；仅删除同品牌旧 slim zip。
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

LOBSTER_BRAND_MARK="${LOBSTER_BRAND_MARK:-yingshi}"
BM_SAFE=$(printf '%s' "$LOBSTER_BRAND_MARK" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9_-')
[ -z "$BM_SAFE" ] && BM_SAFE=yingshi
export LOBSTER_BRAND_MARK

echo "=== lobster_online 精简包（品牌 ${BM_SAFE} · 联网安装 · Windows）==="
rm -f "$(dirname "$SCRIPT_DIR")/lobster_online_slim_${BM_SAFE}_"*.zip 2>/dev/null || true
python3 "$SCRIPT_DIR/scripts/pack_slim_zip.py"
echo "  勿用 install.bat（完整离线包专用）；失败可改用 scripts/build_result_package.sh 打完整包。"
echo ""
