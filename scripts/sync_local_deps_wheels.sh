#!/usr/bin/env bash
# 有网一键：按当前 Python 平台把 requirements.txt 同步到 deps/wheels（供无网机 install_offline.sh）
# 依赖更新后请在发版/拷贝前执行本脚本。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
echo "==> [1/2] prepare_offline.py --target current"
python3 scripts/prepare_offline.py --target current
echo "==> [2/2] verify_offline_wheels.py"
python3 scripts/verify_offline_wheels.py
echo "==> 完成。无网环境：cd lobster_online && bash scripts/install_offline.sh"
