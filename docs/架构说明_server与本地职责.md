# 在线版架构：Server 与本地职责

## 产品结构（以本段为准）

**`lobster_online` 本机侧职责**（本工作区**无**独立 `lobster/` 目录；以下以本机完整包为准）：

- **相同部分（仍在本机）**：包内包含 **OpenClaw**（`nodejs/` 等），以及对话、MCP 工具链、素材、发布等**执行与算力编排**——在用户本机跑。
- **在线版多出来、且必须走 `lobster_server` 的**：**用户注册登录、支付、积分、鉴权**（JWT 与用户数据以服务器为权威来源）。
- **本机无公网 IP**：需要 **服务器作公网入口与消息转发**（例如微信/企微回调、待处理拉取与代发、其它 webhook），本机通过轮询或长连配合 server 完成闭环。
- **速推**：与当前线上一致，**统一由服务器侧处理**——服务器配置 `SUTUI_SERVER_TOKEN` / `SUTUI_SERVER_TOKENS`（及 MCP 上游），**不**要求用户在本机各配一套直连速推算力；本机 MCP 若参与链路，应对齐为走服务器代转发或等价契约。

> **2026-03-20 起约定（防改错回归）**  
> - **OpenClaw 只跑在用户本机**；**lobster_server 生产环境不启动 OpenClaw**。  
> - **智能对话** `/chat`、`/chat/stream` **须走本机后端**（`LOCAL_API_BASE`），以便本机 OpenClaw + 本机 MCP；**勿**把对话默认打回远端 `API_BASE`。  
> - 双入口：**`API_BASE`** = 账号 / 积分 / 支付 / 鉴权等与**中心**相关的接口；**`LOCAL_API_BASE`** = 对话、素材、发布、OpenClaw 配置等**本机能力**。  
> - 详见 **[在线版架构-对话与OpenClaw.md](./在线版架构-对话与OpenClaw.md)**；Cursor：`.cursor/rules/online-chat-openclaw-local.mdc`、`.cursor/rules/only-online-and-server.mdc`。

## 1. 总体结构

- **前端**：一套静态（HTML/JS/CSS），通常配合 **本机起的 `lobster_online` 后端**（`backend/run.py`）使用，以便同源得到 `LOCAL_API_BASE`。
- **双 BASE**：`API_BASE` → **lobster_server**；`LOCAL_API_BASE` → **本机 lobster_online**。下文表格按此划分，**不再**使用「所有请求只打 `API_BASE`」的旧描述。

---

## 2. lobster_server（云上 / 中心）要做什么

部署在公网，是 **账号、计费、鉴权、公网入口与速推统一出口** 的权威侧。

| 模块 | 职责 |
|------|------|
| **认证** | 注册、登录、/auth/me、验证码；微信等 OAuth 回调与换 token |
| **版本与设置** | /api/edition、与线上一致的中心侧设置/模型列表等（以实际路由为准） |
| **对话** | **不作为在线用户主路径**（对话走本机，见上文）。server 上若仍保留 chat 路由，仅作兼容或非产品默认。 |
| **技能商店与订单** | 商店、安装/卸载、解锁与支付相关接口（以 server 实现为准） |
| **计费与充值** | 定价、下单、支付回调、积分变更 |
| **能力与积分** | pre-deduct、record-call、refund 等 **扣积分真相源**；本机可代理转发并带用户 JWT |
| **速推** | **统一走服务器**：`SUTUI_SERVER_TOKEN(S)`、服务器侧 MCP 调上游；与用户本机是否直连 xskill 无关 |
| **无公网 IP** | 微信/企微等 **callback、pending、submit-reply** 等：公网只打到 server，本机通过 server 协作 |
| **发布 / 素材 / OpenClaw 配置 / 本地 MCP 注册表** | **本机**（见第 3、6 节）；server 不替代本机浏览器与本地文件 |

**数据**：用户、积分、订单、企微队列等 **中心数据** 在 server；发布浏览器 profile、本地素材文件、本机 sqlite 等在用户机器。

---

## 3. 本地要做什么

- **本机跑 `lobster_online` 后端（推荐形态）**：提供静态页 + **对话、素材、发布、OpenClaw、本机 MCP**；`AUTH_SERVER_BASE` 指向 server，用 server JWT 做身份校验。
- **前端双 BASE**：登录/积分/支付等 → `API_BASE`（server）；对话与本地能力 → `LOCAL_API_BASE`（本机后端，常与页面同源）。

### 3.1 企微与无公网 IP

- server 收 **callback**、暴露 **pending / submit-reply**；本机轮询 pending、用 OpenClaw/对话生成回复后 **submit-reply** 由云端代发到企微。

### 3.2 发布与浏览器

- **发布账号、Playwright、素材目录** 在用户本机（见第 6 节）；不依赖 server 上跑用户浏览器。

---

## 4. 后续功能放在哪

- **注册登录、支付、积分、鉴权、速推统一出口、公网回调** → **lobster_server**。
- **OpenClaw、对话主路径、本机 MCP、发布浏览器、本地素材** → **lobster_online** 本机后端；前端用 `LOCAL_API_BASE`。
- **用户身份**：JWT 由 server 签发；本机接口通过 `AUTH_SERVER_BASE` + `/auth/me` 或等价方式校验，**不**用本机 `SECRET_KEY` 冒充同一套用户体系。

---

## 5. 小结

| 角色 | 职责 |
|------|------|
| **lobster_server** | 注册登录、支付、积分、鉴权；积分预扣/记录；**速推统一走服务器**；无公网 IP 时的 **Webhook / 企微** 公网入口与转发。 |
| **lobster_online（本机）** | **含 OpenClaw**；对话、本机 MCP、素材、发布；轮询/配合 server 完成企微等链路。 |
| **前端** | `API_BASE` → server；`LOCAL_API_BASE` → 本机后端（对话与本地能力）。 |

---

## 6. 发布 / 素材已迁至客户端（当前实现）

- **发布账号、发布任务、素材** 已全部在 **本地（lobster_online）** 实现：浏览器 profile、素材目录、`run_publish_task` 均在用户本机执行。
- 前端：发布与素材相关请求使用 **LOCAL_API_BASE**（默认同源），即请求发往本机运行的 lobster_online 后端。
- 本地后端：需配置 **AUTH_SERVER_BASE**（认证中心 lobster_server 地址），用 server 的 `/auth/me` 校验请求中的 Bearer token，按 `user_id` 做权限与数据隔离。
- **lobster_server** 已不再挂载发布、素材路由；保留认证、技能/计费、企微公网侧、积分与速推统一处理等；**在线用户对话主路径在本机**（server 上若仍有 chat 相关路由，不作为产品默认入口）。
