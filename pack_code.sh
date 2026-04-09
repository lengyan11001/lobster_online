#!/usr/bin/env bash
# 只打包代码部分（不含 python/ nodejs/ deps/），用于快速更新已部署的 Windows 端。
# 与「纯代码包」OTA 一致：zip 上传 lobster_server client_static/client_code/bundles/，公网路径 /client/client-code/；见 CLIENT_CODE_MANIFEST_URL。
# 打新包前会删掉同目录下旧的代码包。技能依赖：pycryptodome、tos 的 Windows wheel 放入 deps/wheels 后一并打入 zip。
set -euo pipefail
cd "$(dirname "$0")"

rm -f lobster_online_code_*.zip 2>/dev/null || true

# 技能依赖 wheel：须完整，见 scripts/ensure_pack_deps.sh（失败即中止）
if [ "${SKIP_ENSURE_PACK_DEPS:-}" = "1" ]; then
  echo "跳过 ensure_pack_deps（SKIP_ENSURE_PACK_DEPS=1），请确认 deps/wheels 已有人工放入的 pycryptodome / tos"
else
  bash scripts/ensure_pack_deps.sh
fi
# Windows 素材剪辑：可选下载 ffmpeg.exe 到 deps/ffmpeg/（体积大，需联网）
if [ "${INCLUDE_FFMPEG_IN_PACK:-}" = "1" ]; then
  python3 scripts/ensure_ffmpeg_windows.py
fi
# 注意：playwright 和 Pillow 是运行时依赖，通过 install.bat 从 requirements.txt 安装，不打包进代码包

BUILD_ID=$(date +%Y%m%d%H%M%S)
OUT="lobster_online_code_${BUILD_ID}.zip"

# 临时转换 .bat 文件为 GBK 编码 + CRLF 行尾符（Windows 批处理需要）
# 创建临时目录存放转换后的文件，打包完成后自动清理
BAT_TEMP_DIR=$(mktemp -d)
trap "rm -rf '$BAT_TEMP_DIR'" EXIT
for bat in *.bat; do
  if [ -f "$bat" ]; then
    # 检查是否已经是 GBK/ASCII，如果是 UTF-8 则转换
    if file "$bat" | grep -q "UTF-8"; then
      iconv -f UTF-8 -t GBK "$bat" > "$BAT_TEMP_DIR/$bat" 2>/dev/null || cp "$bat" "$BAT_TEMP_DIR/$bat"
    else
      cp "$bat" "$BAT_TEMP_DIR/$bat"
    fi
    # 确保行尾符是 CRLF（Windows 批处理需要）
    sed -i '' 's/$/\r/' "$BAT_TEMP_DIR/$bat" 2>/dev/null || \
    perl -pi -e 's/\n/\r\n/' "$BAT_TEMP_DIR/$bat" 2>/dev/null || \
    awk '{printf "%s\r\n", $0}' "$BAT_TEMP_DIR/$bat" > "$BAT_TEMP_DIR/${bat}.tmp" && mv "$BAT_TEMP_DIR/${bat}.tmp" "$BAT_TEMP_DIR/$bat" 2>/dev/null || true
  fi
done

# 与 lobster/pack_code.sh 对齐的目录与脚本；不含 openclaw/.env、本机密钥与数据库
zip -r "$OUT" \
  backend/ \
  mcp/ \
  static/ \
  scripts/ \
  publisher/ \
  skills/ \
  deps/wheels/ \
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
  2>/dev/null || true

# 添加转换后的 .bat 文件（GBK 编码，Windows 兼容）
for bat in "$BAT_TEMP_DIR"/*.bat; do
  if [ -f "$bat" ]; then
    bat_name=$(basename "$bat")
    (cd "$BAT_TEMP_DIR" && zip "$OLDPWD/$OUT" "$bat_name" >/dev/null 2>&1)
  fi
done

# /chat 必读策略（上面对 openclaw/workspace/* 整目录排除，此处单独补入，避免覆盖安装后模型不调 MCP）
for pol in openclaw/workspace/LOBSTER_CHAT_POLICY_INTRO.md openclaw/workspace/LOBSTER_CHAT_POLICY_TOOLS.md; do
  if [ -f "$pol" ]; then
    zip "$OUT" "$pol" >/dev/null 2>&1 || true
  fi
done

# deps/wheels/ 已整体打入上方 zip；playwright / Pillow 由 install.bat 从 requirements 安装

# 内置 .env：优先仓库根目录 .env（与制包机一致）；否则在未设 SKIP 时用 pack_bundle.env
if [ -f .env ]; then
  ENV_STAGING=$(mktemp -d)
  cp .env "$ENV_STAGING/.env"
  (cd "$ENV_STAGING" && zip "$OLDPWD/$OUT" .env >/dev/null)
  rm -rf "$ENV_STAGING"
  echo "  (已打入 .env ← 仓库根目录 .env)"
elif [ "${SKIP_PACK_BUNDLE_ENV:-}" != "1" ] && [ -f pack_bundle.env ]; then
  ENV_STAGING=$(mktemp -d)
  cp pack_bundle.env "$ENV_STAGING/.env"
  (cd "$ENV_STAGING" && zip "$OLDPWD/$OUT" .env >/dev/null)
  rm -rf "$ENV_STAGING"
  echo "  (已打入 .env ← pack_bundle.env)"
elif [ "${SKIP_PACK_BUNDLE_ENV:-}" = "1" ]; then
  echo "  (SKIP_PACK_BUNDLE_ENV=1 且无根目录 .env：未打入 .env，用户可从 .env.example 生成)"
fi

# 素材剪辑：包内 Windows ffmpeg.exe（可选：Linux 服务端可另放 deps/ffmpeg/ffmpeg）
FFMPEG_MSG=""
if [ -f deps/ffmpeg/ffmpeg.exe ]; then
  zip -r "$OUT" deps/ffmpeg/ffmpeg.exe
  FFMPEG_MSG="${FFMPEG_MSG} ffmpeg.exe"
fi
if [ -f deps/ffmpeg/ffmpeg ]; then
  zip -r "$OUT" deps/ffmpeg/ffmpeg
  FFMPEG_MSG="${FFMPEG_MSG} ffmpeg"
fi
if [ -n "$FFMPEG_MSG" ]; then
  echo "  (已打入 deps/ffmpeg:$FFMPEG_MSG)"
else
  echo "  (未包含 deps/ffmpeg 可执行文件；Windows 可设 INCLUDE_FFMPEG_IN_PACK=1 或 install.bat 联网下载)"
fi

SIZE=$(du -sh "$OUT" | cut -f1)
echo "✓ 已生成: $OUT ($SIZE)"
echo ""
echo "使用方法：见 README-一键使用.txt（解压覆盖 → 双击 一键启动.bat）"
echo "  不会替换 openclaw/.env、sutui_config.json、*.db"
echo ""
