#!/usr/bin/env bash
# 仅打包「素材剪辑 / overlay_text」相关改动，便于同步到 lobster-server、单机 lobster 等。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PARENT="$(dirname "$ROOT")"
PROJ="$(basename "$ROOT")"
BUILD_ID=$(date +%Y%m%d_%H%M%S)
OUT_NAME="media_edit_sync_${BUILD_ID}.zip"
OUT_PATH="${PARENT}/${OUT_NAME}"

rm -f "${PARENT}/media_edit_sync_"*.zip 2>/dev/null || true

README_SYNC="${ROOT}/README_MEDIA_EDIT_SYNC.txt"
cat > "$README_SYNC" <<'EOF'
media_edit_sync 补丁包
====================
在 workspace 根目录解压：unzip -o media_edit_sync_*.zip
将覆盖 lobster_online/ 下所列路径。

详细步骤见：lobster_online/docs/剪辑优化-跨版本同步.md

lobster-server：仅合并 skill_registry.json 里 media_edit_skill → media.edit → arg_schema
（不部署 media_edit_exec.py，剪辑仅在本机执行）。
EOF

cd "$PARENT"
zip -r "$OUT_PATH" \
  "$PROJ/README_MEDIA_EDIT_SYNC.txt" \
  "$PROJ/backend/app/services/media_edit_exec.py" \
  "$PROJ/backend/app/api/assets.py" \
  "$PROJ/backend/app/api/media_edit.py" \
  "$PROJ/backend/app/api/chat.py" \
  "$PROJ/mcp/capability_catalog.json" \
  "$PROJ/skill_registry.json" \
  "$PROJ/skills/media_edit/merge_snippets/capability_catalog_media_edit.json" \
  "$PROJ/skills/media_edit/merge_snippets/skill_registry_media_edit_skill.json" \
  "$PROJ/deps/ffmpeg/README.txt" \
  "$PROJ/docs/素材剪辑-overlay_text参数.md" \
  "$PROJ/docs/剪辑优化-跨版本同步.md" \
  "$PROJ/docs/文档与规则索引.md" \
  "$PROJ/docs/服务器日志-素材剪辑media.edit排查.md" \
  "$PROJ/scripts/pack_media_edit_sync.sh"

rm -f "$README_SYNC"

SIZE=$(du -sh "$OUT_PATH" | cut -f1)
echo "✓ 已生成: $OUT_PATH ($SIZE)"
