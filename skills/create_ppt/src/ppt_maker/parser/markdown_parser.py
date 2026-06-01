"""Markdown 大纲解析器

将 Markdown 格式的大纲转换为 PresentationModel。

支持的 Markdown 语法:
- # 标题 -> 演示文稿标题
- <!-- key: value --> -> 元数据 (author, theme, language 等)
- ## 章节名 -> SECTION 页
- ### 页面标题 -> CONTENT/TITLE 页
- - 要点 -> TextElement(bullet=True)
- --- -> 双栏分隔符
- ```chart ... ``` -> ChartElement
- ```table ... ``` -> TableElement
"""

import re
from typing import Optional

import yaml

from ppt_maker.models import (
    PresentationModel,
    SlideModel,
    SlideType,
    TextElement,
    TextRun,
    TextStyle,
    ChartElement,
    ChartSeries,
    ChartType,
    TableElement,
)
from ppt_maker.errors import FileParseError


def parse(filepath: str) -> PresentationModel:
    """解析 Markdown 文件为 PresentationModel"""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
    except (FileNotFoundError, PermissionError) as e:
        raise FileParseError(filepath, str(e))
    return parse_string(content)


def parse_string(content: str) -> PresentationModel:
    """解析 Markdown 字符串为 PresentationModel"""
    lines = content.split("\n")

    # 解析元数据
    metadata = _parse_metadata(lines)
    author = metadata.pop("author", "")
    theme_name = metadata.pop("theme", "default")
    language = metadata.pop("language", "zh-CN")
    slide_size = metadata.pop("slide_size", "16:9")

    # 解析幻灯片 (同时提取 H1 标题)
    title_out: list[str] = []
    slides = _parse_slides(lines, title_out)
    title = metadata.pop("title", title_out[0] if title_out else "Untitled Presentation")

    return PresentationModel(
        title=title,
        author=author,
        language=language,
        slide_size=slide_size,
        theme_name=theme_name,
        slides=slides,
        metadata=metadata,
    )


def _parse_metadata(lines: list[str]) -> dict:
    """从注释中提取元数据"""
    metadata = {}
    for line in lines:
        m = re.match(r"<!--\s*(\w+)\s*:\s*(.+?)\s*-->", line.strip())
        if m:
            metadata[m.group(1)] = m.group(2).strip()
    return metadata


