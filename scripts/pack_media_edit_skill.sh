#!/usr/bin/env bash
# 仅打包「素材剪辑」技能相关文件，便于单独分发或合并到其它分支（非整站完整包）
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DATE=$(date +%Y%m%d_%H%M%S)
BUNDLE_NAME="media_edit_skill_bundle_${DATE}"
OUT_ZIP="${ROOT}/${BUNDLE_NAME}.zip"
TMP="$(mktemp -d)"
BUNDLE="${TMP}/${BUNDLE_NAME}"

mkdir -p "${BUNDLE}/backend/app/services" "${BUNDLE}/backend/app/api" "${BUNDLE}/skills/media_edit/merge_snippets"

cp "${ROOT}/backend/app/services/media_edit_exec.py" "${BUNDLE}/backend/app/services/"
cp "${ROOT}/backend/app/services/__init__.py" "${BUNDLE}/backend/app/services/"
cp "${ROOT}/backend/app/api/media_edit.py" "${BUNDLE}/backend/app/api/"
cp "${ROOT}/skills/media_edit/PORTING.md" "${BUNDLE}/skills/media_edit/"
cp -r "${ROOT}/skills/media_edit/merge_snippets/." "${BUNDLE}/skills/media_edit/merge_snippets/"

cat > "${BUNDLE}/README.txt" << 'EOF'
素材剪辑技能（media.edit）独立包
================================
内容：
  backend/app/services/media_edit_exec.py  — 剪辑执行（需本机 ffmpeg）
  backend/app/api/media_edit.py          — POST /api/media-edit/run
  skills/media_edit/PORTING.md           — 移植说明
  skills/media_edit/merge_snippets/      — 合并用 JSON 片段与说明

使用前请阅读 skills/media_edit/PORTING.md 与 merge_snippets/README.md。
本包不含 create_app.py / mcp/http_server.json 的自动修改，需按文档合并。
EOF

cd "${TMP}"
zip -r -q "${OUT_ZIP}" "${BUNDLE_NAME}"
rm -rf "${TMP}"

SIZE=$(du -sh "${OUT_ZIP}" | cut -f1)
echo "✓ 已生成: ${OUT_ZIP} (${SIZE})"
