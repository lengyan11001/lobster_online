#!/usr/bin/env bash
# 最小验证包：仅 embedded python + pip 工具链 wheel + pip_bootstrap + verify_install.bat
# 用于在 Windows 上快速验证 CRLF/.bat 解析与离线 pip 引导，体积小（约数十 MB）。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PARENT="$(dirname "$ROOT")"
PROJ=$(basename "$ROOT")
BUILD_ID=$(date +%Y%m%d_%H%M%S)
OUT_NAME="${PROJ}_验证包_${BUILD_ID}.zip"
OUT_PATH="$PARENT/$OUT_NAME"
TMP="$PARENT/.lobster_verify_pack_tmp_${BUILD_ID}"

cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT

echo "=== ${PROJ} 最小验证包 → ${OUT_NAME} ==="
rm -rf "$TMP"
mkdir -p "$TMP/$PROJ/deps/wheels"

cp -R "$ROOT/python" "$TMP/$PROJ/"
cp "$ROOT/deps/get-pip.py" "$TMP/$PROJ/deps/" 2>/dev/null || true

for pat in pip setuptools wheel packaging; do
  shopt -s nullglob
  for f in "$ROOT/deps/wheels/${pat}"*.whl; do
    cp "$f" "$TMP/$PROJ/deps/wheels/"
  done
  shopt -u nullglob
done

mkdir -p "$TMP/$PROJ/scripts"
cp "$ROOT/scripts/pip_bootstrap_from_wheel.py" "$TMP/$PROJ/scripts/"
cp "$ROOT/verify_install.bat" "$TMP/$PROJ/"
# 占位 install.bat：覆盖旧目录里残留的完整版 install.bat，避免用户仍双击到坏换行的旧文件
cp "$ROOT/scripts/install_verify_pack_stub.bat" "$TMP/$PROJ/install.bat"

cat > "$TMP/$PROJ/README-验证包.txt" << 'EOF'
【重要】本包不含完整 install.bat 逻辑。若解压到已有 lobster_online 目录，本包会自带
  install.bat（仅占位说明），用于覆盖旧文件；真正验证请只运行 verify_install.bat。

1. 建议删掉旧目录后整包解压到短路径（如 D:\lobster_online）。
2. 只双击 verify_install.bat（不要指望本包里的 install.bat 能装依赖）。
3. 无「'cho' / '/d' / 'tle'」等乱码且最后 [OK] 即 CRLF 与 pip 离线引导正常。
4. 完整功能请另下「完整项目包」，再运行其中的 install.bat。
EOF

cd "$TMP"
rm -f "$PARENT/${PROJ}_验证包_"*.zip 2>/dev/null || true
zip -r -q "$OUT_PATH" "$PROJ"

SIZE=$(du -sh "$OUT_PATH" | cut -f1)
echo "✓ 已生成: $OUT_NAME ($SIZE)"
echo "  路径: $OUT_PATH"
