#!/usr/bin/env bash
# 打代码包前保证 deps/wheels 含：pycryptodome（Windows wheel）、tos（任意后缀）
# 已存在则只校验不访问网络；缺失则 pip download，失败即退出。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
mkdir -p deps/wheels

shopt -s nullglob

# 3D/SAM runtimes are optional and must not be shipped in the common client package.
_3d_wheels=(
  deps/wheels/torch-*.whl
  deps/wheels/torchvision-*.whl
  deps/wheels/opencv_python-*.whl
  deps/wheels/segment_anything-*.whl
  deps/wheels/trimesh-*.whl
  deps/wheels/pymeshfix-*.whl
  deps/wheels/shapely-*.whl
  deps/wheels/sympy-*.whl
  deps/wheels/filelock-*.whl
  deps/wheels/flatbuffers-*.whl
  deps/wheels/fsspec-*.whl
  deps/wheels/imageio-*.whl
  deps/wheels/jinja2-*.whl
  deps/wheels/jsonschema-*.whl
  deps/wheels/jsonschema_specifications-*.whl
  deps/wheels/lazy_loader-*.whl
  deps/wheels/llvmlite-*.whl
  deps/wheels/markupsafe-*.whl
  deps/wheels/mpmath-*.whl
  deps/wheels/networkx-*.whl
  deps/wheels/numba-*.whl
  deps/wheels/onnxruntime-*.whl
  deps/wheels/platformdirs-*.whl
  deps/wheels/pooch-*.whl
  deps/wheels/pymatting-*.whl
  deps/wheels/referencing-*.whl
  deps/wheels/rembg-*.whl
  deps/wheels/rpds_py-*.whl
  deps/wheels/scikit_image-*.whl
  deps/wheels/scipy-*.whl
  deps/wheels/tifffile-*.whl
)
if [ ${#_3d_wheels[@]} -gt 0 ]; then
  rm -f -- "${_3d_wheels[@]}"
  echo "==> removed optional 3D/SAM wheels from the common package"
fi
rm -f -- models/sam/sam_vit_b.pth 2>/dev/null || true

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
