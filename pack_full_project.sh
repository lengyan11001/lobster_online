#!/usr/bin/env bash
# 在线客户端 - 完整项目包：仅 zip；不修改 install.bat、start.bat、run_*.bat。
# 排除使用说明、打包脚本等（与 pack_full 排除项类似，更严）。
# LOBSTER_BRAND_MARK（默认 yingshi）写入产物名，且仅删除同品牌旧 zip，避免 yingshi/bihuo 互相覆盖。
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PARENT="$(dirname "$SCRIPT_DIR")"
PROJ=$(basename "$SCRIPT_DIR")
LOBSTER_BRAND_MARK="${LOBSTER_BRAND_MARK:-yingshi}"
BM_SAFE=$(printf '%s' "$LOBSTER_BRAND_MARK" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9_-')
[ -z "$BM_SAFE" ] && BM_SAFE=yingshi
BUILD_ID=$(date +%Y%m%d_%H%M%S)
OUT_NAME="${PROJ}_完整项目包_${BM_SAFE}_${BUILD_ID}.zip"
OUT_PATH="$PARENT/$OUT_NAME"

echo "=== ${PROJ} 完整项目包（品牌 ${BM_SAFE} · 含依赖 · 无使用说明/打包脚本）==="

if [ "${SKIP_ENSURE_FULL_PACK_DEPS:-}" = "1" ]; then
  echo "跳过 ensure_full_pack_deps（SKIP_ENSURE_FULL_PACK_DEPS=1）"
else
  bash "$SCRIPT_DIR/scripts/ensure_full_pack_deps.sh"
fi

if [ ! -d "$SCRIPT_DIR/python" ] || [ ! -f "$SCRIPT_DIR/nodejs/node.exe" ]; then
    echo "提示: 未检测到 python/ 或 nodejs/node.exe，包内将不包含 Windows 嵌入式运行环境。"
    echo "      可先在本目录执行 ./build_package.sh 再打包。"
fi

if [ -d "$SCRIPT_DIR/browser_chromium" ]; then
    echo "检测到 browser_chromium/，将打入包内。"
else
    echo "提示: 未检测到 browser_chromium/，包内不含 Playwright 离线浏览器。"
fi

rm -f "$PARENT/${PROJ}_完整项目包_${BM_SAFE}_"*.zip 2>/dev/null || true
cd "$PARENT"

# 仅 zip，不修改 install.bat / start.bat / run_*.bat（与 build_result_package.sh 约定一致；CRLF 请在仓库内维护或本地手动执行 scripts/ensure_bat_crlf.py）
# 与 pack_full 相同的排除，并额外排除：使用说明、打包脚本、开发文档、制包用密钥 pack_bundle.env 等（上线包仅保留 .env.example）
if command -v zip >/dev/null 2>&1; then
  zip -r "$OUT_NAME" "$PROJ" \
    -x "${PROJ}/.git/*" \
    -x "${PROJ}/*.pyc" "${PROJ}/*__pycache__*" "${PROJ}/*.db" \
    -x "${PROJ}/openclaw/workspace/*" "${PROJ}/openclaw/.env" "${PROJ}/.env" "${PROJ}/browser_data/*" "${PROJ}/assets/*" \
    -x "${PROJ}/sutui_config.json" \
    -x "${PROJ}/pack_bundle.env" \
    -x "${PROJ}/${PROJ}_*.zip" "${PROJ}/*.tar.gz" "${PROJ}/explore_douyin.py" \
    -x "${PROJ}/douyin_*.png" "${PROJ}/douyin_*.json" \
    -x "${PROJ}/media_edit_skill_bundle_*.zip" \
    -x "${PROJ}/lobster_online_code_*.zip" \
    -x "${PROJ}/lobster_code_*.zip" \
    -x "${PROJ}/nodejs/node_modules/thread-stream/test/*.zip" \
    -x "${PROJ}/nodejs/node_modules/thread-stream/test/*/*.zip" \
    -x "${PROJ}/xskill_*.json" \
    -x "${PROJ}/xskill_*.jsonl" \
    -x "${PROJ}/openclaw.log" \
    -x "${PROJ}/installed_packages.json" \
    -x "${PROJ}/mcp_registry_cache.json" \
    -x "${PROJ}/logs/*" \
    -x "${PROJ}/test_mcp.py" \
    -x "${PROJ}/pack_*.sh" \
    -x "${PROJ}/pack_full_project.sh" \
    -x "${PROJ}/build_package.sh" \
    -x "${PROJ}/使用说明*.txt" \
    -x "${PROJ}/README-一键使用.txt" \
    -x "${PROJ}/README.md" \
    -x "${PROJ}/单机版启动脚本*.txt" \
    -x "${PROJ}/修复MCP服务未就绪.md" \
    -x "${PROJ}/诊断MCP连接问题.md" \
    -x "${PROJ}/static/桌面图标说明.txt" \
    -x "${PROJ}/docs/*" \
    -x "${PROJ}/scripts/ensure_full_pack_deps.sh" \
    -x "${PROJ}/scripts/ensure_pack_deps.sh" \
    -x "${PROJ}/scripts/pack_media_edit_skill.sh" \
    -x "${PROJ}/scripts/build_result_package.sh" \
    -x "${PROJ}/scripts/sync_deps_for_pack.sh" \
    -x "${PROJ}/scripts/report_pack_gaps.py" \
    -x "*.DS_Store"
else
  echo "提示: 未找到 zip 命令，使用 Python 生成 zip（排除规则与上方一致）"
  _PY="python3"
  if ! "$_PY" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" 2>/dev/null; then
    _PY="python"
  fi
  "$_PY" "$SCRIPT_DIR/scripts/pack_full_project_zip.py" "$PARENT" "$PROJ" "$OUT_PATH"
fi

if [ ! -f "$OUT_PATH" ]; then
  echo "[ERR] 未生成压缩包: $OUT_PATH"
  exit 1
fi

SIZE=$(du -sh "$OUT_PATH" 2>/dev/null | cut -f1)
echo "✓ 已生成: $OUT_NAME ($SIZE)"
echo "  路径: $OUT_PATH"
echo "  说明: 已排除 docs/、根目录 *.md、pack_bundle.env、根目录技能/代码历史 zip、thread-stream 测试 zip（保留 python/python312.zip）"
echo ""
