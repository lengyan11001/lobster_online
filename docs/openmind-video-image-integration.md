# OpenMind 图片与视频接入记录

记录时间：2026-06-06  
测试服务器：`42.194.209.150`，服务目录 `/opt/lobster-server`

## 配置

服务器 `.env` 已有：

```env
OPENMIND_API_BASE=https://www.openmindapi.com
OPENMIND_API_KEY=***
OPENMIND_IMAGE_MODEL=gpt-image-2
OPENMIND_IMAGE_FALLBACK_ENABLED=1
```

注意：直接用 Python 默认 `User-Agent` 请求 `/v1/models` 会被 Cloudflare 1010 拦截。请求头建议固定加：

```http
User-Agent: Mozilla/5.0 Chrome/126 Safari/537.36
Authorization: Bearer ${OPENMIND_API_KEY}
Content-Type: application/json
Accept: application/json
```

## 图片生成

Endpoint：

```http
POST ${OPENMIND_API_BASE}/v1/images/generations
```

示例请求：

```json
{
  "model": "gpt-image-2",
  "prompt": "A premium product photography image for ecommerce: a sleek white ceramic smart thermos bottle with subtle gold trim, standing on a clean light-gray studio surface, soft daylight, modern minimal composition, high-end commercial photography, realistic shadows, no text, no logo, no watermark.",
  "size": "1024x1024",
  "n": 1,
  "response_format": "url"
}
```

返回取图逻辑：

- 优先取 `data[0].url`
- 如返回 base64，再兼容 `data[0].b64_json`

实测：

- 模型请求：`gpt-image-2`
- 实际返回模型：`gpt-image-2-codex`
- 耗时：约 `25s`
- 图片 URL 示例：
  `https://sub.g-aisc.com/media/siphonlab-media/images/outputs/2026/06/05/839dbd79d5af4c98bb3e9de92acd8435e3eb2635b89a46b7fb5e56554f9e261f-7c4e3c37-045e-4f2e-834b-7fde95de6184.png`

## Veo3.1 视频

模型列表中可用：

- `veo31`
- `veo31-fast`
- `veo31-ref`

Endpoint：

```http
POST ${OPENMIND_API_BASE}/v1/videos
GET  ${OPENMIND_API_BASE}/v1/videos/{task_id}
```

### 文生视频示例

```json
{
  "model": "veo31",
  "prompt": "A simple cinematic shot of a red apple on a white table, soft daylight, slow camera push-in.",
  "seconds": "4",
  "size": "1280x720",
  "aspect_ratio": "16:9",
  "resolution": "720p"
}
```

实测成功：

- 模型：`veo31`
- 提交耗时：约 `57.8s`
- 任务 ID：`task_rPSgerih7ERMvzJL2Bkhxjzj2mbSEmuh`
- 状态：`completed`
- 视频 URL 示例：
  `http://107.148.176.80/generated/8af5e541f31346519d0ede0448905fd8.mp4`

### 图生视频示例

```json
{
  "model": "veo31-fast",
  "prompt": "Create a vertical 9:16 ecommerce short video based on the reference product image. Show the white ceramic smart thermos bottle as the hero product, slow elegant camera movement, clean bright modern studio, soft reflections, premium lifestyle advertising, no text, no captions, no watermark.",
  "image": "https://example.com/product.png",
  "image_url": "https://example.com/product.png",
  "images": ["https://example.com/product.png"],
  "seconds": "4",
  "size": "720x1280",
  "aspect_ratio": "9:16",
  "resolution": "720p"
}
```

实测成功：

- 图片生成耗时：约 `25.0s`
- 视频模型：`veo31-fast`
- 视频提交到完成：约 `66.3s`
- 任务 ID：`task_HJUz0cVnudX0CRnWV2tDtLihJibX6iCz`
- 视频 URL 示例：
  `http://107.148.176.80/generated/8ca57584ee7e4a2e9e9d863af21391ad.mp4`

返回格式示例：

```json
{
  "id": "task_xxx",
  "task_id": "task_xxx",
  "object": "video.task",
  "model": "veo31-fast",
  "status": "completed",
  "progress": 100
}
```

轮询完成后常见 URL 字段：

```json
{
  "video": { "url": "http://..." },
  "url": "http://...",
  "output": { "url": "http://..." },
  "result": { "url": "http://..." },
  "video_url": "http://..."
}
```

## Seedance 2.0 视频

模型列表中可用：

- `doubao-seedance-2-0-260128`
- `doubao-seedance-2-0-fast-260128`

Endpoint 同样是：

