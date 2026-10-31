# 制作手册：数字人、素材、合成

价格是 2026-10-21 实测（Renoise credits）。生成前一律 estimate_cost → 给用户看模型、提示词、数量、总价 → 等明确同意。

## 1. 主播形象（定妆照）

- Seedream 5.0 Pro，16:9，2k，≈6 credits。
- 构图：书房中景、人物居中、头在上三分之一、**两侧留白**（放卡片和图表）；专属道具：暖黄霓虹名字灯、玩偶、一盏标志性台灯。
- 布景要和被参考的频道明显不同（原频道 v1 是客厅霓虹、v2 是白书架；示例集用胡桃木书架 + 绿色台灯）。
- 用户提供原创脸 → 作 reference_image，提示词写「keep her face, hairstyle and outfit exactly the same」。
- **身份核查**：用户给的脸如果被描述为某位真实创作者，或与前一张被描述为真人的照片五官、饰品一致只改了发型眼镜，不要用；纯文字生成或换一张确实不同的原创脸。

## 2. 数字人两方案（实测对比）

| | W：Wan 3.0 音频驱动 | G：Grok Video 1.5 原生说话 |
|---|---|---|
| 输入 | 定妆照 + 该拍音频（reference_audio） | 定妆照（只能 1 张）+ 提示词里写台词 |
| 口型 | 跟着给定音频走；停顿处偶尔不闭嘴 | 与它自己生成的声音天然一致 |
| 表情眼神 | 偏呆，变化少 | 灵动：眨眼、挑眉、歪头、手势丰富 |
| 分辨率 | 480p（720p 翻倍价） | 720p / 480p |
| 价格 | 480p 6/秒 | 720p 17/秒、480p 10/秒 |
| 时长 | 2–30 秒 | ≤15 秒/段 |
| 声音 | 就是你的最终音频 | Grok 的声音，事后「换音色」 |
| 坑 | 输出音频比画面晚 0.23 秒 → host.json offset 0.23 | 每段音色略有差异；型号要写中文读法（A一百、H二十）否则会念英文 |

**推荐组合**：全屏和侧移镜头用 G（720p）；圆形小窗和被空镜盖住的拍用 G 480p（只取声音）或 W；预算紧时小窗用 3 段 10 秒通用说话循环轮换。

### W 提示词（中文效果好）
```
@task:<定妆照> 中的主播坐在沙发上正对镜头讲话，口型与 @material:<该拍音频> 的中文语音逐字同步；说话停顿时嘴巴自然闭合，表情亲切自然、不夸张，偶尔点头、眨眼，双手做解释性手势；固定机位中景，不推拉，人物长相、发型、服装、场景和光线与参考图完全一致。音频：只用参考音频的人声。无字幕、无水印。
```
duration = ceil(该拍音频秒数)；「俏皮、挑眉」之类会让表情盖过口型，少写。

### G 提示词（英文描述 + 中文台词）
```
She looks straight into the camera and talks to the viewer in lively, natural Mandarin Chinese, like a friendly finance YouTuber chatting with a friend: bright attentive eyes that track the lens, natural blinks, an occasional raised eyebrow and quick smile on key words, small head tilts and nods, expressive explaining hand gestures. Fixed medium shot, no camera movement; her face, hair, outfit and the study stay exactly the same.
She says, in Mandarin: "<该拍台词，型号写中文读法>"
Sound: only her clear speaking voice in a quiet room; no music, no other sounds. No subtitles, no watermark.
```
duration = min(15, ceil(字数/5.2)+1)。实测 Grok 语速约 5.2–5.6 字/秒，15 秒能说约 80 字。一段长片含多拍时，用 clips.json 的 from/to 切窗口复用。

