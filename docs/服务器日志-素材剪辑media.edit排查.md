# 服务器日志：素材剪辑（media.edit）排查

## 必读：能力跑在哪

| 位置 | 是否执行 `media.edit` |
|------|------------------------|
| **`lobster_online` 本机后端**（`LOCAL_API_BASE`，用户解压代码包运行的那份） | **是**。`/api/media-edit/run`、`[media_edit]`、`[media_edit_exec]`、ffmpeg 都在这里。 |
| **`lobster-server`（云端 ECS，只鉴权/积分/商店等）** | **否**。**不要**把 `media_edit` 路由或 `media_edit_exec` 同步进 server；架构见 **`架构说明_server与本地职责.md`**、**`server-ssh-operations.mdc`**。 |

因此：**排查叠字失败，必须在「实际跑 `backend/run.py` 的那台用户机器」上看日志**（或你远程桌面到那台机）。**仅** SSH 到 ECS 上的 `lobster_server` 时，往往只能看到 `save-url`、鉴权等，**没有** `[CHAT] media.edit` **不代表**本机没调剪辑。

---

## 0. 怎么 SSH 连上服务器（与部署脚本同一套变量）

> 本节用于看 **云端 API（lobster-server）** 的日志或部署；**剪辑问题**请再结合上表到 **本机在线版** 日志排查。

云端 API 仓库 **`lobster-server/`** 里已约定连接方式，详见 **`lobster-server/README-部署.md`**。

### 0.1 配置（开发机一次）

在 `lobster-server` 根目录：

```bash
cp .env.deploy.example .env.deploy
# 编辑 .env.deploy，至少包含：
#   LOBSTER_DEPLOY_HOST=root@你的服务器IP
#   LOBSTER_DEPLOY_SSH_KEY=/path/to/your_private_key   # 有密钥时必填路径
#   LOBSTER_DEPLOY_REMOTE_DIR=/root/lobster_server      # 服务器上 git 仓库目录，与 deploy 脚本一致
```

### 0.2 手动 SSH（登上去看日志、与 deploy 同源）

与 `scripts/deploy_from_local.sh` 使用的参数一致：

```bash
cd /path/to/lobster-server
set -a && [ -f .env.deploy ] && . ./.env.deploy && set +a

REMOTE_DIR="${LOBSTER_DEPLOY_REMOTE_DIR:-/root/lobster_server}"
SSH_OPTS="-o StrictHostKeyChecking=accept-new"
[ -n "$LOBSTER_DEPLOY_SSH_KEY" ] && [ -r "$LOBSTER_DEPLOY_SSH_KEY" ] && SSH_OPTS="-i $LOBSTER_DEPLOY_SSH_KEY $SSH_OPTS"

# 登录交互 shell
ssh $SSH_OPTS "$LOBSTER_DEPLOY_HOST"

# 或直接一条命令看日志（不登录）
ssh $SSH_OPTS "$LOBSTER_DEPLOY_HOST" "tail -200 ${REMOTE_DIR}/logs/app.log"
```

若未配置 `LOBSTER_DEPLOY_SSH_KEY`（密码登录），则：

```bash
ssh -o StrictHostKeyChecking=accept-new root@你的服务器IP
```

### 0.3 与「一键部署」的关系

- **`bash scripts/deploy_from_local.sh`**：本机 `git push` 后，用**同一套** `LOBSTER_DEPLOY_*` 在远端 `git pull` + 重启。
- **看日志**：用上面 **0.2** 的 `ssh` 登录或 `ssh ... tail`，**没有单独神秘通道**，就是普通 SSH。

> 说明：Cursor 里的 AI **不能**替你执行本机 `ssh`；你在自己终端按本节执行即可。

---

## 1. 典型部署里日志在哪

| 组件 | 常见路径 / 说明 |
|------|----------------|
| 后端 FastAPI | 如 **`${LOBSTER_DEPLOY_REMOTE_DIR}/logs/app.log`**（例：`/root/lobster_server/logs/app.log`），或 systemd journal |
| MCP（8001） | 若与后端同进程则仍在 `app.log`；若单独进程，看启动重定向或 `journalctl -u <服务名>` |

在线版：**对话与工具调用**主要在后端进程；**`[MCP] tools/call`**、**`[MCP media.edit]`** 若写在 MCP 进程，可能不在 `app.log`，需两处都搜。

---

## 2. 一键筛选（连上服务器后执行）

```bash
APP_LOG=/root/lobster_server/logs/app.log   # 按你 REMOTE_DIR 改

grep -E '\[CHAT\] media\.edit|\[media_edit\]|\[media_edit_auth\]|\[MCP media\.edit\]|\[media_edit_exec\]|media-edit/run' "$APP_LOG" | tail -100
```

若怀疑 MCP 单独日志：

```bash
grep -E '\[MCP\] tools/call name=|\[MCP media\.edit\]' /var/log/*.log 2>/dev/null
# 或你自定义的 mcp.log 路径
```

---

## 3. 关键字含义（便于对照）

| 日志前缀 | 含义 |
|----------|------|
| `[CHAT] media.edit payload` | 对话层即将调用剪辑，`operation` / `asset_id` |
| `[CHAT] media.edit result preview` | MCP 返回给模型的文本（含错误 JSON 前 800 字） |
| `[media_edit] request` / `ok` / `500` | 后端 `/api/media-edit/run` 收到请求或失败 |
| `[media_edit_exec]` | ffmpeg 管线、resolve 素材路径等 |
| `[MCP media.edit] invoke` | MCP 转发到本机后端前的参数摘要 |
| `[MCP media.edit] backend error` | 后端返回 4xx/5xx 及 detail |

**未出现 `[CHAT] media.edit`**：模型可能未调用 `invoke_capability(media.edit)`，先查对话是否走直连 LLM + 工具、技能是否已安装。

---

## 4. 导出一段给他人分析

```bash
tail -500 /root/lobster_server/logs/app.log > /tmp/lobster_tail500.txt
# scp 下载到本机发对方即可
```

---

## 5. 本机依赖（Linux 服务端跑剪辑时）

若后端跑在 **Linux** 上执行 ffmpeg：需 **`deps/ffmpeg/ffmpeg`** 可执行文件，或系统 `PATH` 中有 `ffmpeg`，或设置 **`LOBSTER_FFMPEG_PATH`**。Windows 客户端包里的 **`ffmpeg.exe` 不能在 Linux 上直接执行**。

---

## 6. 与「助手乱给替代方案」相关

对话系统提示已要求：**叠字必须用 `media.edit`**，禁止用文生图或外部软件替代。若仍乱答，确认部署的 **`backend/app/api/chat.py`** 已更新并**重启后端**。

---

*文档随仓库维护；`LOBSTER_DEPLOY_*` 以 `lobster-server/.env.deploy.example` 为准。*
