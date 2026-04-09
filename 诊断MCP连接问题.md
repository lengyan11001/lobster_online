# MCP 服务连接问题诊断

## 问题现象
龙虾提示连不上 8001 端口（MCP 服务）

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

### 2. 检查 MCP 日志
```bash
# 查看 MCP 日志（如果存在）
cat mcp.log
# 或
tail -50 mcp.log
```

### 3. 手动启动 MCP 服务测试
```bash
# Windows
cd lobster_online
python -m mcp --port 8001

# macOS/Linux
cd lobster_online
python3 -m mcp --port 8001
```

### 4. 检查端口是否被占用
如果端口被占用，需要先停止占用进程：
```bash
# Windows - 停止占用 8001 端口的进程
netstat -ano | findstr ":8001"
taskkill /F /PID <进程ID>

# macOS/Linux
lsof -ti:8001 | xargs kill -9
```

## 常见原因和解决方案

### 原因1: MCP 服务未启动
**解决方案**: 使用 `start.bat` 或 `start_online.bat` 启动服务

### 原因2: 端口被占用
**解决方案**: 
1. 运行 `stop.bat` 停止所有服务
2. 检查并手动停止占用 8001 端口的进程
3. 重新启动服务

### 原因3: MCP 服务启动失败
**可能原因**:
- Python 依赖缺失
- 模块导入错误
- 配置文件错误

**解决方案**:
1. 检查 Python 环境: `python --version` 或 `python3 --version`
2. 安装依赖: `pip install -r requirements.txt`
3. 手动测试导入: `python -c "from mcp import http_server; print('OK')"`

### 原因4: 防火墙或网络问题
**解决方案**: 检查防火墙设置，确保 8001 端口未被阻止

## 快速修复

### Windows
```batch
# 1. 停止所有服务
stop.bat

# 2. 等待几秒
timeout /t 3

# 3. 重新启动
start.bat
```

### macOS/Linux
```bash
# 1. 停止所有服务
pkill -f "mcp.*8001"
pkill -f "uvicorn.*8000"

# 2. 等待几秒
sleep 3

# 3. 重新启动
# 使用 start_online.sh 或手动启动
```

## 验证 MCP 服务是否正常

访问健康检查接口：
```
http://127.0.0.1:8000/api/health
```

应该返回：
```json
{
  "status": "ok",
  "mcp": {
    "reachable": true,
    "tools_count": 9
  }
}
```

如果 `mcp.reachable` 为 `false`，说明 MCP 服务未正常启动。
