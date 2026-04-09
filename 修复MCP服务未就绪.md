# 修复 MCP 服务未就绪问题

## 问题现象
- 无法查询速推模型列表
- 提示"速推能力服务（MCP）未就绪"
- `http://本机IP:8000/api/health` 返回 `mcp.reachable: false`

## 诊断步骤

### 1. 检查 MCP 服务是否运行
```bash
# Windows
netstat -ano | findstr ":8001"

# macOS/Linux
lsof -i :8001
# 或
netstat -an | grep 8001
```

### 2. 检查健康状态
访问：`http://127.0.0.1:8000/api/health`

期望返回：
```json
{
  "status": "ok",
  "mcp": {
    "reachable": true,
    "tools_count": 9
  }
}
```

如果 `mcp.reachable` 为 `false`，说明 MCP 服务未启动。

### 3. 检查 MCP 日志
```bash
# 查看 MCP 启动日志
cat mcp.log
# 或
tail -50 mcp.log
```

### 4. 检查配置文件
确认以下文件存在且配置正确：

**`upstream_urls.json`**:
```json
{
  "sutui": "https://api.xskill.ai/api/v3/mcp-http"
}
```

**`mcp/capability_catalog.json`**:
- 应包含 `sutui.search_models` 等能力定义

**`sutui_config.json`** (可选，用于单机版):
```json
{
  "token": "你的速推Token"
}
```

## 解决方案

### 方案1: 重启服务（最常见）
```bash
# Windows
stop.bat
start.bat

# macOS/Linux
pkill -f "mcp.*8001"
pkill -f "uvicorn.*8000"
# 然后重新启动
```

### 方案2: 手动启动 MCP 服务
```bash
# Windows
cd lobster_online
python -m mcp --port 8001

# macOS/Linux
cd lobster_online
python3 -m mcp --port 8001
```

### 方案3: 检查端口占用
如果 8001 端口被占用：
```bash
# Windows - 查找占用进程
netstat -ano | findstr ":8001"
taskkill /F /PID <进程ID>

# macOS/Linux
lsof -ti:8001 | xargs kill -9
```

### 方案4: 检查 Python 依赖
```bash
# 确保所有依赖已安装
pip install -r requirements.txt

# 测试 MCP 模块导入
python -c "from mcp import http_server; print('OK')"
```

### 方案5: 检查速推 Token 配置

**在线版（lobster_online）**:
- 用户需要在「系统配置」中配置速推 Token
- 或通过速推账号登录自动获取

**仅本机配置（无独立 `lobster/` 目录时）**:
- 在 `sutui_config.json` 中配置 Token（若使用）
- 或在「系统配置」中填写

## 验证修复

### 1. 检查健康状态
```bash
curl http://127.0.0.1:8000/api/health
```

### 2. 测试 MCP 连接
运行测试脚本：
```bash
python3 test_mcp.py
```

### 3. 测试查询模型列表
在界面中尝试查询速推模型列表，应该能正常显示。

## 常见错误

### 错误1: "端口 8001 已被占用"
**解决**: 停止占用进程或修改 MCP 端口（在 `.env` 中设置 `MCP_PORT=8002`）

### 错误2: "无法导入 mcp 模块"
**解决**: 
- 确认在 `lobster_online` 目录下运行
- 检查 Python 路径配置
- 重新安装依赖

### 错误3: "MCP 服务启动但 tools_count=0"
**可能原因**:
- `capability_catalog.json` 文件损坏或为空
- `upstream_urls.json` 配置错误

**解决**: 检查配置文件格式和内容

### 错误4: "查询模型列表时提示 Token 未配置"
**解决**: 在「系统配置」中填写速推 Token，或使用速推账号登录

## 快速修复脚本

创建 `fix_mcp.sh` (macOS/Linux) 或 `fix_mcp.bat` (Windows):

```bash
#!/bin/bash
# fix_mcp.sh
echo "停止现有服务..."
pkill -f "mcp.*8001" 2>/dev/null
pkill -f "uvicorn.*8000" 2>/dev/null
sleep 2

echo "检查端口..."
if lsof -i :8001 >/dev/null 2>&1; then
    echo "警告: 8001 端口仍被占用"
    lsof -i :8001
fi

echo "启动 MCP 服务..."
cd "$(dirname "$0")"
python3 -m mcp --port 8001 > mcp.log 2>&1 &
sleep 2

echo "检查服务状态..."
if curl -s http://127.0.0.1:8001/mcp -X POST -H "Content-Type: application/json" -d '{"jsonrpc":"2.0","id":"test","method":"tools/list","params":{}}' | grep -q "tools"; then
    echo "✓ MCP 服务运行正常"
else
    echo "✗ MCP 服务未正常启动，请查看 mcp.log"
fi
```