```http
POST ${OPENMIND_API_BASE}/v1/videos
GET  ${OPENMIND_API_BASE}/v1/videos/{task_id}
```

重要：`duration` / `seconds` 必须传数字，不要传字符串。  
错误示例：`"duration": "4"` 会触发 `model_price_error`。  
正确示例：`"duration": 4`。

### 4 秒竖屏图生视频

```json
{
  "model": "doubao-seedance-2-0-260128",
  "prompt": "Create a vertical 9:16 ecommerce short video based on the reference product image. Hero product is a white ceramic smart thermos bottle with gold trim. Bright clean studio, premium product advertising, gentle camera push-in, soft highlights, no text, no watermark.",
  "image": "https://example.com/product.png",
  "image_url": "https://example.com/product.png",
  "images": ["https://example.com/product.png"],
  "seconds": 4,
  "duration": 4,
  "size": "720x1280",
  "aspect_ratio": "9:16",
  "resolution": "720p"
}
```

实测成功：

- 模型：`doubao-seedance-2-0-260128`
- 任务 ID：`task_ao3ftbMdL6ToSOAAhvjEirERBVYdoffw`
- 提交耗时：约 `5.8s`
- 生成完成耗时：约 `162s`
- 视频 URL 示例：
  `https://ark-acg-cn-beijing.tos-cn-beijing.volces.com/doubao-seedance-2-0/...mp4?...`

### 8 秒女主播带货视频

```json
{
  "model": "doubao-seedance-2-0-260128",
  "prompt": "Vertical 9:16 ecommerce livestream style product video, 8 seconds. A natural friendly Chinese woman presenter appears in a bright clean modern home studio, holding and showing the white ceramic smart thermos bottle with subtle gold trim from the reference image. She looks at the camera and speaks naturally in Mandarin, with realistic lip movement and expressive gestures, as if saying: '这款陶瓷保温杯颜值很高级，保温效果好，日常通勤和办公都很适合。' Premium but natural, soft daylight, clean background, product always clearly visible, no subtitles, no on-screen text, no watermark.",
  "image": "https://example.com/product.png",
  "image_url": "https://example.com/product.png",
  "images": ["https://example.com/product.png"],
  "seconds": 8,
  "duration": 8,
  "size": "720x1280",
  "aspect_ratio": "9:16",
  "resolution": "720p"
}
```

实测成功：

- 模型：`doubao-seedance-2-0-260128`
- 任务 ID：`task_GpQNmu6isWQ8R8o6JEvCVU8Wxp8uzXfk`
- 提交耗时：约 `4.7s`
- 总耗时：约 `236s`
- 最终状态：`completed`
- 视频 URL 示例：
  `https://ark-acg-cn-beijing.tos-cn-beijing.volces.com/doubao-seedance-2-0/...mp4?...`

说明：Seedance 返回的视频 URL 是火山 TOS 签名链接，有过期时间，拿到后应尽快下载或入库保存。

### fast 版测试结果

`doubao-seedance-2-0-fast-260128` 实测：

- 提交可成功
- 任务 ID：`task_HESzn7Grf8Np2fC6wWgztQPxvvMwO7cZ`
- 最终失败
- 错误：`upstream returned unrecognized message`

当前建议：生产先使用 `doubao-seedance-2-0-260128`，不要默认用 fast 版。

## 统一轮询逻辑

提交后取任务 ID：

- `id`
- `task_id`
- `video_id`
- `data.id`
- `data.task_id`
- `data.video_id`

轮询状态：

```http
GET ${OPENMIND_API_BASE}/v1/videos/{task_id}
```

终态建议：

```text
completed
succeeded
success
failed
error
cancelled
```

视频 URL 兼容读取：

- `url`
- `video_url`
- `video.url`
- `output.url`
- `result.url`
- `metadata.url`

## 接入建议

1. 图片生成可作为 `gpt-image-2` 备用或独立渠道。
2. Veo3.1 可接 `veo31` / `veo31-fast`，竖屏传 `size=720x1280`、`aspect_ratio=9:16`。
3. Seedance 2.0 建议接普通版 `doubao-seedance-2-0-260128`。
4. Seedance 的 `duration` / `seconds` 必须是数字。
5. 所有 OpenMind 请求都加浏览器 `User-Agent`，避免 Cloudflare 1010。
6. 视频结果 URL 如果是 TOS 签名链接，应在后端拿到后立即下载到自己的素材库/CDN。
7. 用户侧不要展示原始上游错误；失败时建议提示“生成失败，可稍后重试或切换模型”。

