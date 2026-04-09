#!/usr/bin/env python3
"""快速测试 MCP 服务是否正常运行"""
import sys
import socket
import httpx
import json

MCP_PORT = 8001
MCP_URL = f"http://127.0.0.1:{MCP_PORT}/mcp"

def test_port():
    """测试端口是否开放"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2)
        result = s.connect_ex(("127.0.0.1", MCP_PORT))
        s.close()
        if result == 0:
            print(f"✓ 端口 {MCP_PORT} 已开放")
            return True
        else:
            print(f"✗ 端口 {MCP_PORT} 未开放")
            return False
    except Exception as e:
        print(f"✗ 检查端口失败: {e}")
        return False

def test_mcp_service():
    """测试 MCP 服务是否响应"""
    try:
        with httpx.Client(timeout=5.0) as client:
            response = client.post(
                MCP_URL,
                json={
                    "jsonrpc": "2.0",
                    "id": "test",
                    "method": "tools/list",
                    "params": {}
                }
            )
            if response.status_code == 200:
                data = response.json()
                tools = data.get("result", {}).get("tools", [])
                print(f"✓ MCP 服务正常，可用工具数: {len(tools)}")
                if tools:
                    print(f"  工具列表: {', '.join([t.get('name', '') for t in tools[:5]])}...")
                else:
                    print("  警告: 工具列表为空，可能配置有问题")
                return True
            else:
                print(f"✗ MCP 服务返回错误: HTTP {response.status_code}")
                print(f"  响应: {response.text[:200]}")
                return False
    except httpx.ConnectError:
        print(f"✗ 无法连接到 MCP 服务 ({MCP_URL})")
        print("  请确认 MCP 服务已启动")
        print("  尝试运行: python -m mcp --port 8001")
        return False
    except Exception as e:
        print(f"✗ 测试 MCP 服务失败: {e}")
        return False

if __name__ == "__main__":
    print("=" * 50)
    print("MCP 服务连接测试")
    print("=" * 50)
    print()
    
    # 测试端口
    port_ok = test_port()
    print()
    
    # 测试服务
    if port_ok:
        service_ok = test_mcp_service()
        print()
        
        if service_ok:
            print("=" * 50)
            print("✓ MCP 服务运行正常")
            print("=" * 50)
            sys.exit(0)
        else:
            print("=" * 50)
            print("✗ MCP 服务异常")
            print("=" * 50)
            print("\n建议:")
            print("1. 检查 mcp.log 查看错误信息")
            print("2. 尝试手动启动: python -m mcp --port 8001")
            print("3. 检查 Python 依赖是否完整")
            sys.exit(1)
    else:
        print("=" * 50)
        print("✗ MCP 服务未启动")
        print("=" * 50)
        print("\n建议:")
        print("1. 运行 start.bat 启动服务")
        print("2. 检查是否有进程占用 8001 端口")
        print("3. 查看启动日志确认错误")
        sys.exit(1)
