#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

PY="python3"
if ! "$PY" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" 2>/dev/null; then
  PY="python"
fi

echo "=== 龙虾 (Lobster) 安装包构建 ==="

# Step 1: Download Python embeddable for Windows
PYTHON_VER="3.12.8"
PYTHON_ZIP="python-${PYTHON_VER}-embed-amd64.zip"
PYTHON_URL="https://www.python.org/ftp/python/${PYTHON_VER}/${PYTHON_ZIP}"

if [ ! -d "python" ]; then
    echo "[1] 下载 Python embeddable (${PYTHON_VER})..."
    curl -L -o "/tmp/${PYTHON_ZIP}" "${PYTHON_URL}"
    mkdir -p python
    cd python
    unzip -o "/tmp/${PYTHON_ZIP}"
    # Enable site-packages
    PTH_FILE=$(ls python3*._pth 2>/dev/null | head -1)
    if [ -n "$PTH_FILE" ]; then
        "$PY" -c "p=r'$PTH_FILE'; t=open(p).read().replace('#import site','import site'); open(p,'w').write(t)"
        echo "Lib/site-packages" >> "$PTH_FILE"
    fi
    mkdir -p Lib/site-packages
    cd ..
    echo "  [√] Python embeddable 就绪"
else
    echo "[1] Python embeddable 已存在，跳过"
fi

# Step 2: Download get-pip.py
mkdir -p deps/wheels
if [ ! -f "deps/get-pip.py" ]; then
    echo "[2] 下载 get-pip.py..."
    curl -L -o deps/get-pip.py https://bootstrap.pypa.io/get-pip.py
else
    echo "[2] get-pip.py 已存在，跳过"
fi

# Step 3: Windows wheels — 若 verify 已通过则整步跳过，不碰 PyPI
if "$PY" scripts/verify_offline_wheels.py 2>/dev/null; then
    echo "[3] deps/wheels 已满足 verify_offline_wheels.py，跳过 pip download"
