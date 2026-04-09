# 合并到其它在线版分支

1. **复制 Python**：将 `../backend/app/services/` 与 `../backend/app/api/media_edit.py` 拷到目标工程对应路径。
2. **`mcp/capability_catalog.json`**：把 `capability_catalog_media_edit.json` 里的 **`media.edit` 键**合并进根对象（与 `image.generate` 等并列）；注意逗号。
3. **`skill_registry.json`**：把 `skill_registry_media_edit_skill.json` 里的 **`media_edit_skill`** 合并进 `packages`。
4. **`create_app.py`**：增加 `media_edit` 路由与 `_ensure_media_edit_capability()`（见主仓库 `lobster_online/backend/app/create_app.py`）。
5. **`mcp/http_server.py`**：在 `invoke_capability` 里、`pre-deduct` 之后增加 **`upstream_name == "local"`** 分支，请求 `POST {BASE_URL}/api/media-edit/run`（见主仓库同文件）。

单机免解锁时：可把 `skill_registry` 中该包改为 `default_installed: true` 并删除 `unlock_price_credits`。
