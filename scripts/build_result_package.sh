#!/usr/bin/env bash
# 一键：先检查缺口 → 只向 lobster_online 内下载补齐依赖 → 复检 → 再 zip。
# 绝不修改、不覆盖：install.bat、start.bat、run_backend.bat、run_mcp.bat（打包脚本只 zip）。
# 强制全量重下 wheel：FORCE_PREPARE_OFFLINE=1
# 制包默认品牌：yingshi（InsClaw）；必火包可 export LOBSTER_BRAND_MARK=bihuo
# 用法：bash scripts/build_result_package.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export ROOT
export LOBSTER_BRAND_MARK="${LOBSTER_BRAND_MARK:-yingshi}"

# Git Bash / Windows：python3 常为 Store 占位（无法运行脚本）；回退到 python
PY="python3"
if ! "$PY" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" 2>/dev/null; then
  PY="python"
fi

echo ""
echo "=========================================="
echo "  Lobster 一键结果包（检查 → 拉齐依赖 → zip）"
echo "  不改 install.bat / start.bat / run_*.bat"
echo "=========================================="
echo ""

echo ">>> [0/7] 制包前检查（只读）"
"$PY" "$ROOT/scripts/report_pack_gaps.py" || true
echo ""

echo ">>> [1/7] build_package.sh（Python embed、get-pip、wheels、Node、OpenClaw、openclaw.json）"
bash "$ROOT/build_package.sh"

mkdir -p "$ROOT/deps"
VC_URL="https://aka.ms/vs/17/release/vc_redist.x64.exe"
if [ ! -f "$ROOT/deps/vc_redist.x64.exe" ]; then
  echo ">>> [2/7] 下载 VC++ 运行库 -> deps/vc_redist.x64.exe"
  curl -fL -o "$ROOT/deps/vc_redist.x64.exe" "$VC_URL"
else
  echo ">>> [2/7] deps/vc_redist.x64.exe 已存在，跳过"
fi

echo ">>> [3/7] ensure_full_pack_deps.sh（仅缺失时联网；verify 通过则跳过 prepare_offline）"
export INCLUDE_FFMPEG=1
unset FORCE_PREPARE_OFFLINE 2>/dev/null || true
bash "$ROOT/scripts/ensure_full_pack_deps.sh"

if [ -d "$ROOT/browser_chromium" ] && [ -n "$(ls -A "$ROOT/browser_chromium" 2>/dev/null)" ]; then
  echo ">>> [4/7] browser_chromium 已存在，跳过下载"
elif "$PY" -c "import sys; raise SystemExit(0 if sys.platform=='win32' else 1)" 2>/dev/null; then
  echo ">>> [4/7] prepare_chromium.py（仅因目录为空执行）"
  "$PY" "$ROOT/scripts/prepare_chromium.py"
else
  echo ""
  echo "[ERR] 当前不是 Windows，且 browser_chromium 为空。"
  echo "      在 Windows 执行 python scripts/prepare_chromium.py 后拷回 browser_chromium，再运行本脚本。"
  exit 1
fi

if [ ! -f "$ROOT/scripts/pip_bootstrap_from_wheel.py" ]; then
  echo "[ERR] 缺少 scripts/pip_bootstrap_from_wheel.py"
  exit 1
fi

echo ">>> [5/7] 校验 pip wheel 存在"
shopt -s nullglob
_pips=( "$ROOT/deps/wheels"/pip-*.whl )
shopt -u nullglob
if [ ${#_pips[@]} -eq 0 ]; then
  echo "[ERR] deps/wheels 下无 pip-*.whl，无法离线引导 pip"
  exit 1
fi

echo ">>> [6/7] 拉齐后复检（核心项不齐则中止，不生成 zip）"
"$PY" "$ROOT/scripts/report_pack_gaps.py"

_restore_env_example() {
  if [ -n "${_ENV_EXAMPLE_BACKUP:-}" ] && [ -f "$_ENV_EXAMPLE_BACKUP" ]; then
    cp -f "$_ENV_EXAMPLE_BACKUP" "$ROOT/.env.example"
    rm -f "$_ENV_EXAMPLE_BACKUP"
  fi
}
if [ "${LOBSTER_BRAND_MARK}" != "yingshi" ]; then
  echo ">>> [6b/7] 将 LOBSTER_BRAND_MARK=${LOBSTER_BRAND_MARK} 写入 .env.example（zip 仅含模板、不含 .env）"
  _ENV_EXAMPLE_BACKUP="$(mktemp "${TMPDIR:-/tmp}/lobster_env_example.XXXXXX")"
  cp "$ROOT/.env.example" "$_ENV_EXAMPLE_BACKUP"
  export _ENV_EXAMPLE_BACKUP
  trap '_restore_env_example' EXIT
  "$PY" -c "
import pathlib, os
root = pathlib.Path(os.environ['ROOT'])
mark = os.environ.get('LOBSTER_BRAND_MARK', 'yingshi')
p = root / '.env.example'
text = p.read_text(encoding='utf-8')
out = []
for line in text.splitlines(True):
    if line.strip().startswith('LOBSTER_BRAND_MARK='):
        out.append(f'LOBSTER_BRAND_MARK={mark}\n')
    else:
        out.append(line)
p.write_text(''.join(out), encoding='utf-8')
"
fi

echo ">>> [7/7] pack_full_project.sh（仅 zip，不改 bat）"
export SKIP_ENSURE_FULL_PACK_DEPS=1
bash "$ROOT/pack_full_project.sh"
_restore_env_example
trap - EXIT 2>/dev/null || true

echo ""
echo "=== 完成：结果包位于上级目录 lobster_online_完整项目包_<品牌>_<时间>.zip（仅会删除同品牌旧包）==="
echo "    用户解压后双击 install.bat（优先离线、失败可联网补救）；真无网见 使用说明-完整包.txt"
echo "    打包流程与排除项见 lobster_online/docs/生产打包流程.md（本 zip 内不含 docs/，请在源码仓查看）"
echo ""
