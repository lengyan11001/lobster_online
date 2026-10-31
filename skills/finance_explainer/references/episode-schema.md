# 拍表与配置文件字段

## episode.json
```json
{
  "meta": {"title": "…", "channel": "Berry说", "theme": "v2-glass", "fps": 30, "width": 1920, "height": 1080, "facts": "facts.md"},
  "voice": {"engine": "mimo", "model": "mimo-v2.5-tts", "voice": "茉莉", "style": "（占位）语气说明…"},
  "lexicon": {"GPU": "G P U", "H20": "H 二十"},
  "beats": [
    {"id": "c01", "narration": "二〇二五年一月二十七号，[[n1]]英伟达一天之内…", "host": "off", "bg": "gen:trading_floor",
     "overlays": [{"type": "counter", "at": "n1", "from": 0, "to": 600000000000, "prefix": "−$", "money": true, "pos": "center"}]},
    {"id": "c05", "kind": "chapter", "duration": 2.6, "bg": "gen:chip_macro", "title": "第一章 · 卡了什么", "subtitle": "EXPORT CONTROLS"},
    {"id": "c10", "narration": "…", "host": "pip",
     "stage": {"type": "steps", "title": "2024.12 管制升级", "source": "美国商务部 BIS 公告", "items": [{"at": "f1", "text": "24 类芯片制造设备", "sub": "造不出来"}]}}
  ]
}
```

### 拍（beat）
| 字段 | 说明 |
|---|---|
| id | 唯一，录音稿和音频文件都用它 |
| narration | 口播原文；`[[cue]]` 标卡点（小写字母开头） |
| kind | `chapter` 章节卡（无口播，用 duration、title、subtitle、bg） |
| host | `full` / `shrink` / `side` / `pip` / `off`（切换时主播弹簧缩放） |
| punch | 仅 full：推近倍数（1.08–1.15），绕脸部放大，盖跳切 |
| bg | host=off 时的全屏空镜，`gen:<id>` / `stock:<id>` 在 assets.json 查 |
| stage | 舞台卡片（host=pip 时用），见下 |
| overlays | 叠加卡片数组，见下 |

### 叠加卡片（overlays）通用字段
`type`、`at`（cue 名或 "start"）、`until`（可选，到某 cue 消失；默认拍结束）、`pos`（left / right / lower / center / topleft）。

| type | 专有字段 |
|---|---|
| comment | user, text, side(left/right), time |
| datestack | items: [{at, date, label, strike}] |
| counter | from, to, prefix, suffix, money, dur |
| kinetic | text, sub, style(big/stamp/stamp-red/brush), size |
| term | en, zh |
| questions | items: [问题…] |
| meme | src(gen:id), caption |
| sticker | src(gen:id), bubble: [{at, text}] |
| list | items: [{at, text, tone(good/bad)}] |

### 舞台（stage）
| type | 字段 |
|---|---|
| bars | title, source, bars: [{label(可含\n), value, unit, at, hot}], callout {at, text} |
| line | title, source, points [{x, y}], reveal [cue…], callout |
| steps | title, source, items [{at, text, sub}] |
| doc | source（「据 XX 公告整理」）, title, lines [{at, text, hl}] |
| quiz | question, ask, reveal(cue), answer, note |

## host.json
```json
{"src": "host/anchor.jpg", "kind": "image", "face": [0.5, 0.33], "offset": 0.23, "loop_len": 10,
 "clips": {"c02": "host/c02.mp4", "c03": {"src": "host/grok_15s.mp4", "offset": 9.08}},
 "loops": ["host/loopA.mp4", "host/loopB.mp4", "host/loopC.mp4"]}
```
- clips 值为字符串时用全局 offset（Wan = 0.23）；为对象时用自己的 offset（Grok = 片段内画面起点）。
- 没有 clip 的可见拍用 loops 轮换（长拍自动拆成 ≤ loop_len−0.5 秒的段）。

## assets.json
`{"trading_floor": {"src": "assets/trading_floor.mp4", "kind": "video"}, "sticker_gpu": {"src": "assets/sticker_gpu.png", "kind": "image"}}`
缺失的素材在画面上画「素材待生成」占位，样片阶段也能先渲染看节奏。

## timings.json（三个来源同格式）
narrate_mimo.py（占位）/ align_recording.py（自录）/ grok_beats.py（Grok 原生说话）：
`beats[id] = {file, text, start, end, duration, words[{text,start,end,i0,i1}], chars[每个字的开始秒], cues{name: 秒}, align}`
