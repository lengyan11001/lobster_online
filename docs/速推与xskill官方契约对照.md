# 速推 / xskill 官方契约与 Lobster 实现对照

本文对照你提供的 **`xskill-ai` skill 包**（`SKILL.md` + `scripts/xskill_api.py`）与当前 **`lobster_online` / `lobster-server`** 行为，说明「模型与参数」应如何核对、为何仍会踩坑。

## 1. 官方契约（zip 里写死的）

### REST 提交任务（与 Lobster 一致）

`xskill_api.py` 中：

```python
def submit_task(model_id: str, params: dict, token: str) -> dict:
    body = {"model": model_id, "params": params, "channel": None}
    resp = _request("POST", "/api/v3/tasks/create", body=body, token=token)
```

本仓库 **`lobster_online/mcp/http_server.py`** 与 **`lobster-server/mcp/http_server.py`** 中的 **`_call_upstream_sutui_tasks_rest`** 对 `generate` 的拼包方式相同：

- `params` = 除 `model` 外的所有字段（`prompt`、`image_url`、`aspect_ratio`、`duration`、`options` 等）。

即：**官方 CLI 与 Lobster 走的是同一条 `/api/v3/tasks/create` 契约**，不是另一套私有协议。

### SKILL.md 里对参数的抽象

- 文生图/文生视频多数场景：**`model` + `prompt`** 即可。
- 视频：**`aspect_ratio`** 示例为 `16:9`、`9:16` 这类**枚举**，**没有**写 `auto`。
- **`duration`** 写的是「视频时长秒数（如 5, 10）」——与 UI 里常见的 **`"6s"` 字符串**不是同一种形态；若原样塞进 `params` 而上游 Schema 只接受整数或特定格式，就会 **422**。

**官方也写明**：参数不对时可从返回里拿 **正确 Schema** 再重试（`SKILL.md` 第 143、256 行附近）。zip **并不**内置「全模型全字段」的完整清单，完整 Schema 应以 **`GET /api/v3/models/{model_id}/docs`**（对应 CLI `info`）为准。

## 2. 「直连 MCP」到底改了没有？

**速推的 `generate` / `get_result` 在 Lobster 里已经统一改为走 REST**（避免 MCP HTTP 返回体 `Decimal` 等导致的 `-32603`），与 zip 里的 `submit_task` 一致：

| 场景 | 实际调用 |
|------|----------|
| 本机 `upstream_urls.json` 里 sutui 指向 `…/mcp-http`（看起来像 MCP） | 仍会在代码里 **拦截** `generate`/`get_result`，改为 **`https://api.xskill.ai/api/v3/tasks/create` / `tasks/query`**（可用 `SUTUI_API_BASE` 覆盖域名） |
| 在线版走 **`/mcp-gateway` → 服务器 lobster MCP** | 服务器侧同样对上述工具走 **REST**；规范化在 **服务器** `mcp/http_server.py` 里执行 |

因此：**并不是「只修了直连 MCP、没修网关」**——两条链路最终都是 **REST tasks**，差异只在 **Token/JWT** 与 **哪一端执行 `_normalize_*_payload`**。

## 3. 为何还会出现 422 / 参数问题？

1. **规范化层**（`_normalize_video_generate_payload` / `_normalize_image_generate_payload`）负责把「统一 payload」切成各模型能接受的字段；若 **UI/模型返回** 传入 **`aspect_ratio: "auto"`**、**`duration: "6s"`** 等，而此前未映射，就会原样进入 `params` → 上游 **422**。这类问题需在 **MCP 规范化**里修（且 **`lobster_online` 与 `lobster-server` 两处都要一致**——见下条）。
2. **双份实现**：本机直连速推时跑 **`lobster_online`** 的 MCP；走网关时跑 **`lobster-server`** 的 MCP。只改一侧会导致「同一种 UI 参数在一端好、另一端坏」。
3. **zip 无法替代逐模型核对**：全量「所有模型、所有参数」应以 **`xskill_api.py info <model_id>`** 或线上文档接口为准，而不是只对照 SKILL 里的通用示例。

## 4. 建议的核对方式（与官方 zip 对齐）

在任意装了依赖的环境执行（需有效 `XSKILL_API_KEY` 或按脚本提示输入）：

```bash
# 与 zip 内脚本一致：查看某模型完整参数 Schema
python3 /path/to/xskill-ai/scripts/xskill_api.py info fal-ai/bytedance/seedance/v1.5/pro/image-to-video
python3 /path/to/xskill_api.py info wan/v2.6/image-to-video
```

把输出中的 **required / enum / 类型** 与 **`mcp/http_server.py`** 里对应 `if "seedance/v1.5" in model` 等分支对照即可。

## 5. 与本仓库文档的关系

- **`docs/视频生成-按模型参数转换.md`**：说明「谁在 `_normalize_video_generate_payload` 里做映射」。
- **`docs/素材库-附图URL与速推.md`**：说明素材 `source_url` 如何进 `image_url`。

若速推前端或 OpenClaw 以后新增字段（如更多 `options`），应 **先查 `info` 再改规范化**，避免只凭 SKILL 的通用 JSON 猜字段。

---

**全量拉清单 + 自动审计脚本**：见 **[全量模型审计.md](./全量模型审计.md)**（`scripts/xskill_fetch_models.py` 与 `lobster-server/scripts/audit_model_params.py`）。
