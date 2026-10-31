# 安装引导（只在缺的时候用）

原则：**已经有的一句话带过，不提安装**；缺什么才给装法。装法给多种：先放用户当前在用的那种（标「推荐」），其余列在下面让用户挑。装完再检查一次确认。

## 1. Renoise MCP

### 怎么判断有没有
直接调 Renoise 的 `get_account`：
- 成功 → 「Renoise 已连接，余额 X 积分」，继续干活。
- 工具不存在 / 调用报未连接 → 缺，按下面引导。
- 报「disabled in connector settings」→ 不是没装，是某个工具被关了：让用户在连接器设置里把它打开。
- 报未登录 / 授权过期 → 不用重装，重新登录授权就行（Claude 连接器点「Connect」；Codex `codex mcp login renoise`）。

### 装法（先有 Renoise 账号；全部走浏览器登录授权，不用复制 API key）

还没有账号 → 先去注册：**[https://renoise.ai/mcp?utm_medium=xcom&utm_source=official](https://renoise.ai/mcp?utm_medium=xcom&utm_source=official)**（给用户时原样给这条链接，保留 utm 追踪参数，不要改写成 renoise.ai 首页）。

| 用的是 | 怎么装 |
|---|---|
| **Claude 桌面端 / claude.ai 网页** | 设置 → 连接器（Connectors）→ Add custom connector → 名称 `Renoise`，地址 `https://renoise.ai/api/public/mcp` → 继续 → 登录 renoise.ai 点同意 |
| **Claude Code** | 依次输入 `/plugin marketplace add ArcoCodes/renoise-plugins-official`、`/plugin install renoise@renoise-plugins-official`，首次调用时会打开浏览器登录 |
| **Codex（CLI / 桌面）** | 终端运行 `codex mcp add renoise --url https://renoise.ai/api/public/mcp`，再 `codex mcp login renoise` |
| **OpenClaw** | `openclaw plugins install renoise --marketplace ArcoCodes/renoise-plugins-official` |
| **其他支持远程 MCP 的客户端**（Cursor、Windsurf、Cline 等） | 在它的 MCP 配置里加一个远程服务器，地址 `https://renoise.ai/api/public/mcp`。常见写法：`{"mcpServers": {"renoise": {"url": "https://renoise.ai/api/public/mcp"}}}`（字段名以客户端文档为准） |
| **不确定 / 想看最新** | 打开 renoise.ai/plugin，官网列了各客户端的最新装法 |

装完：Claude 系要重开对话（或在连接器里点重连）才能看到工具；Codex 重开会话。然后再调一次 `get_account`。

## 2. 本机环境（剪辑时才需要）
`bash scripts/doctor.sh` 会逐项检查：有的打 ✓ 跳过，缺的才列出多种装法。**系统软件让用户自己装，不要替用户执行安装命令**（Python 包可以在用户同意后帮装）。

| 缺的 | macOS | Windows | Linux | 其他 |
|---|---|---|---|---|
| ffmpeg | `brew install ffmpeg` | `winget install Gyan.FFmpeg` 或 `choco install ffmpeg` | `sudo apt install ffmpeg` | 官网 ffmpeg.org 下载 |
| Python 3 | `brew install python` | `winget install Python.Python.3.12` | `sudo apt install python3 python3-pip` | python.org 下载；或 conda |
| numpy / Pillow | `python3 -m pip install numpy pillow` | 同左 | 同左 | `conda install numpy pillow` |
| Node 18+ | `brew install node` | `winget install OpenJS.NodeJS.LTS` | `sudo apt install nodejs npm` 或 nvm | nodejs.org 下载；`nvm install --lts` |
| whisper-cli | `brew install whisper-cpp` | 从 github.com/ggerganov/whisper.cpp Releases 下载 | 源码编译（`cmake -B build && cmake --build build`） | — |
| whisper 模型 | 下载 `ggml-medium-q5_0.bin`（huggingface.co/ggerganov/whisper.cpp）放到 `~/models/` | 同左 | 同左 | 或 `export WHISPER_MODEL=模型路径` |

Remotion 不用单独装：`scripts/new_edit.sh` 起工程时会先从本机已有的 Remotion 工程克隆，没有才 `npm install`；npm 源连不上时换镜像 `npm config set registry https://registry.npmmirror.com`。

## 3. 数字人形象
persona.json 的 reference_material 在当前账号能用（`view_image` 能打开）→ 直接用。用不了（换了账号、素材过期、别人的包）→ 按 references/new-avatar.md 用用户自己的照片重做。