def _parse_slides(lines: list[str], title_out: list | None = None) -> list[SlideModel]:
    """解析所有幻灯片

    Args:
        title_out: 如果提供，将 H1 标题写入 title_out[0]
    """
    slides: list[SlideModel] = []
    current_slide: Optional[SlideModel] = None
    in_code_block = False
    code_block_type = ""
    code_block_lines: list[str] = []
    in_two_column = False
    first_column_elements: list = []
    first_column_done = False

    i = 0
    while i < len(lines):
        line = lines[i]

        # 代码块处理 (chart/table)
        if line.strip().startswith("```"):
            if not in_code_block:
                in_code_block = True
                code_block_type = line.strip()[3:].strip().lower()
                code_block_lines = []
                i += 1
                continue
            else:
                # 结束代码块
                element = _parse_code_block(code_block_type, code_block_lines)
                if element and current_slide:
                    current_slide.elements.append(element)
                in_code_block = False
                code_block_type = ""
                code_block_lines = []
                i += 1
                continue

        if in_code_block:
            code_block_lines.append(line)
            i += 1
            continue

        # 注释行跳过
        if line.strip().startswith("<!--"):
            i += 1
            continue

        # H1 - 演示文稿标题 (不创建幻灯片，仅提取标题)
        if line.startswith("# ") and not line.startswith("## "):
            if title_out is not None and len(title_out) == 0:
                title_out.append(line[2:].strip())
            if current_slide:
                slides.append(current_slide)
                current_slide = None
            i += 1
            continue

        # --- 分隔符 - 双栏标记
        if line.strip() == "---":
            if current_slide and not in_two_column:
                in_two_column = True
                first_column_elements = list(current_slide.elements)
                first_column_done = True
                current_slide.slide_type = SlideType.TWO_COLUMN
                current_slide.elements = [first_column_elements]
            i += 1
            continue

        # H2 - 章节分隔页
        if line.startswith("## "):
            if current_slide:
                slides.append(current_slide)
            section_title = line[3:].strip()
            current_slide = SlideModel(
                slide_type=SlideType.SECTION,
                title=section_title,
            )
            # 章节页后续可能还有内容，先不追加
            i += 1
            continue

        # H3 - 内容页标题
        if line.startswith("### "):
            if current_slide:
                # 无论当前是什么页，都先保存
                slides.append(current_slide)
            page_title = line[4:].strip()
            current_slide = SlideModel(
                slide_type=SlideType.CONTENT,
                title=page_title,
            )
            in_two_column = False
            first_column_done = False
            i += 1
            continue

        # 列表项
        if line.strip().startswith("- ") or line.strip().startswith("* "):
            if current_slide is None:
                current_slide = SlideModel(slide_type=SlideType.CONTENT)
            text = line.strip()[2:].strip()
            indent = len(line) - len(line.lstrip())
            level = indent // 2

            if in_two_column and first_column_done:
                # 第二栏内容
                if isinstance(current_slide.elements, list) and len(current_slide.elements) == 1 and isinstance(current_slide.elements[0], list):
                    current_slide.elements[0].append(
                        TextElement(text=text, style=TextStyle(bullet=True, bullet_level=level))
                    )
                else:
                    current_slide.elements.append(
                        TextElement(text=text, style=TextStyle(bullet=True, bullet_level=level))
                    )
            else:
                current_slide.elements.append(
                    TextElement(text=text, style=TextStyle(bullet=True, bullet_level=level))
                )
            i += 1
            continue

        # 普通文本 (非空行)
        stripped = line.strip()
        if stripped:
            if current_slide is None:
                # H1 后的副标题
                current_slide = SlideModel(
                    slide_type=SlideType.TITLE,
                    title="",
                    subtitle=stripped,
                )
            elif current_slide.slide_type == SlideType.SECTION and not current_slide.elements:
                # 章节页下方的文本变为内容
                pass
            elif not current_slide.title and current_slide.slide_type == SlideType.CONTENT:
                current_slide.title = stripped
            else:
                current_slide.elements.append(
                    TextElement(text=stripped)
                )

        i += 1

    # 保存最后一个幻灯片
    if current_slide:
        slides.append(current_slide)

    # 后处理: 将双栏的元素列表整理
    slides = _postprocess_slides(slides)

    return slides


def _parse_code_block(block_type: str, lines: list[str]) -> Optional[object]:
    """解析代码块内容"""
    content = "\n".join(lines)
    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError:
        return None

    if not isinstance(data, dict):
        return None

    if block_type == "chart":
        return ChartElement(
            chart_type=ChartType(data.get("type", "bar")),
            title=data.get("title", ""),
            categories=data.get("categories", []),
            series=[
                ChartSeries(name=s.get("name", ""), values=s.get("values", []))
                for s in data.get("series", [])
            ],
            show_legend=data.get("show_legend", True),
            show_data_labels=data.get("show_data_labels", False),
        )
    elif block_type == "table":
        return TableElement(
            headers=data.get("headers", []),
            rows=data.get("rows", []),
        )

    return None


def _postprocess_slides(slides: list[SlideModel]) -> list[SlideModel]:
    """后处理: 识别特殊页面类型"""
    result = []

    for slide in slides:
        # 处理双栏元素结构
        if slide.slide_type == SlideType.TWO_COLUMN:
            # 双栏元素在 _parse_slides 中已经处理为嵌套列表
            pass

        # 如果标题页只有标题没有元素，识别为 TITLE
        if slide.slide_type == SlideType.CONTENT and not slide.elements and slide.title:
            # 检查是否有副标题
            if not slide.subtitle:
                slide.slide_type = SlideType.TITLE

        result.append(slide)

    # 如果第一页没有明确是 TITLE，尝试识别
    if result and result[0].slide_type == SlideType.CONTENT and not result[0].elements:
        result[0].slide_type = SlideType.TITLE

    return result
