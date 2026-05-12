#!/usr/bin/env bash
# 在线客户端 - 完整包：仅把 lobster_online 打成 zip；不修改 install.bat、start.bat、run_*.bat。
# 用户说明见 使用说明-完整包.txt（本脚本会临时复制为 使用说明.txt 打入 zip）。
# LOBSTER_BRAND_MARK（默认 yingshi）写入产物名；仅删除同品牌旧 zip。
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PARENT="$(dirname "$SCRIPT_DIR")"
PROJ=$(basename "$SCRIPT_DIR")
LOBSTER_BRAND_MARK="${LOBSTER_BRAND_MARK:-yingshi}"
BM_SAFE=$(printf '%s' "$LOBSTER_BRAND_MARK" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9_-')
[ -z "$BM_SAFE" ] && BM_SAFE=yingshi
BUILD_ID=$(date +%Y%m%d_%H%M%S)
OUT_NAME="${PROJ}_完整包_${BM_SAFE}_${BUILD_ID}.zip"
OUT_PATH="$PARENT/$OUT_NAME"

echo "=== ${PROJ} 完整包（品牌 ${BM_SAFE} · 解压即用 · 目标 Windows）==="
# 离线安装依赖：deps/wheels + get-pip.py（与 install.bat --no-index 对齐）
if [ "${SKIP_ENSURE_FULL_PACK_DEPS:-}" = "1" ]; then
  echo "跳过 ensure_full_pack_deps（SKIP_ENSURE_FULL_PACK_DEPS=1）"
else
  bash "$SCRIPT_DIR/scripts/ensure_full_pack_deps.sh"
fi

if [ ! -d "$SCRIPT_DIR/python" ] || [ ! -f "$SCRIPT_DIR/nodejs/node.exe" ]; then
    echo "提示: 未检测到 python/ 或 nodejs/node.exe，完整包将不包含 Windows 运行环境。"
    echo "      先在本目录执行 ./build_package.sh 生成后再打包。"
fi

# 检查 browser_chromium（Playwright 离线浏览器）
if [ -d "$SCRIPT_DIR/browser_chromium" ]; then
    echo "检测到 browser_chromium/，将包含在完整包中（离线发布功能可用）"
else
    echo "提示: 未检测到 browser_chromium/，完整包将不包含 Playwright 浏览器。"
    echo "      运行 python scripts/prepare_chromium.py 可预下载（需联网）。"
fi

rm -f "$PARENT/${PROJ}_完整包_${BM_SAFE}_"*.zip 2>/dev/null || true
if [ -f "$SCRIPT_DIR/使用说明-完整包.txt" ]; then
  cp "$SCRIPT_DIR/使用说明-完整包.txt" "$SCRIPT_DIR/使用说明.txt"
fi
cd "$PARENT"
zip -r "$OUT_NAME" "$PROJ" \
  -x "${PROJ}/.git/*" "${PROJ}/*.pyc" "${PROJ}/*__pycache__*" "${PROJ}/*.db" \
  -x "${PROJ}/openclaw/workspace/*" "${PROJ}/openclaw/.env" "${PROJ}/.env" "${PROJ}/browser_data/*" "${PROJ}/assets/*" "${PROJ}/static/hifly_previews/*" \
  -x "${PROJ}/sutui_config.json" \
  -x "${PROJ}/pack_bundle.env" \
  -x "${PROJ}/${PROJ}_*.zip" "${PROJ}/*.tar.gz" "${PROJ}/explore_douyin.py" \
  -x "${PROJ}/douyin_*.png" "${PROJ}/douyin_*.json" \
  -x "${PROJ}/media_edit_skill_bundle_*.zip" \
  -x "${PROJ}/lobster_online_code_*.zip" \
  -x "${PROJ}/lobster_code_*.zip" \
  -x "${PROJ}/nodejs/node_modules/thread-stream/test/*.zip" \
  -x "${PROJ}/nodejs/node_modules/thread-stream/test/*/*.zip" \
  -x "${PROJ}/docs/*" \
  -x "*.DS_Store" \
  2>/dev/null || true
rm -f "$SCRIPT_DIR/使用说明.txt" 2>/dev/null || true

SIZE=$(du -sh "$OUT_PATH" 2>/dev/null | cut -f1)
echo "✓ 已生成: $OUT_NAME ($SIZE)"
echo "  解压 → 进入 ${PROJ} → install.bat → start.bat → 浏览器打开 http://localhost:8000"
echo ""
