#!/usr/bin/env bash
# 打代码包前保证 deps/wheels 含：pycryptodome（Windows wheel）、tos（任意后缀）
# 已存在则只校验不访问网络；缺失则 pip download，失败即退出。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
mkdir -p deps/wheels

shopt -s nullglob

_have_pycrypt() {
  local a=(deps/wheels/pycryptodome*.whl)
  [ ${#a[@]} -gt 0 ]
}

_have_tos() {
  local a=(deps/wheels/tos-*)
  [ ${#a[@]} -gt 0 ]
}

if _have_pycrypt && _have_tos; then
  echo "==> deps/wheels 已含 pycryptodome 与 tos，跳过 pip download"
else
  echo "==> [1/2] pycryptodome — Windows win_amd64 + CPython 3.12 仅 wheel"
  python3 -m pip download pycryptodome \
    --platform win_amd64 \
    --python-version 312 \
    --only-binary :all: \
    -d deps/wheels
  echo "==> [2/2] tos (>=2.9)（完整 Windows wheel 请以 prepare_offline.py --target windows 为准）"
  python3 -m pip download "tos>=2.9.0" -d deps/wheels
fi

if ! _have_pycrypt; then
  echo "ERROR: deps/wheels 中缺少 pycryptodome*.whl"
  exit 1
fi
if ! _have_tos; then
  echo "ERROR: deps/wheels 中缺少 tos-*"
  exit 1
fi

echo "==> deps/wheels 校验通过:"
ls -la deps/wheels/pycryptodome*.whl
ls -la deps/wheels/tos-*
