# 必火AI员工 EXE 打包与启动逻辑

本文说明当前桌面端 `exe` 的定位、打包入口、启动流程和注意事项。当前方案优先追求快速可用：使用 `pywebview` + Microsoft Edge WebView2 把现有 Web 前端以 Windows 桌面软件窗口展示，不重写前端，也不引入 Electron。

## 当前产物定位

当前生成的 `必火AI员工.exe` 是桌面启动器，不是完整离线环境安装包。

它负责：

- 启动本地 MCP 服务：`run_mcp.bat`
- 启动本地后端服务：`run_backend.bat`
- 等待 `http://127.0.0.1:8000/` 就绪
- 使用 WebView2 打开本地前端页面
- WebView2 不可用时回退到系统浏览器

它不负责：

- 自动创建 `.env`
- 内置完整 Python 运行时
- 内置完整 Node.js/OpenClaw 运行时
- 内置完整 Playwright/Chromium 浏览器
- 生成完整环境安装包

`.env` 必须由已有安装/配置流程准备，桌面启动器只读取其中的端口和标题等配置，不会静默创建或覆盖。

## 关键文件

| 文件 | 作用 |
| --- | --- |
| `desktop/launcher.py` | 桌面启动器主逻辑 |
| `desktop/build_desktop_exe.py` | PyInstaller 构建脚本，支持中文 exe 文件名 |
| `desktop/build_desktop_exe.bat` | Windows 入口，只调用 Python 构建脚本 |
| `desktop/run_desktop.bat` | 本地开发运行桌面启动器 |
| `desktop/requirements-desktop.txt` | 桌面启动器依赖 |
| `scripts/create_desktop_shortcut.ps1` | 安装后创建桌面快捷方式 |
| `static/index.html` | WebView 加载的主前端页面 |
| `static/css/common.css` | 通用样式 |

## 构建命令

在项目根目录执行：

```bat
desktop\build_desktop_exe.bat
```

构建完成后输出：

```text
dist\必火AI员工.exe
```

用于本地直接双击测试时，可复制到项目根目录：

```powershell
Copy-Item -LiteralPath "dist\必火AI员工.exe" -Destination "必火AI员工.exe" -Force
```

注意：如果同名 exe 正在运行，Windows 会锁定该文件，覆盖会失败。先关闭已打开的窗口，再重新复制。

## 为什么构建脚本分成 bat + py

`cmd.exe` 对中文命令参数依赖当前代码页。直接在 `.bat` 里写：

```bat
--name "必火AI员工"
```

在部分环境会被解析成乱码，导致 PyInstaller 命令断裂。现在的做法是：

1. `desktop/build_desktop_exe.bat` 保持 ASCII，只负责定位 Python 并调用脚本。
2. `desktop/build_desktop_exe.py` 使用 UTF-8 源码传递 `APP_NAME = "必火AI员工"` 给 PyInstaller。

这样可以稳定生成中文文件名：

```text
必火AI员工.exe
```

## 启动流程

`desktop/launcher.py` 的核心流程如下：

1. 解析项目根目录。
   - 源码运行时从 `desktop/launcher.py` 向上找项目根目录。
   - PyInstaller 打包后从 `sys.executable` 所在目录找项目根目录。
2. 检查 `backend/` 和 `static/` 是否存在。
3. 读取 `.env` 中的可选配置：
   - `PORT`，默认 `8000`
   - `MCP_PORT`，默认 `8001`
   - `LOBSTER_DESKTOP_TITLE`，默认 `必火AI员工`
4. 构造运行环境变量：
   - 设置 `PYTHONPATH` 为项目根目录
   - 设置 `LOBSTER_DESKTOP=1`
   - 如果存在 `browser_chromium/`，设置 `PLAYWRIGHT_BROWSERS_PATH`
   - 如果存在 `nodejs/node.exe`，把 `nodejs/` 加到 `PATH`
5. 如果 MCP 端口未监听，启动 `run_mcp.bat`。
6. 如果后端未就绪，启动 `run_backend.bat`。
7. 等待后端 `http://127.0.0.1:8000/api/health` 或首页可访问。
8. 打开 WebView2 窗口。
9. WebView2/pywebview 启动失败时，自动用系统浏览器打开同一 URL。