### 接入
- W：`build_timeline.py … --host host.json`，host.json 里 `clips: {拍id: "host/xx.mp4"}`、`offset: 0.23`；没有专属片段的拍用 `loops` 自动轮换。
- G：`python3 scripts/grok_beats.py episode.json clips.json voice --public <engine>/public --host-out host.json` → 逐拍对齐、只裁首尾静音、画面同步偏移 → 直接喂给 build_timeline。
- 换音色（G 方案收尾）：把 voice/narration（或逐拍 audio）交给保留节奏的换声（Seed Audio 1.0 re-voicing 等），输出按 timings 逐拍切回 audio/<id>.wav，重跑 build_timeline 即可；口型不受影响。
- 抽检口型：截停顿处与说话处的脸部特写对比（停顿应闭嘴）；脚本里 mouth_seq 的做法：每 0.1 秒一帧 + 当时音量 dB。

## 3. 声音

- **主播自录（首选）**：`align_recording.py 录音 episode.json voice/` — 逐段 whisper（整段长录音 + 长提示会漏句编句，已改为按 ≥0.45 秒停顿切段逐段转写）、重录取最后一遍、拍内停顿 >0.28 秒压到 0.16 秒、接缝 4ms 淡入淡出。
- **占位**：MiMo 预设音色（男：白桦/苏打；女：茉莉/冰糖），`narrate_mimo.py`；英文缩写写进 lexicon 拆字母读（GPU→G P U），型号写中文读法。
- **不做**：从真实创作者的视频里截取声音做克隆参考。

## 4. 空镜、梗、贴纸

- Wan 3.0 480p，audioGeneration=false，5 秒 30 / 10 秒 60。提示词写清「no readable text or logos」「slow dolly / slow orbit」。长拍用 10 秒片 + 引擎里的 Ken Burns 慢推。
- 素材库：`fetch_stock.py <id> "<英文关键词>" <public>/assets --kind photo|video [--source unsplash|pexels|pixabay|wikimedia]`。Unsplash 自动打 download 端点并记署名；Wikimedia 只收 CC0 / CC BY / CC BY-SA / PD；credits.json 用于片尾致谢。
- 梗：原创 3D 卡通短片（Wan 5 秒），不用影视、综艺片段。
- 卡通贴纸：grok-image（3 credits），提示词末尾写「plain solid pure green background」，然后抠绿：
  ```python
  green=(g>120)&(g-np.maximum(r,b)>40); alpha=MinFilter(3)+GaussianBlur(1); 绿溢色：g=min(g,max(r,b)); 裁 bbox
  ```

## 5. 合成与出片

1. `build_timeline.py episode.json voice/timings.json <public> --assets assets.json --host host.json --bgm music/bgm.mp3 --badge "AI 生成数字人演示 · 非商用"`
   - 片段演示用 `--from c01 --to c11`，或另存一个只含部分拍的 episode。
   - 卡片 `until: "<cue>"` 让它在某个卡点消失。
2. 抽帧：`npx remotion still Episode out/x.jpg --frame=N`（并行 6–8 张拼一张图看）；一定要看主播缩放的过渡中间帧。
3. 渲染：`npx remotion render Episode out/raw.mp4 --concurrency=6`（1080p 约 0.25× 实时）。
4. 出片：`ffmpeg -i raw.mp4 -c:v libx264 -crf 21 -pix_fmt yuv420p -af loudnorm=I=-16:TP=-1.5:LRA=11 -c:a aac -b:a 192k -movflags +faststart 成片.mp4`（引擎直出约 -22 LUFS，偏小）。

## 6. 踩过的坑
- 字幕断句要按**原文标点**（对齐结果里没有标点）：build_timeline 用 timings 的 text + chars。
- Renoise 下载 CDN 偶发中断（curl 18）：逐个 `curl -C - --retry 3` 循环，下完 ffprobe 校验。
- Wan 片段 480p 拉到 1080p 会软；全屏镜头优先 Grok 720p。
- PiP 圆形头像：face 坐标填人脸在定妆照里的相对位置（示例 [0.5, 0.33]）。
- PIL / numpy 只在 python3.14 里有；ffmpeg 没有 drawtext，标注一律用 PIL。
- 不要扫描系统找 API key；只读 ~/.config/xiaolin-explainer/keys.env。
