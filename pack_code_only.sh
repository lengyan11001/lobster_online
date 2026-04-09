#!/usr/bin/env bash
# 纯代码包：不含 deps/wheels、不含 ffmpeg.exe、不含 pack_bundle.env 注入；排除 scripts 下探测产物。
# 解压覆盖后请在已有环境中用 install.bat / pip 安装依赖（与完整代码包相比体积极小）。
set -euo pipefail
cd "$(dirname "$0")"

rm -f lobster_online_code_only_*.zip 2>/dev/null || true

export SKIP_ENSURE_PACK_DEPS=1
export SKIP_PACK_BUNDLE_ENV=1

BUILD_ID=$(date +%Y%m%d%H%M%S)
OUT="lobster_online_code_only_${BUILD_ID}.zip"

BAT_TEMP_DIR=$(mktemp -d)
trap "rm -rf '$BAT_TEMP_DIR'" EXIT
for bat in *.bat; do
  if [ -f "$bat" ]; then
    if file "$bat" | grep -q "UTF-8"; then
      iconv -f UTF-8 -t GBK "$bat" > "$BAT_TEMP_DIR/$bat" 2>/dev/null || cp "$bat" "$BAT_TEMP_DIR/$bat"
    else
      cp "$bat" "$BAT_TEMP_DIR/$bat"
    fi
    sed -i '' 's/$/\r/' "$BAT_TEMP_DIR/$bat" 2>/dev/null || \
    perl -pi -e 's/\n/\r\n/' "$BAT_TEMP_DIR/$bat" 2>/dev/null || \
    awk '{printf "%s\r\n", $0}' "$BAT_TEMP_DIR/$bat" > "$BAT_TEMP_DIR/${bat}.tmp" && mv "$BAT_TEMP_DIR/${bat}.tmp" "$BAT_TEMP_DIR/$bat" 2>/dev/null || true
  fi
done

zip -r "$OUT" \
  backend/ \
  mcp/ \
  static/ \
  scripts/ \
  publisher/ \
  skills/ \
  deps/ffmpeg/README.txt \
  docs/ \
  skill_registry.json \
  upstream_urls.json \
  openclaw/openclaw.json \
  requirements.txt \
  requirements.runtime.txt \
  .env.example \
  README-一键使用.txt \
  README.md \
  一键启动.bat \
  使用说明-完整包.txt \
  使用说明-精简包.txt \
  单机版启动脚本_完整包.txt \
  nodejs/package.json \
  nodejs/package-lock.json \
  -x "*.pyc" "*__pycache__*" "*.db" "openclaw/workspace/*" "openclaw/.env" "browser_data/*" "assets/*" "sutui_config.json" \
  -x "*probe_three_out*" \
  -x "*.DS_Store" \
  2>/dev/null || true

for bat in "$BAT_TEMP_DIR"/*.bat; do
  if [ -f "$bat" ]; then
    bat_name=$(basename "$bat")
    (cd "$BAT_TEMP_DIR" && zip "$OLDPWD/$OUT" "$bat_name" >/dev/null 2>&1)
  fi
done

for pol in openclaw/workspace/LOBSTER_CHAT_POLICY_INTRO.md openclaw/workspace/LOBSTER_CHAT_POLICY_TOOLS.md; do
  if [ -f "$pol" ]; then
    zip "$OUT" "$pol" >/dev/null 2>&1 || true
  fi
done

if [ -f .env ]; then
  ENV_STAGING=$(mktemp -d)
  cp .env "$ENV_STAGING/.env"
  (cd "$ENV_STAGING" && zip "$OLDPWD/$OUT" .env >/dev/null)
  rm -rf "$ENV_STAGING"
  echo "  (已打入 .env ← 仓库根目录 .env)"
fi

SIZE=$(du -sh "$OUT" | cut -f1)
echo "✓ 已生成纯代码包: $OUT ($SIZE)"
echo "  未含: deps/wheels、ffmpeg.exe、pack_bundle.env；已排除 scripts 内 *probe_three_out*"
echo "  依赖: 在目标机用既有 install.bat 或 pip install -r requirements.txt"
echo ""
