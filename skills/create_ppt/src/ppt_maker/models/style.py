from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Optional


class ColorScheme(BaseModel):
    """配色方案"""

    primary: str = "#2B579A"
    secondary: str = "#4472C4"
    accent: str = "#ED7D31"
    background: str = "#FFFFFF"
    text_dark: str = "#333333"
    text_light: str = "#FFFFFF"
    text_secondary: str = "#666666"
    divider: str = "#E0E0E0"


class TextStyle(BaseModel):
    """文本样式"""

    font_name: str = "微软雅黑"
    font_name_latin: str = "Calibri"
    font_size: int = 18
    bold: bool = False
    italic: bool = False
    color: str = "#333333"
    alignment: str = "left"
    line_spacing: float = 1.4
    bullet: bool = False
    bullet_level: int = 0


class TextRun(BaseModel):
    """文本运行 (同一行内不同样式的片段)"""

    text: str = ""
    style: Optional[TextStyle] = None


class FontScheme(BaseModel):
    """字体方案"""

    title_font: str = "微软雅黑"
    title_font_latin: str = "Calibri"
    title_size: int = 36
    subtitle_font: str = "微软雅黑"
    subtitle_font_latin: str = "Calibri"
    subtitle_size: int = 24
    body_font: str = "微软雅黑"
    body_font_latin: str = "Calibri"
    body_size: int = 18
    caption_font: str = "微软雅黑"
    caption_font_latin: str = "Calibri"
    caption_size: int = 14

    def make_title_style(self) -> TextStyle:
        return TextStyle(
            font_name=self.title_font,
            font_name_latin=self.title_font_latin,
            font_size=self.title_size,
            bold=True,
            alignment="center",
            line_spacing=1.3,
        )

    def make_subtitle_style(self) -> TextStyle:
        return TextStyle(
            font_name=self.subtitle_font,
            font_name_latin=self.subtitle_font_latin,
            font_size=self.subtitle_size,
            alignment="center",
            line_spacing=1.4,
        )

    def make_section_title_style(self) -> TextStyle:
        return TextStyle(
            font_name=self.title_font,
            font_name_latin=self.title_font_latin,
            font_size=40,
            bold=True,
            alignment="center",
            line_spacing=1.3,
        )

    def make_body_style(self) -> TextStyle:
        return TextStyle(
            font_name=self.body_font,
            font_name_latin=self.body_font_latin,
            font_size=self.body_size,
            line_spacing=1.4,
        )


class SpacingScheme(BaseModel):
    """间距方案"""

    title_line_spacing: float = 1.3
    body_line_spacing: float = 1.4
    paragraph_spacing: float = 0.5
    slide_margin_top: float = 0.08
    slide_margin_bottom: float = 0.08
    slide_margin_left: float = 0.06
    slide_margin_right: float = 0.06


class ThemeModel(BaseModel):
    """完整主题定义"""

    name: str = "default"
    display_name: str = "简约白"
    color_scheme: ColorScheme = Field(default_factory=ColorScheme)
    font_scheme: FontScheme = Field(default_factory=FontScheme)
    spacing_scheme: SpacingScheme = Field(default_factory=SpacingScheme)
