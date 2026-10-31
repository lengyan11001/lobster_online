# 画面语法图鉴 ↔ 引擎组件 ↔ 拍表字段

原频道两代形式：
- **v1（2022–23）**：客厅布景 + 名字霓虹灯；图表上「紫色几何舞台」，主播整帧缩成左下角矩形小窗。
- **v2（2025–）**：白书架；深色玻璃风气泡/卡片舞台，主播缩成圆形头像；新增 AI 卡通人物贴纸演戏、主播抠像叠素材墙。

引擎主题：`meta.theme = "v1-purple" | "v2-glass"`（templates/engine/src/lib/theme.ts）。复刻用自己的布景（示例：胡桃木书架 + 绿台灯），只沿用语法。

| # | 画面语法 | 什么时候用 | 拍表写法 | 组件 |
|---|---|---|---|---|
| 1 | 主播中景 | 观点、情绪、过渡句 | `"host": "full"` | Host |
| 2 | 推近盖跳切 | 全屏拍之间、关键句 | `"punch": 1.08–1.15` | Host（绕脸硬切放大） |
| 3 | 缩小 + 卡片（私信卡） | 开场观众点题 | `"host": "shrink"` + `comment` | Host + Comment |
| 4 | 侧移 + 右侧卡 | 术语、清单、贴纸独白 | `"host": "side"` + `term`/`list`/`sticker` | Host |
| 5 | 小窗 + 图表舞台（放大缩小） | 数据、文件、步骤、竞猜 | `"host": "pip"` + `stage` | Host + StageLayer |
| 6 | 全屏空镜 | 提到具体事物、主播无需出镜 | `"host": "off", "bg": "gen:xxx"` | Asset（Ken Burns 慢推） |
| 7 | 章节标题卡 | 每章开头、片尾 | `{"kind":"chapter","title","subtitle","bg","duration":2.6}` | Chapter + impact 音效 |
| 8 | 日期贴纸叠加 | 时间线，倒下的划红线 | overlay `datestack`（items 带 at、strike） | DateStack |
| 9 | 大数字滚动 | 金额、数量冲击 | overlay `counter`（money:true 写满零） | Counter + ding |
| 10 | 盖章 / 大字 / 毛笔字 | 吐槽、结论、一词收尾 | overlay `kinetic` style big / stamp / stamp-red / brush | Kinetic + slam |
| 11 | 术语卡 | 英文术语 + 中文解释 | overlay `term`（en, zh） | Term |
| 12 | 问题云 | 本期议程 | overlay `questions`（items） | Questions |
| 13 | 原文标黄 | 公告、财报、新闻一手证据 | `stage.type = "doc"`（lines: text + hl） | Doc（逐行荧光笔） |
| 14 | 柱状 / 折线 / 步骤 | 数据对比、时间轴拉锯、原因清单 | `stage.type = bars / line / steps`（每项 at） | Bars / Line / Steps + callout |
| 15 | 竞猜 | 「你猜多少钱？」 | `stage.type = "quiz"`（ask, reveal, answer） | Quiz |
| 16 | 卡通贴纸独白 | 拟人内心戏 | overlay `sticker`（src, bubble 逐句） | Sticker（浮动 + 气泡） |
| 17 | 反应梗 | 情绪出口 | overlay `meme`（src, caption） | Meme（全屏） |
| 18 | 要点清单 | 好/坏两面、两件事 | overlay `list`（tone good/bad） | List |
| — | 字幕 | 全程 | 自动（按原文标点断句 ≤13 字，英文不拆） | Subtitles |
| — | 角标 / 声明 | 演示片 | `--badge "AI 生成数字人演示 · 非商用"` | Badge |

尚未实现（需要时再加）：主播抠像叠素材墙（v2，需 Vision 人像分割）、人名条 + 黄色手写外号、气泡关系图（v2 玻璃气泡）、自拍插入。

## 节奏参数（来自 6 支样片逐帧统计）
- 每分钟约 9–12 次画面类型切换；硬切间隔中位数约 3 秒。
- 2023 年版：真人全景约 50%，最长一口气 68 秒；2026 年版：真人全景 13–17%，最长 ≤8 秒。拍表里主播全屏一次别超过 12 秒，长段落拆成「全屏 → 小窗图表 → 全屏」。
