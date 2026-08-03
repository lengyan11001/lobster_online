# 创意成片技能

当用户只给一个目标，要求结合记忆自动生成宣传视频、短视频或图生视频时，调用 `goal.video.pipeline`。

## 使用方式

优先一次调用：

```json
{
  "capability_id": "goal.video.pipeline",
  "payload": {
    "action": "start_pipeline",
    "goal": "给产品生成30秒、9:16、720p的宣传视频",
    "platform": "douyin",
    "duration": 30,
    "resolution": "720p",
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

- 用户原话中明确写了时长、比例、分辨率、是否带声音、随机种子、负面提示词、首尾帧或参考素材时，必须传入对应字段，禁止用默认值覆盖。
- `30秒`、`30s` 必须传 `duration: 30`，不要改成 6 秒或拆成一个未合并的短片。
- 不要因为时长自动切换到必火 2.5 或其他价格更高的模型；只有用户明确选择该模型时才能使用。当前模型不兼容时应明确提示。
- 用户未指定时长时可以省略 `duration`；当前单次创意成片最长支持 30 秒。
- 9-30 秒当前支持 `480p`、`720p`；不支持的参数要明确报错，禁止静默降级。
- 参考图片、视频、音频分别放入 `reference_image_urls`、`reference_video_urls`、`reference_audio_urls`；首尾帧使用 `first_image_url`、`end_image_url`。
- 不要自己串联 `image.generate`、`video.generate` 来替代本能力。
- `start_pipeline` 返回 `job_id` 后，如果本轮等待超时或返回 `status=running`，说明任务仍在运行；继续用 `poll_pipeline` + `job_id` 查询，不要说能力不可用。
- 不要编造任务 ID、素材 ID、费用或“已完成”状态。
- 只有返回里存在 `final_asset_id` 或 `video_asset_id`，才能说视频已入库。
- 用户后续要发布时，使用返回的 `final_asset_id` 调用现有发布工具。
- 用户提供参考素材时，把素材 ID 放进 `reference_asset_ids`，把公网图片 URL 放进 `reference_image_urls`。
