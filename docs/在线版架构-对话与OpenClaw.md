# 在线版架构约定：对话走本机 OpenClaw

> **目的**：固定产品形态，避免后续改代码时又把「智能对话」打到 **lobster_server**，进而在 **远端** 误启 OpenClaw 或误用 `127.0.0.1:18789`（服务器上的空网关）。

## 原则（必须遵守）

1. **OpenClaw 只跑在用户本机**  
   `lobster_online` 完整包内含 `nodejs/`、`openclaw`，网关默认 **本机** `http://127.0.0.1:18789`（或 `.env` 中的 `OPENCLAW_GATEWAY_URL`）。

2. **智能对话 `/chat`、`/chat/stream` 必须走本机后端**  
   请求应发往 **`LOCAL_API_BASE`**（与 `backend/run.py` 同源），由 `lobster_online/backend/app/api/chat.py` 处理：直连 LLM、本机 MCP、本机 `_try_openclaw`。

3. **lobster_server 不承担「用户对话主路径」**  
   服务器提供：注册登录、支付、积分、鉴权、能力代转发、无外网 IP 时的回调入口等。**不要在服务器上为在线用户启动 OpenClaw** 作为对话依赖。

## 与「仅本机、无账号中心」形态的对比

**一句话**：`lobster_online` **包含 OpenClaw**，执行面在本机；**多出**的注册登录、支付、积分、鉴权走 **server**；**无公网 IP** 时消息经 **server 转发/入口**；**速推**与线上一致，**统一由 server 侧**处理（服务器 Token / MCP 上游），不要求用户本机各配一套直连速推。

| 能力 | 仅本机后端（无独立 `lobster/` 目录） | 在线 lobster_online |
|------|--------------------------------------|----------------------|
| OpenClaw / 本机 MCP / 发布 / 素材 | 本机 | 本机（同上） |
| 注册登录、支付、积分、鉴权 | 无或简化 | **lobster_server** |
| 无外网 IP 的回调、统一出口 | 自行解决 | **经 server 转发/入口** |
| 速推算力 | 本机配置 Token 等 | **server 统一配置与转发** |

## 前端调用约定（改前必查）

- **`API_BASE`**：远端 `lobster_server` — 用于登录、`/auth/*`、`/api/edition`、积分/充值等与**账号、计费**相关的接口。
- **`LOCAL_API_BASE`**：本机 `lobster_online` 后端 — 用于素材上传、发布、**OpenClaw 配置/重启**、以及 **应走本机的对话**。

### 当前实现注意（易踩坑）

- `static/js/chat.js` 中流式对话若仍使用 `API_BASE + '/chat/stream'`，则对话在 **服务器** 上执行，与「对话走本机 OpenClaw」**不一致**。  
  对齐架构时应改为使用 **`LOCAL_API_BASE`**（或单独的 `CHAT_BASE`，默认等于 `LOCAL_API_BASE`），并保证本机后端已启动、且携带合法 `Authorization`。

### 其他引用

- E2E 脚本示例：`scripts/e2e_upload_again_creation.py` 若拼接 `BASE + '/chat/stream'`，需与上述约定一致（本机或服务端二选一，**在线产品应本机**）。

## 相关文档

- 服务端侧说明（禁止在 server 上依赖 OpenClaw）：`lobster-server/docs/在线版架构-对话与OpenClaw.md`
- Cursor 规则：`.cursor/rules/online-chat-openclaw-local.mdc`
