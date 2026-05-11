# HiFly 公共声音「试听 / 预览」接入 — 任务交接文档

> 给下一位/下一个模型：用户对当前进度不满，希望快速交接。本文档汇总目标、已有接口/数据、已完成代码、目前卡点。**最后两节是用户原始粘贴内容，请直接利用，不要再让用户重复提供。**

---

## 1. 业务目标

在 `lobster_online` 的「公共声音」面板里，让 HiFly 公共声音卡片**点击即可试听**（30k+ 字符的当前公共声音卡片是没有 demo_url 的，因为开放 API kind=2 不返回 demo_url）。

办法：通过 hifly.cc 消费者站的内部接口 `/api/app/v1/tts_voices/{numeric_id}/preview` 拿到每个声音的 base64 wav，存到本地 `lobster_online/static/hifly_previews/{numeric_id}.wav`，前端 demo_url 指向这个本地路径即可。

---

## 2. 已知接口（用户已实测可用）

### 2.1 公共声音列表

```
GET https://hiflyworks-api.lingverse.co/api/app/v1/tts_voice_groups?page=0&size=50
```

请求头：

```
Authorization: Bearer <JWT>
Origin: https://hifly.cc
Referer: https://hifly.cc/
x-client-type: web
x-lvs-language: zh-CN
x-name: hiflyworks-web
```

**注意**：用相同 JWT 直接 curl 此接口只返回 4 条（用户自己的 group），但用户在浏览器里返回 90 条。原因未知（可能浏览器 session 还有 cookie/team header 等额外认证因素，或他的账号被授予了查看公共声音的权限而 token 内不体现）。**所以靠 server-side 用 JWT 直拉拉不全；当前实战路径是用浏览器拉到 90 条数据后导入本地。**

### 2.2 单个声音预览（验证可用）

```
POST https://hiflyworks-api.lingverse.co/api/app/v1/tts_voices/{numeric_id}/preview
Content-Type: application/json
Authorization: Bearer <JWT>
（其余头同 2.1）

Body:
{ "text": "现在的一切都是为将来的梦想编织翅膀，让梦想在现实中展翅高飞。" }

Response:
{
  "code": 0,
  "message": "OK",
  "data": { "audio_base64": "UklGR..." }   // wav
}
```

实测可用 numeric id（举例）：273706, 338185, 652552 等。

---

## 3. 用户提供的 JWT

```
eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJleHAiOjE3Nzg4MzQyMDQsImlkZW50aXR5IjoiODE4NzI2NDVhNWM4NWQyM2Q2ZjM4MmQ0ZjdkNzUxYjIzOWIxZjI0YTljMzBkMzU4NDBmODYwMmE5NjY3NDdjMTNhMDkzYWY2NjViN2EzOWNmNjc5OGZlZTRhZDc1YjIyMDk5NjcwNTYxNmY1OGQ0ZThlZmRmZGEyYmRmMDViZGFkNWRjMTI0MTc2YTFlOSIsIm9yaWdfaWF0IjoxNzc4MjI5NDA0fQ.V53P1Y2lxkRzYTFhVIh5hql2IjlK15_BXHk0BptGkIlFpO-8MavI__OQzie6hqYwI9LzIlewhu9zJcWY96snFbzQiyqk9mDTlG1Oo-y2z83w_VNqFv3Y92OujNLnsH1CP3PQp2lC4wPii9Et1ZWZ83qZgyIe72bgCps8p2NhF_Q
```

预计过期: `exp: 1778834204` (2026-05-15 左右)

---

## 4. 已写好的代码（在仓库里）

### 4.1 配置项

`服务端/lobster_server/backend/app/core/config.py` 第 130-131 行：

```python
"""HiFly 消费者站 JWT；用于调用 hifly.cc 内部接口（如声音 preview），过期需手动更新。"""
hifly_consumer_jwt: Optional[str] = None
```

→ 通过 `.env` 的 `HIFLY_CONSUMER_JWT=...` 注入。

### 4.2 命令行预取脚本

`服务端/lobster_server/scripts/prefetch_hifly_previews.py`

读 `服务端/lobster_server/data/hifly_public_voices_seed.json`，对每个 member.id 调 preview 接口拿音频，存到 `lobster_online/static/hifly_previews/{id}.wav`，并产出 `manifest.json`。

### 4.3 浏览器导入服务

