# media.edit · `operation: overlay_text` 参数说明

实现：`backend/app/services/media_edit_exec.py`（ffmpeg `drawtext`）。

## 规则

- **仅允许白名单字段**：多传任意未列出字段 → `400`/`ValueError`（不静默忽略）。
- **中文**：未指定 `font_file` 时自动解析 CJK 字体（或环境变量 `LOBSTER_DRAWTEXT_FONT`）；找不到则报错。
- **自定义位置**：`x_expr` 与 `y_expr` 必须**同时**提供，且为受限的 ffmpeg 表达式字符集（防注入）。
- **颜色**：命名色 `white/black/red/...`，或 `#RRGGBB` / `0xRRGGBB` / `0xRRGGBBAA`。使用 **8 位 hex 含 alpha** 时不要同时传对应的 `*_alpha` 字段。

## 字段一览

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `operation` | string | — | 固定 `overlay_text` |
| `asset_id` | string | — | 主素材 ID |
| `text` | string | — | 文案（必填） |
| `position` | enum | — | `top` / `center` / `bottom`，与 `vertical_align` 二选一，**后者优先** |
| `vertical_align` | enum | `top` | 垂直对齐 |
| `horizontal_align` | enum | `center` | 水平：`left` / `center` / `right` |
| `margin_x` | int | `40` | 0–4000；左/右对齐时距边；居中时作水平微调基准 |
| `margin_y` | int | `40` | 0–4000；上/下边距 |
| `offset_x` | int | `0` | -4000–4000；在计算位置上再平移 |
| `offset_y` | int | `0` | -4000–4000 |
| `font_size` | int | `48` | 8–200 |
| `font_color` | string | `white` | 见上「颜色」 |
| `font_alpha` | number | 不传则不着色带 @ | 0–1；与 6 位色/命名色联用 |
| `font_file` | string | — | 字体绝对路径 `.ttf/.ttc/.otf/.otc` |
| `x_expr` | string | — | 与 `y_expr` 同时出现，覆盖对齐参数 |
| `y_expr` | string | — | 同上 |
| `box` | bool | `false` | 是否文字背景框 |
| `box_color` | string | `black` | 背景色 |
| `box_alpha` | number | `0.5` | 0–1；`box_color` 为 `0xRRGGBBAA` 时勿传 |
| `box_border_width` | int | `0` | 0–80 |
| `border_width` | int | `0` | 0–40 文字描边 |
| `border_color` | string | `black` | 描边色 |
| `shadow_x` | int | `0` | -120–120；与 `shadow_y` **均为 0** 时不加阴影 |
| `shadow_y` | int | `0` | -120–120 |
| `shadow_color` | string | `black` | 阴影色 |
| `shadow_alpha` | number | `0.45` | 0–1 |
| `line_spacing` | int | 不传 | -200–200 多行行距 |
| `text_align` | enum | 不传 | 多行：`left` / `center` / `right` |
| `fix_bounds` | bool | `false` | 是否限制文字在画面内 |

JSON Schema 见：`mcp/capability_catalog.json`（`media.edit`）。
