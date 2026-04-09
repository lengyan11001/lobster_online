#!/usr/bin/env bash
# 无网络：仅从 lobster_online/deps/wheels 安装 requirements.txt（与 install.bat 离线分支一致）
#
# 使用前须在有网机器同步 wheel 到 deps/wheels：
#   本机/macOS/Linux：  python3 scripts/prepare_offline.py --target current && python3 scripts/verify_offline_wheels.py
#   Windows 完整包：    bash scripts/ensure_full_pack_deps.sh
#
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-python3}"
command -v "$PYTHON" >/dev/null 2>&1 || PYTHON=python

if [ ! -d deps/wheels ] || [ -z "$(ls -A deps/wheels 2>/dev/null)" ]; then
  echo "[ERR] deps/wheels 为空。请在有网络的机器上执行："
  echo "      python3 scripts/prepare_offline.py --target current"
  echo "  然后将 lobster_online（含 deps/wheels）整目录拷贝到本机再运行本脚本。"
  exit 1
fi

echo "==> 离线安装 Python 依赖（--no-index，来源 deps/wheels）"
"$PYTHON" -m pip install --no-index --find-links deps/wheels -r requirements.txt

echo "==> 校验关键模块导入"
"$PYTHON" -c "import fastapi, uvicorn, pydantic, httpx, sqlalchemy, playwright, greenlet, PIL, googleapiclient, httplib2" \
  && echo "[OK] 离线依赖安装完成"
