# 素材库 ID → 速推 `image_url` 的传递逻辑

## 调用链（在线版 `lobster_online`）

1. 前端在发对话请求时带上 **`attachment_asset_ids`**（素材库里的 `asset_id` 列表）。
2. 后端 `chat.py`：
   - **`_get_attachment_public_urls`** → 对每个 id 调 **`get_asset_public_url(asset_id, …)`**（`backend/app/api/assets.py`）。
   - 得到的 URL 列表记为 **`attachment_urls`**。
3. 当工具调用为 **`video.generate`（图生视频）** 时，**`_inject_video_media_urls`** 会用 **`attachment_urls`** **覆盖** `payload` 中的：
   - `image_url`（首张图）
   - `filePaths`、`media_files`
   - 并设置 `functionMode=first_last_frames` 等  
   即：**速推最终拿到的 `image_url` = 本条消息解析出的「附图公网 URL」之一**，而不是模型在 JSON 里随便写的字符串。

## `get_asset_public_url` 读什么

- 只读数据库 **`Asset.source_url`**（上传 / 保存 URL 时写入）。
- **不会**根据 id 去「猜」别的域名；若 `source_url` 为空则返回 `None`，附图流程会 400。

## 为何会出现 `https://cdn.sutui.com.cn/asset/...`

- 历史上 **`/api/assets/save-url`** 在 TOS 失败且原链为内网时，会走 **`sutui.transfer_url`**，把返回的 CDN 链写入 **`source_url`**。
- 该域名虽带 `cdn.`，但**不一定**对速推生成链路稳定可拉取；此前逻辑会把它当作「公网 CDN」**原样**传给 `image_url`，导致上游拉取失败。

## 当前行为（修复后）

- 若 **`source_url` 的主机名为 `*.sutui.com.cn`**：
  - **优先**：本地 `assets/` 仍有对应文件且已配置 **`TOS_CONFIG`** 时，**自动重传 TOS** 并更新 `source_url`，再返回新链。
  - **否则**：**不返回**该速推 CDN 链（返回 `None`），附图校验失败并提示配置 TOS 或重新上传，**避免**把不可拉取的 URL 传给速推。

## 建议

- 素材长期用于图生视频：优先 **`TOS_CONFIG`** 上传，使 `source_url` 为自有桶域名。
- 无 TOS 时依赖认证中心 **`/api/assets/upload-temp`** 返回的公网链，勿依赖速推临时 CDN 入库。