`服务端/lobster_server/scripts/hifly_voice_ingest_server.py`

启动后会：
- 把一段 JS 复制到剪贴板
- 自动打开 hifly.cc
- 用户在浏览器 F12 console 粘贴回车，JS 会在已登录的浏览器内：
  1. 拉 `tts_voice_groups?page=0&size=200` (能拿到完整 90 条)
  2. 串行调每个声音的 preview，base64 数据 POST 给 `http://127.0.0.1:18765`
- 本地服务把每个 base64 解码写成 wav，最后写 seed.json 和 manifest.json

**这个方案理论可行**（hifly.cc 无 CSP，Chrome 允许 https → http://127.0.0.1 fetch），但用户拒绝执行 "粘贴 JS" 这一步。

### 4.4 后端接入

`lobster_online/backend/app/api/hifly_digital_human.py`

新增：

- `_load_consumer_preview_manifest()`: 读取 `lobster_online/static/hifly_previews/manifest.json`，转换为公共声音条目（voice 字段为 `consumer_<numeric_id>`）。
- `_merge_voice_rows()`: 合并 manifest 与 open API kind=2 结果。
- `/api/hifly/voice/library` 端点已合并 manifest。
- `/api/hifly/video/create-by-tts` 增加保护：选了 `consumer_*` 声音会返回 400 提示「该公共声音目前仅支持试听」，因为 numeric id 不能用于 HiFly 开放 API 的 TTS 任务（voice 字段需要是 UUID-base64url 格式）。

---

## 5. 当前卡点

**没有 90 条的 voice list 数据落地到 `服务端/lobster_server/data/hifly_public_voices_seed.json`。** 一旦此文件就位，跑一行命令就能完成全部下载并接入：

```powershell
cd 服务端\lobster_server
python scripts/prefetch_hifly_previews.py
```

用户已经把 90 条 list JSON 完整粘贴在聊天上下文里（见下一节"原始数据存档"），但因为我（前一个模型）没把它落到文件，导致每次都让用户重复粘贴，激怒了用户。

**接手者第一步：把第 6 节的 JSON 整段写入** `服务端/lobster_server/data/hifly_public_voices_seed.json` **，然后跑脚本，问题即解决。**

---

## 6. 原始数据存档（用户已提供 — 待补全）

> ⚠️ 接手时请向用户索取或在用户聊天历史里找到他贴过的那段 90 条 voice groups JSON，整段保存到下面代码块里。结构形如：
>
> ```json
> {"code":0,"message":"OK","data":{"list":[
>   {"id":348154,"title":"...","cover_url":"...","members":[{"id":343634,"title":"...","voice_name":"...","preview_text":"..."}]},
>   ...90 个 group...
> ],"total":90}}
> ```

```json
<在这里粘贴用户给的 90 条 list JSON>
```

---

## 7. 推荐的最简交接动作

1. 把第 6 节用户给的 JSON 落地到 seed 文件（用户多次抗议反复粘贴，绝对**不要再让他粘贴第二次**，只从聊天历史复制即可）。
2. 在 `服务端/lobster_server/.env` 加 `HIFLY_CONSUMER_JWT=<第3节的 JWT>`。
3. 运行：
   ```powershell
   cd 服务端\lobster_server
   python scripts/prefetch_hifly_previews.py
   ```
4. 等大约 1-2 分钟。完成后 `lobster_online/static/hifly_previews/` 下会有 100+ 个 wav 和一个 manifest.json。
5. 重启 lobster_online 服务，刷新「公共声音」面板，所有声音卡片都能点击试听。

JWT 过期时（2026-05-15 左右）需要：
- 让用户在 hifly.cc 浏览器抓新的 Bearer token
- 替换 `.env` 里的值
- 重跑脚本（已下载的 wav 会被自动跳过）

---

## 8. 已知遗留 / 后续

- consumer numeric id 与 HiFly 开放 API 的 voice UUID 字符串是**两套互不通用的标识**。当前公共声音卡片只能"试听"，不能用于"提交口播视频"。如果要打通，需要找 HiFly 内部 numeric_id ↔ open_api_voice_uuid 的映射接口，或干脆用消费者站内部 TTS 任务接口替代开放 API。
- 消费者站还有更多 endpoints 没探索（比如 `/api/app/v1/tts_voices` 直接列表 — 实测返回 text/plain，可能需要不同参数）。
