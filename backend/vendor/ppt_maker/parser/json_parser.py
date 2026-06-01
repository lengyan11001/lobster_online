"""JSON 输入解析器

将 JSON 格式的演示文稿定义转换为 PresentationModel。
"""

import json

from ppt_maker.models import (
    PresentationModel,
    SlideModel,
    SlideType,
    TextElement,
    TextStyle,
    ChartElement,
    ChartSeries,
    ChartType,
    TableElement,
)
from ppt_maker.errors import FileParseError


def parse_json(filepath: str) -> PresentationModel:
    """解析 JSON 文件为 PresentationModel"""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, PermissionError) as e:
        raise FileParseError(filepath, str(e))
    except json.JSONDecodeError as e:
        raise FileParseError(filepath, f"JSON 格式错误: {e}")
    return parse_json_dict(data)


def parse_json_string(content: str) -> PresentationModel:
    """解析 JSON 字符串为 PresentationModel"""
    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        raise FileParseError("<string>", f"JSON 格式错误: {e}")
    return parse_json_dict(data)


def parse_json_dict(data: dict) -> PresentationModel:
    """将字典转换为 PresentationModel"""
    slides = []
    for slide_data in data.get("slides", []):
        slides.append(_parse_slide(slide_data))

    return PresentationModel(
        title=data.get("title", "Untitled"),
        author=data.get("author", ""),
        subject=data.get("subject", ""),
        language=data.get("language", "zh-CN"),
        slide_size=data.get("slide_size", "16:9"),
        theme_name=data.get("theme_name", "default"),
        template_path=data.get("template_path"),
        slides=slides,
    )


def _parse_slide(data: dict) -> SlideModel:
    """将字典转换为 SlideModel"""
    slide_type_str = data.get("slide_type", "content")
    try:
        slide_type = SlideType(slide_type_str)
    except ValueError:
        slide_type = SlideType.CONTENT

    elements = []
    for elem_data in data.get("elements", []):
        element = _parse_element(elem_data)
        if element:
            elements.append(element)

    return SlideModel(
        slide_type=slide_type,
        title=data.get("title", ""),
        subtitle=data.get("subtitle", ""),
        elements=elements,
        notes=data.get("notes", ""),
    )


def _parse_element(data: dict):
    """将字典转换为元素模型"""
    elem_type = data.get("element_type", "text")

    if elem_type == "chart":
        try:
            chart_type = ChartType(data.get("chart_type", "bar"))
        except ValueError:
            chart_type = ChartType.BAR
        return ChartElement(
            chart_type=chart_type,
            title=data.get("title", ""),
            categories=data.get("categories", []),
            series=[
                ChartSeries(
                    name=s.get("name", ""),
                    values=s.get("values", []),
                )
                for s in data.get("series", [])
            ],
            show_legend=data.get("show_legend", True),
            show_data_labels=data.get("show_data_labels", False),
        )

    elif elem_type == "table":
        return TableElement(
            headers=data.get("headers", []),
            rows=data.get("rows", []),
        )

    else:
        style_data = data.get("style", {})
        style = TextStyle(
            font_name=style_data.get("font_name", "微软雅黑"),
            font_name_latin=style_data.get("font_name_latin", "Calibri"),
            font_size=style_data.get("font_size", 18),
            bold=style_data.get("bold", False),
            italic=style_data.get("italic", False),
            color=style_data.get("color", "#333333"),
            alignment=style_data.get("alignment", "left"),
            line_spacing=style_data.get("line_spacing", 1.4),
            bullet=style_data.get("bullet", False),
            bullet_level=style_data.get("bullet_level", 0),
        )
        return TextElement(
            text=data.get("text", ""),
            style=style,
        )