else
    echo "[3] 补齐 Windows amd64 wheels（verify 未通过，需联网）..."
    "$PY" -m pip download \
        -r requirements.txt \
        --platform win_amd64 \
        --python-version 312 \
        --only-binary :all: \
        -d deps/wheels/ 2>&1 | tail -20 || true
    "$PY" -m pip download \
        -r requirements.txt \
        --platform any \
        --python-version 312 \
        --only-binary :all: \
        --no-deps \
        -d deps/wheels/ 2>&1 | tail -20 || true
    "$PY" -m pip download \
        -r requirements.txt \
        --no-deps \
        -d deps/wheels/ 2>/dev/null || true
    # 勿删 *.tar.gz：tos 等 sdist 供离线安装
    rm -f deps/wheels/*macosx*.whl 2>/dev/null
    rm -f deps/wheels/*linux*.whl 2>/dev/null
    echo "  [√] Wheels 目录现有 $(ls deps/wheels/*.whl 2>/dev/null | wc -l | tr -d ' ') 个 whl"
fi

# Step 4: Download Node.js portable for Windows
# 与 lobster 对齐：仅当存在 nodejs/node.exe 视为 Windows 便携版就绪；若仅有本机开发用的 nodejs（无 node.exe）须先移走，否则报错退出
NODE_VER="22.22.1"
NODE_ZIP="node-v${NODE_VER}-win-x64.zip"
NODE_URL="https://nodejs.org/dist/v${NODE_VER}/${NODE_ZIP}"

if [ -d "nodejs" ] && [ ! -f "nodejs/node.exe" ]; then
    echo "[ERR] 当前 nodejs/ 存在但不是 Windows 便携版（缺少 nodejs/node.exe），多为本机（如 macOS）开发依赖。"
    echo "      打 Windows 包前请将 nodejs 改名为 nodejs_host_backup 或移出本目录，再重新执行 ./build_package.sh"
    exit 1
fi

if [ ! -f "nodejs/node.exe" ]; then
    echo "[4] 下载 Node.js 便携版 (v${NODE_VER})..."
    curl -L -o "/tmp/${NODE_ZIP}" "${NODE_URL}"
    mkdir -p nodejs_tmp
    cd nodejs_tmp
    unzip -o "/tmp/${NODE_ZIP}"
    cd ..
    mv "nodejs_tmp/node-v${NODE_VER}-win-x64"/* nodejs_tmp/ 2>/dev/null || true
    rm -rf "nodejs_tmp/node-v${NODE_VER}-win-x64"
    mv nodejs_tmp nodejs
    echo "  [√] Node.js 便携版就绪"
else
    echo "[4] Windows Node.js 便携版已存在 (nodejs/node.exe)，跳过"
fi

# Step 5: Pre-install OpenClaw + plugins from nodejs/package.json（制包机须有 npm；便携 node.exe 无法在 macOS/Linux 上执行，故用系统 npm --prefix）
# 已有 openclaw 且已有微信插件则跳过，避免无谓 npm 与 Windows EBUSY
if [ ! -f "nodejs/package.json" ]; then
    echo "[ERR] 缺少 nodejs/package.json，无法预装 OpenClaw 依赖"
    exit 1
fi
if [ ! -d "nodejs/node_modules/openclaw" ] || [ ! -d "nodejs/node_modules/@tencent-weixin/openclaw-weixin" ]; then
    echo "[5] 预安装 nodejs 依赖（openclaw、@tencent-weixin/openclaw-weixin 等）..."
    if ! command -v npm >/dev/null 2>&1; then
        echo "[ERR] 未找到 npm。请安装 Node.js（仅制包机需要），再重新执行本脚本。"
        exit 1
    fi
    if [ -f "nodejs/package-lock.json" ]; then
        npm ci --prefix "$(pwd)/nodejs" --no-fund --no-audit
    else
        npm install --prefix "$(pwd)/nodejs" --no-fund --no-audit
    fi
    if [ ! -d "nodejs/node_modules/openclaw" ]; then
        echo "[ERR] OpenClaw 未安装成功（nodejs/node_modules/openclaw 不存在）"
        exit 1
    fi
    if [ ! -d "nodejs/node_modules/@tencent-weixin/openclaw-weixin" ]; then
        echo "[ERR] 微信 OpenClaw 插件未安装成功（nodejs/node_modules/@tencent-weixin/openclaw-weixin 不存在）"
        exit 1
    fi
    echo "  [√] nodejs 依赖预安装完成（含微信插件）"
else
    echo "[5] OpenClaw 与微信插件已存在，跳过"
fi

# Step 6: Create openclaw config directory
mkdir -p openclaw/workspace
if [ ! -f "openclaw/openclaw.json" ]; then
    echo "[6] 创建 OpenClaw 配置模板..."
    cat > openclaw/openclaw.json << 'OCEOF'
{
  "agent": {
    "workspace": "./openclaw/workspace",
    "model": { "primary": "anthropic/claude-sonnet-4-5" }
  },
  "gateway": {
    "mode": "local",
    "port": 18789,
    "bind": "127.0.0.1",
    "auth": { "mode": "token", "token": "LOBSTER_AUTO_TOKEN_PLACEHOLDER" },
    "http": { "endpoints": { "chatCompletions": { "enabled": true } } }
  },
  "mcp": {
    "servers": {
      "lobster": { "url": "http://127.0.0.1:8000/mcp-gateway" }
    }
  },
  "plugins": {
    "enabled": true,
    "load": {
      "paths": [
        "nodejs/node_modules/@tencent-weixin/openclaw-weixin"
      ]
    },
    "entries": {
      "openclaw-weixin": { "enabled": true }
    }
  }
}
OCEOF
    echo "  [√] OpenClaw 配置模板就绪"
else
    echo "[6] OpenClaw 配置已存在，跳过"
fi

# Step 7: Init .env if not exists
if [ ! -f ".env" ]; then
    cp .env.example .env 2>/dev/null || true
fi

echo ""
echo "=== 构建完成 ==="
echo "将整个 lobster_online/ 目录拷贝到 Windows 机器即可（或保持你本仓库文件夹名）"
echo "目录大小: $(du -sh . | cut -f1)"
echo ""
echo "体积说明（解压后，量级供参考）:"
echo "  - nodejs/node_modules：OpenClaw 及传递依赖，通常数百 MB"
echo "  - browser_chromium：Playwright 离线浏览器（若已放入），常约 300–700MB"
echo "  - python/：嵌入解释器 + Lib/site-packages（install 后）"
echo "  - deps/wheels：离线 pip；deps/ffmpeg：仅 INCLUDE_FFMPEG=1 时拉取（剪辑）"
echo "一键结果包请用: bash scripts/build_result_package.sh（会复检 scripts/report_pack_gaps.py）"
