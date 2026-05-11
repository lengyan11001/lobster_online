# 目标成片技能

当用户只给一个目标，要求结合记忆自动生成宣传视频、短视频或图生视频时，调用 `goal.video.pipeline`。

## 使用方式

优先一次调用：

```json
{
  "capability_id": "goal.video.pipeline",
  "payload": {
    "action": "start_pipeline",
    "goal": "用户的目标原文",
    "platform": "douyin",
    "duration": 8,
    "aspect_ratio": "9:16"
  }
}
```

该能力会在后端固定完成：

1. 检索并使用用户记忆/代理商记忆。
2. 生成文案、图片提示词和视频提示词。
3. 调用 `image.generate` 生成图片并轮询真实结果。
4. 调用 `video.generate` 使用图片生成视频并轮询真实结果。
5. 返回真实 `task_id`、`saved_assets`、`image_asset_id`、`video_asset_id` 和 `final_asset_id`。

## 规则

- 不要自己串联 `image.generate`、`video.generate`、`task.get_result`，除非本能力不可用。
- 不要编造任务 ID、素材 ID、费用或“已完成”状态。
- 只有返回里存在 `final_asset_id` 或 `video_asset_id`，才能说视频已入库。
- 用户后续要发布时，使用返回的 `final_asset_id` 调用现有发布工具。
- 用户提供参考素材时，把素材 ID 放进 `reference_asset_ids`，把公网图片 URL 放进 `reference_image_urls`。
