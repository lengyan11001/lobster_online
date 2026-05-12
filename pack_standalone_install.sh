#!/usr/bin/env bash
# 在线客户端一键安装包：打包整个项目目录，解压后进入该目录运行 install.bat 再 start.bat
# LOBSTER_BRAND_MARK（默认 yingshi）写入产物名；仅删除同品牌旧 zip。
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PARENT="$(dirname "$SCRIPT_DIR")"
PROJ=$(basename "$SCRIPT_DIR")

LOBSTER_BRAND_MARK="${LOBSTER_BRAND_MARK:-yingshi}"
BM_SAFE=$(printf '%s' "$LOBSTER_BRAND_MARK" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9_-')
[ -z "$BM_SAFE" ] && BM_SAFE=yingshi
BUILD_ID=$(date +%Y%m%d_%H%M%S)
OUT_NAME="${PROJ}_一键安装包_${BM_SAFE}_${BUILD_ID}.zip"
OUT_PATH="$PARENT/$OUT_NAME"

rm -f "$PARENT/${PROJ}_一键安装包_${BM_SAFE}_"*.zip 2>/dev/null || true

echo "=== ${PROJ} 一键安装包（目标 Windows）==="
echo "正在打包: $OUT_PATH"
echo ""

cd "$PARENT"
zip -r "$OUT_NAME" "$PROJ" \
  -x "${PROJ}/.git/*" "${PROJ}/*.pyc" "${PROJ}/*__pycache__*" "${PROJ}/*.db" \
  -x "${PROJ}/node_modules/*" "${PROJ}/${PROJ}_*.zip" "${PROJ}/*.tar.gz" \
  -x "${PROJ}/openclaw/workspace/*" "${PROJ}/browser_data/*" "${PROJ}/assets/*" "${PROJ}/static/hifly_previews/*" \
  -x "${PROJ}/explore_douyin.py" "${PROJ}/douyin_*.png" "${PROJ}/douyin_*.json" \
  2>/dev/null || true

SIZE=$(du -sh "$OUT_PATH" 2>/dev/null | cut -f1)
echo "✓ 已生成: $OUT_NAME ($SIZE)"
echo ""
echo "使用方法（Windows）："
echo "  1. 将 $OUT_NAME 拷贝到目标机器（如 D:\\）"
echo "  2. 右键 → 解压到当前文件夹，得到 ${PROJ} 文件夹"
echo "  3. 进入 ${PROJ} 目录，双击 install.bat 完成依赖安装"
echo "  4. 双击 start.bat 启动"
echo "  5. 浏览器打开 http://localhost:8000 ，按界面配置 API / 登录云端等"
echo ""
