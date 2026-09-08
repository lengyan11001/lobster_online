# Online 远程控制 Agent

这里是从 BHZN-ToDesk 同步进 Online 的真实 Windows/macOS Agent 源码，包含
WebRTC Video Track、独立 control DataChannel、屏幕采集、输入控制和文件传输。
Online 后端只在“设置 → 远程支持”开关打开后才会启动它；开关关闭时不会导入
`aiortc`、`mss`，也不会扫描设备进程或读取设备身份。

## 当前运行方式

Online 优先使用本目录的隔离 Python 运行时（仅当 `.venv/.ready` 存在），否则使用
已安装的 `BHZN-ToDesk-Agent.exe`。因此源码已在 Online 内，现有客户端没有安装 RTC
依赖时仍能保持原业务不受影响。

## Windows 构建

在 Online 客户端目录执行：

```powershell
cd desktop/todesk_agent
powershell -ExecutionPolicy Bypass -File .\build-windows.ps1
```

脚本只在准备远程支持时创建隔离环境、安装 `requirements.txt`，并在依赖导入检查通过
后写入 `.ready`。输出为 `desktop/todesk_agent/dist/BHZN-ToDesk-Agent.exe`。

## Agent 参数

源码支持 `--config <path>`、`--show-id` 和后台运行的 `--nogui`。配置文件默认保存到：

- Windows：`%APPDATA%\\BHZN-ToDesk\\agent.json`
- macOS：`~/Library/Application Support/BHZN-ToDesk/agent.json`

设备身份仍由 Agent 自己生成并保存，Online 只负责在开关打开时启动、停止和上报状态。
