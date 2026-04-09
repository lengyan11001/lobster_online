#!/usr/bin/env bash
# 从 static/bihu_source.png 重新生成 Windows .ico、各档 PNG、macOS .icns（白底或任意底图均可）
set -euo pipefail
cd "$(dirname "$0")"
SRC="bihu_source.png"
if [[ ! -f "$SRC" ]]; then
  echo "missing $SRC" >&2
  exit 1
fi
# 若源文件实为 JPEG（扩展名误为 .png），先转为真 PNG
if ! file "$SRC" | grep -q 'PNG image'; then
  echo "[regen_icons] converting source to PNG..."
  sips -s format png "$SRC" --out bihu_source_tmp.png
  mv bihu_source_tmp.png "$SRC"
fi
for s in 16 32 48 64 128 256 512 1024; do
  sips -z "$s" "$s" "$SRC" --out "bihu_${s}.png"
done
python3 build_desktop_icon.py
rm -rf BihuIcon.iconset
mkdir BihuIcon.iconset
sips -z 16 16 "$SRC" --out BihuIcon.iconset/icon_16x16.png
sips -z 32 32 "$SRC" --out BihuIcon.iconset/icon_16x16@2x.png
sips -z 32 32 "$SRC" --out BihuIcon.iconset/icon_32x32.png
sips -z 64 64 "$SRC" --out BihuIcon.iconset/icon_32x32@2x.png
sips -z 128 128 "$SRC" --out BihuIcon.iconset/icon_128x128.png
sips -z 256 256 "$SRC" --out BihuIcon.iconset/icon_128x128@2x.png
sips -z 256 256 "$SRC" --out BihuIcon.iconset/icon_256x256.png
sips -z 512 512 "$SRC" --out BihuIcon.iconset/icon_256x256@2x.png
sips -z 512 512 "$SRC" --out BihuIcon.iconset/icon_512x512.png
sips -z 1024 1024 "$SRC" --out BihuIcon.iconset/icon_512x512@2x.png
iconutil -c icns BihuIcon.iconset -o 必火AI_BOX.icns
echo "OK: bihu_box.ico, 必火AI_BOX.icns, bihu_*.png"
