# 猎聘技能（Online 本地浏览器驱动）

> 目标：把「HR 提需求 → AI 组织操作 → 出结果」做成 Online 里的一个技能。
> 模式与抖音发布 / 抖音获客一致：**用户先扫码登录，然后由客户端驱动浏览器做事**。

## 一、整体思路

```
用户需求（自然语言）
   │
   ├─ (1) 能力目录（本文件 + skill_registry.json 的 capabilities）交给 LLM
   │        LLM 判断：要搜什么词、要不要过滤、要不要发消息、要不要导出
   │
   ├─ (2) LLM 逐步调用能力（liepin.candidates.search → liepin.report.export …）
   │        每次调用 = 一次真实的界面操作（打开页面/输入/点击/读取）
   │
   └─ (3) 台账 + 结果
            所有动作写 _lobster_runtime/liepin/ledger.json（可去重、可汇报）
            结果直接回给对话（表格/文件路径）
```

**为什么这样设计**：猎聘只能通过真实登录态 + 真实界面操作拿到数据，所以能力要「小、稳、可审计」；
LLM 不直接点页面，只组合这些能力。这样换搜索词、换目标人群都不用改代码。

## 二、站点结构（实测，2026-10-08）

### 1. 顶部导航（登录后）
`人才推荐 / 职位管理 / 搜索人才 / 沟通 / 人才管理 / 猎头服务 / 提效服务 / 我的专属顾问 / 招聘工作台 / 我的权益 / <账号> / 设置`

### 2. 搜索人才页 `https://lpt.liepin.com/search`
- 搜索框：`input[placeholder*="搜职位"]`（提示语：搜职位/公司/行业等，中文用空格隔开）
- 开关：`不限职位` / `包含全部关键词`；按钮：`搜索`
- 筛选面板（可用于能力参数）：
  - 职位（全部职位）、公司（全部公司）、清除全部
  - 目前城市：不限 / 印度尼西亚 / 亚洲 / 越南 / 深圳 / 东莞 / 其他
  - 期望城市、经验（不限 / 在校-应届 / 1-3年 / 3-5年 / 5-10年 / 自定义）
  - 教育经历（不限 / 本科 / 硕士 / 博士-博士后 / 大专 / 中专-中技 / 高中及以下）、统招要求、院校要求
  - 其他筛选：活跃状态 / 求职状态 / 跳槽频率 / 年龄要求 / 性别要求 / 语言要求 / 毕业年份 / 当前行业 / 期望行业
- 结果区：`共有 N 份简历` + 卡片（一页约 20–30 张）

### 3. 候选人卡片字段（顺序固定，可直接解析）
```
<活跃标签> | 姓名(平台脱敏，如 施先生/教**) | 38岁 | 15年 | 本科 | 现居城市
| 期望： | 期望职位+薪资 | 行业 | 公司1 | 职位+起止时间 | 公司2 | 职位+起止时间
| 学校 | 专业+学历(统招/非统招)+时间 | 操作区(立即沟通 / 继续沟通)
```
- `立即沟通` = 还没建立沟通关系；`继续沟通` = 已有会话
- 列表页**不显示**电话/微信，需要开简历详情（消耗查看权益）

### 4. 沟通页 `https://lpt.liepin.com/chat/im`（在线沟通）
- 左栏：会话列表 + `搜索姓名` 输入框；Tab：`全部 / 新招呼(N) / 我发起的 / 我回复的 / 有简历 / 超级聊聊 / 不合适`
- 工具条：`批量处理 / 筛选 / 快捷回复 / 通过筛选 / 不合适`
- 会话行：姓名 | 职位 | 最新消息 | 时间 | 未读数
- 打开某个会话后，右下出现输入区与 `发送` 按钮

### 5. 已知限制（必须写进能力实现）
| 限制 | 表现 | 处理 |
|---|---|---|
| 风控置空 | 搜索后页面变 `about:blank` | 关掉窗口重开、重试；一次页面只做 1–2 次搜索 |
| 页面未 hydration | 找不到搜索框 | 等输入框出现（最多 60s）再操作；点击用 force |
| 权益 | 开简历详情消耗查看权益；发消息消耗沟通权益 | 默认拒绝，必须 `confirm=true`；台账去重 |
| 姓名脱敏 | 列表页 `教**` | 需要全名就开详情；沟通页里是真实姓名 |

## 三、能力清单（已登记到 skill_registry.json）

| 能力 id | 作用 | 关键参数 | 写操作 |
|---|---|---|---|
| `liepin.browser.open` | 启动/重启浏览器窗口、返回登录态 | `action: start/restart/status` | 否 |
| `liepin.login.status` | 登录状态/账号 | — | 否 |
| `liepin.candidates.search` | 搜索并解析候选人卡片 | `query, limit, filters{must_include, cities, min_years}` | 否 |
| `liepin.candidates.detail` | 打开简历详情（补全名/经历） | `name, age, confirm` | **是（权益）** |
| `liepin.chat.list` | 读沟通会话列表 | `limit` | 否 |
| `liepin.chat.send` | 按话术发消息 | `target, text, confirm` | **是（触达）** |
| `liepin.ledger.read` | 读动作台账 | `kind` | 否 |
| `liepin.report.export` | 导出 Excel/CSV | `rows, format, filename` | 否 |

## 四、实现位置

- 浏览器驱动：`backend/liepin_origin/liepin_browser.py`
  （内置 Chromium + `--remote-debugging-port=9222`，登录态复用；搜索/解析/沟通/发送/台账）
- 能力分发：`backend/liepin_origin/liepin_actions.py`
- HTTP 入口：`backend/liepin_origin/liepin_api.py` → `POST /api/liepin/action`、`GET /api/liepin/status`、`GET /api/liepin/ledger`
- 挂载：`backend/app/api/liepin_origin.py` → `app.include_router(liepin_origin_router)`
- 技能包：`skill_registry.json` → `liepin_recruit_skill`
- 运行产物：`_lobster_runtime/liepin/`（`ledger.json`、`exports/`）

## 五、LLM 执行契约（给模型看的提示词骨架）

```
你可以操作「猎聘招聘」技能，能力有：<capabilities>
规则：
1. 先调 liepin.browser.open 确认登录；未登录就提示用户扫码，不要继续。
2. 搜索用 liepin.candidates.search，一次一个关键词；需要更多结果就换词，不要连续狂搜（风控）。
3. 只有 confirm=true 才能发消息/开简历；发送前先检查 liepin.ledger.read 避免重复。
4. 结果必须结构化返回：岗位/公司/姓名/年龄/年限/履历/匹配理由。
5. 需要给领导的表格就调 liepin.report.export 出 Excel，并把路径返回给用户。
```
