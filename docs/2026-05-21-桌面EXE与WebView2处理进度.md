# 2026-05-21 桌面 EXE 与 WebView2 处理进度

## 背景

用户机器安装 `lobster_online_client_code_ota_20260521171048.zip` 后，双击 `必火AI员工.exe` 弹出：

> 当前电脑的 WebView2/pywebview 环境不可用，已自动改用系统浏览器打开客户端。

排查发现：

- `lobster_online_client_code_ota_20260521124649.zip` 里的 `必火AI员工.exe` 是约 15.9 MB 的 PyInstaller 自包含启动器。
- `lobster_online_client_code_ota_20260521171048.zip` 里的 `必火AI员工.exe` 变成约 103 KB 的 C# stub。
- 103 KB stub 只负责拉起本机 Python 去运行 `desktop/launcher.py`，依赖目标机已有 Python/pywebview 环境。
- 15.9 MB PyInstaller 版本会把 Python/pywebview 这一层封进 exe，但仍然依赖系统 Microsoft Edge WebView2 Runtime。

结论：15 MB exe 可以解决“缺 Python/pywebview”的机器启动问题，但不能解决“缺 WebView2 Runtime”的机器启动问题。

## 当前处理结果

### 1. 恢复 15 MB 自包含 EXE

已把 `desktop/build_desktop_exe.py` 从 C# stub 构建方式恢复为 PyInstaller onefile 构建方式。

当前构建行为：

- 使用 `python/python.exe` 优先构建。
- 先从 `desktop/wheels` 安装 `pywebview==6.2.1`。
- 通过 PyInstaller 生成 `dist/必火AI员工.exe`。
- 构建完成后自动复制到项目根目录 `必火AI员工.exe`，避免 OTA 带出 103 KB stub。

当前已重新构建：

- `D:\lobster_online\必火AI员工.exe`
- 大小约 `15,990,059` 字节。

旧包对照：

- `lobster_online_client_code_ota_20260521124649.zip` 中 exe 约 `15,965,933` 字节。
- 当前 exe 已恢复到同类启动方式。

### 2. WebView2 安装边界

明确边界：

- 不允许在 exe 启动阶段下载或安装依赖。
- 依赖安装只允许发生在 `install.bat` 阶段。

已撤销启动器中“启动时自动安装 WebView2”的逻辑。当前 `desktop/launcher.py` 不再执行 WebView2 下载/安装。

### 3. install.bat 增加 WebView2 检测/安装

已在 `install.bat` 增加 `[7b/7] Checking Microsoft Edge WebView2 Runtime...`。

逻辑：

1. 先查注册表判断 WebView2 Runtime 是否已安装。
2. 已安装则跳过。
3. 未安装时，优先使用包内：

   `desktop\webview2\MicrosoftEdgeWebView2RuntimeInstallerX64.exe`

4. 如果包内没有，且不是 `LOBSTER_OFFLINE_ONLY=1`，则在 install 阶段联网下载 Microsoft WebView2 Evergreen Bootstrapper。
5. 安装命令：

   `MicrosoftEdgeWebView2RuntimeInstallerX64.exe /silent /install`

6. 安装失败只警告，不阻断整个安装；但后续 exe 可能退回系统浏览器。

### 4. OTA 带 WebView2 Bootstrapper

已下载 1.7 MB 的 Microsoft WebView2 Evergreen Bootstrapper：

`D:\lobster_online\desktop\webview2\MicrosoftEdgeWebView2RuntimeInstallerX64.exe`

已修改 `scripts/pack_client_code_ota.py`，把 `desktop/webview2` 加入 OTA 路径。

注意：

- 这个 1.7 MB 文件不是完整离线 Runtime。
- 它是微软官方 Bootstrapper，安装时仍可能联网下载 WebView2 Runtime。
- 不使用 300 MB Fixed Version Runtime，因为包体太大。

## 当前文件变更

已确认的有效变更：

- `desktop/build_desktop_exe.py`
  - 恢复 PyInstaller onefile 构建。
  - 构建后复制 exe 到项目根目录。

- `install.bat`
  - 新增 WebView2 Runtime 检测和 install 阶段安装。

- `scripts/pack_client_code_ota.py`
  - 保留之前 OTA 带 `static/hifly_previews` 的改动。
  - 新增 `desktop/webview2` 到 OTA 清单。

- `必火AI员工.exe`
  - 已由 103 KB stub 恢复为约 15.9 MB 自包含启动器。

新增未跟踪文件：

- `desktop/webview2/MicrosoftEdgeWebView2RuntimeInstallerX64.exe`

运行态未跟踪文件，不属于本次功能：

- `chat_storage/`
- `openclaw/.lobster_plugin_state_backup.json`

## 后续打 OTA 包时要做

1. 确认根目录 exe 是 15 MB 版本：

   ```bat
   dir D:\lobster_online\必火AI员工.exe
   ```

2. 如需重新构建 exe：

   ```bat
   cd /d D:\lobster_online
   python\python.exe desktop\build_desktop_exe.py
   ```

3. 打 OTA 包：

   ```bat
   cd /d D:\lobster_online
   python scripts\pack_client_code_ota.py
   ```

4. 检查 OTA 包内必须包含：

   - `必火AI员工.exe`，大小约 15 MB
   - `desktop/webview2/MicrosoftEdgeWebView2RuntimeInstallerX64.exe`，大小约 1.7 MB
   - `static/hifly_previews/`

## 用户机器处理方式

对于已经出现 WebView2 弹窗的机器：

1. 覆盖新版 OTA。
2. 先运行 `install.bat`。
3. 再运行 `必火AI员工.exe`。

如果用户跳过 `install.bat` 直接启动 exe，缺 WebView2 的机器仍然可能弹窗或退回系统浏览器，这是预期行为。

## 设计取舍

当前方案：

- 包体增量小：只增加约 1.7 MB Bootstrapper。
- 启动不做安装，避免用户双击 exe 后卡在依赖安装。
- 首次安装/修复依赖统一放在 `install.bat`。
- 仍保留 15 MB 自包含 exe，避免目标机缺 Python/pywebview 造成启动失败。

不采用方案：

- 不把 300 MB WebView2 Fixed Runtime 放进 OTA。
- 不在 exe 启动时下载或安装 WebView2。
