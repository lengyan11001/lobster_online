# 素材剪辑技能（`media.edit`）移植说明

## 单独打本技能包

在 `lobster_online` 根目录执行：

```bash
bash scripts/pack_media_edit_skill.sh
```

会在当前目录生成 **`media_edit_skill_bundle_日期时间.zip`**（约十几 KB），仅含本技能 Python、`PORTING`、`merge_snippets`，**不含**整站 `python/`、`nodejs/` 等。

## 依赖

- 系统 **PATH** 中可执行 **`ffmpeg`**（未安装则接口报错，无静默降级）。
- 与素材库一致：使用现有 `Asset`、`_save_bytes_or_tos`（见 `backend/app/api/assets.py`）。

## 在线版（默认）

- **安装 = 服务端 + 本机各一次**：点「安装」时，前端会先调 **`API_BASE`（认证服）** 的 `/skills/install`，再调 **`LOCAL_API_BASE`（本机 backend）** 的 `/skills/install`。后者把 `media.edit` 写入本机 `CapabilityConfig` 与 `mcp/capability_catalog.local.json`，**MCP 才能识别能力**。仅服务端安装成功而本机未同步时，对话里仍会表现为「技能未就绪」。
- **技能商店入口**：在线版前端调的是 **认证服务器（`lobster-server`）** 的 `/skills/store`，条目以服务端仓库根目录 **`skill_registry.json`** 为准。若拷贝了本机 `lobster_online` 但**未部署/未更新**服务端 `skill_registry.json`（含 `media_edit_skill`），商店里**不会出现**「素材剪辑」——这不是客户端缺文件，而是**服务端未登记**。
- **权限**：技能包 `media_edit_skill`，**1000 积分解锁**（服务端 `skill_registry.json` → `unlock_price_credits`）。
- **无云端、纯单机拷贝本仓库时**：可将该包改为 **`default_installed: true`** 并**去掉** `unlock_price_credits`，使能力默认可用（产品自行选择，勿两套行为混用而不文档化）。

## 文件清单（复制到其他分支时按表勾选）

| 路径 | 说明 |
|------|------|
| `backend/app/services/media_edit_exec.py` | ffmpeg 执行与参数校验 |
| `backend/app/services/__init__.py` | 包标记（可为空） |
| `backend/app/api/media_edit.py` | `POST /api/media-edit/run` |
| `backend/app/create_app.py` | `include_router(media_edit_router)` + `_ensure_media_edit_capability()` |
| `mcp/capability_catalog.json` | 增加 `media.edit`（`upstream: local`） |
| `mcp/http_server.py` | `invoke_capability` 内 `upstream_name == "local"` → 调本机 `/api/media-edit/run` |
| `skill_registry.json`（**仅单机/自建商店**若需要） | 增加包 `media_edit_skill`；**在线商店以 `lobster-server/skill_registry.json` 为准** |

## 接入点（合并时注意）

1. `create_app.py`：注册路由 + 启动时 `_ensure_media_edit_capability()`（已有库补写 `CapabilityConfig`）。
2. `mcp/http_server.py`：**仅一处** `local` 分支，勿分散。
3. `mcp/capability_catalog.local.json`（若使用）：可覆盖同名能力，合并时勿覆盖掉 `media.edit`。

## 调用约定

- MCP / 对话：`invoke_capability`，`capability_id` = `media.edit`，`payload` 含 `operation`（见 catalog `arg_schema`）与 `asset_id`。