## WebView URL 缓存处理

桌面窗口打开的 URL 会追加随机版本参数：

```text
http://127.0.0.1:8000/?desktop=1&v=<timestamp>-<random>
```

原因是 WebView2 会持久化缓存。如果不加版本参数，前端样式修改后，用户双击 exe 可能仍看到旧页面，尤其是布局和滚动样式会表现得像没有更新。

## 窗口标题

默认窗口标题来自 `desktop/launcher.py`：

```python
APP_NAME = "必火AI员工"
```

实际运行时可被 `.env` 覆盖：

```env
LOBSTER_DESKTOP_TITLE=必火AI员工
```

如果用户反馈左上角标题不是 `必火AI员工`，优先检查 `.env` 是否配置了 `LOBSTER_DESKTOP_TITLE`。

## 桌面滚动模型

桌面 WebView 中不能让整个 `body/document` 滚动，否则切换到子页面后，顶部导航会跟随内容一起被卷走。

当前前端样式采用：

- `body` 固定窗口高度，并禁止整页滚动。
- `.dashboard-main` 作为主内容滚动容器。
- 顶部 `.header` 留在主滚动容器外，因此不会跟随子页面内容滚动。
- 首页空状态允许内容自然撑开 `.dashboard-main`，下面的功能卡片可以通过主内容区滚动查看。

相关样式位于：

```text
static/index.html
static/css/common.css
```

验证过的关键行为：

- `body` 的 `overflow` 为 `hidden`
- `body` 的 `position` 为 `fixed`
- `.dashboard-main` 的 `overflow-y` 为 `auto`
- 滚动内容时 `window.scrollY` 保持 `0`
- 顶部 header 坐标保持不变

## 快捷方式逻辑

`scripts/create_desktop_shortcut.ps1` 创建桌面快捷方式时，目标优先级为：

1. 项目根目录 `必火AI员工.exe`
2. 项目根目录旧版 `lobster.exe`
3. `start.bat`

后续安装器应优先指向：

```text
必火AI员工.exe
```

## WebView2 运行环境说明

当前方案依赖用户电脑的 Microsoft Edge WebView2 Runtime。多数 Windows 10/11 机器已自带，但仍可能遇到：

- WebView2 未安装
- WebView2 损坏
- 企业环境禁用相关组件

启动器已做浏览器回退。如果 pywebview/WebView2 失败，会自动打开系统浏览器，不会让用户只看到空白窗口。

生产安装器建议内置或安装 Microsoft Edge WebView2 Runtime，降低“点 exe 页面出不来”的概率。

## 与完整环境安装包的区别

当前 `必火AI员工.exe` 只是桌面启动器。完整离线安装包还需要补齐：

- `python/python.exe`
- `nodejs/node.exe`
- `deps/wheels/`
- `deps/get-pip.py`
- `deps/vc_redist.x64.exe`
- `browser_chromium/`
- OpenClaw 相关 `node_modules`

可用下面命令检查完整包缺口：

```bat
python scripts\report_pack_gaps.py
```

只有这些依赖补齐后，才能做“用户电脑无 Python/Node 环境也能完整安装运行”的完整安装包或自解压包。

## 推荐发布步骤

1. 确认 `.env` 已按目标环境配置。
2. 运行安装依赖流程，确保本机后端/MCP 可启动。
3. 构建桌面启动器：

   ```bat
   desktop\build_desktop_exe.bat
   ```

4. 复制构建产物到项目根目录：

   ```powershell
   Copy-Item -LiteralPath "dist\必火AI员工.exe" -Destination "必火AI员工.exe" -Force
   ```

5. 双击 `必火AI员工.exe` 验证：
   - 窗口左上角标题为 `必火AI员工`
   - 首页可查看下面功能区
   - 切换子页面滚动时顶部导航不动
   - 后端/MCP 日志无启动错误

6. 若要制作完整环境包，再按 `scripts/report_pack_gaps.py` 的缺口补齐离线依赖。
