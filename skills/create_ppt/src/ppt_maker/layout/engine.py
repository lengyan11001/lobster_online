"""布局引擎 - 将内容元素映射到幻灯片上的精确位置"""

from ppt_maker.models import (
    SlideModel,
    SlideType,
    TextElement,
    ChartElement,
    TableElement,
    ElementModel,
    LayoutZone,
    ThemeModel,
    TextStyle,
    TextRun,
)
from ppt_maker.template.layout_map import LAYOUT_PRESETS


# 标准幻灯片尺寸 (EMU)
SLIDE_SIZE_16_9 = (12192000, 6858000)   # 25.4cm x 14.29cm
SLIDE_SIZE_4_3 = (9144000, 6858000)     # 19.05cm x 14.29cm


class LayoutEngine:
    """布局引擎"""

    def __init__(self, theme: ThemeModel, slide_size: str = "16:9"):
        self.theme = theme
        if slide_size == "4:3":
            self.slide_w, self.slide_h = SLIDE_SIZE_4_3
        else:
            self.slide_w, self.slide_h = SLIDE_SIZE_16_9

    def layout_slide(self, slide: SlideModel) -> list[tuple[ElementModel, LayoutZone]]:
        """为幻灯片中每个元素计算绝对位置 (EMU)"""
        preset = LAYOUT_PRESETS.get(slide.slide_type, LAYOUT_PRESETS[SlideType.CONTENT])
        result = []

        if slide.slide_type == SlideType.TITLE:
            result = self._layout_title(slide, preset)
        elif slide.slide_type == SlideType.SECTION:
            result = self._layout_section(slide, preset)
        elif slide.slide_type == SlideType.CONTENT:
            result = self._layout_content(slide, preset)
        elif slide.slide_type == SlideType.TWO_COLUMN:
            result = self._layout_two_column(slide, preset)
        elif slide.slide_type == SlideType.CHART:
            result = self._layout_chart(slide, preset)
        elif slide.slide_type == SlideType.TABLE:
            result = self._layout_table(slide, preset)
        elif slide.slide_type == SlideType.ENDING:
            result = self._layout_ending(slide, preset)
        else:
            result = self._layout_content(slide, preset)

        return result

    def _to_emu(self, zone: LayoutZone) -> LayoutZone:
        """比例坐标转 EMU 绝对坐标"""
        return LayoutZone(
            x=int(zone.x * self.slide_w),
            y=int(zone.y * self.slide_h),
            width=int(zone.width * self.slide_w),
            height=int(zone.height * self.slide_h),
        )

    def _layout_title(self, slide: SlideModel, preset) -> list:
        result = []
        title_elem = TextElement(
            text=slide.title,
            style=self.theme.font_scheme.make_title_style(),
        )
        result.append((title_elem, self._to_emu(preset.title_zone)))
        if slide.subtitle:
            sub_elem = TextElement(
                text=slide.subtitle,
                style=self.theme.font_scheme.make_subtitle_style(),
            )
            result.append((sub_elem, self._to_emu(preset.content_zone)))
        return result

    def _layout_section(self, slide: SlideModel, preset) -> list:
        title_elem = TextElement(
            text=slide.title,
            style=self.theme.font_scheme.make_section_title_style(),
        )
        return [(title_elem, self._to_emu(preset.title_zone))]

    def _layout_content(self, slide: SlideModel, preset) -> list:
        result = []
        if slide.title:
            title_elem = TextElement(
                text=slide.title,
                style=self.theme.font_scheme.make_title_style(),
            )
            result.append((title_elem, self._to_emu(preset.title_zone)))
        if preset.content_zone:
            content_zone = self._to_emu(preset.content_zone)
            text_elements = [e for e in slide.elements if isinstance(e, TextElement)]
            non_text_elements = [e for e in slide.elements if not isinstance(e, TextElement)]

            if text_elements:
                combined = self._combine_text_elements(text_elements)
                result.append((combined, content_zone))
            for elem in non_text_elements:
                result.append((elem, content_zone))
        return result

    def _layout_two_column(self, slide: SlideModel, preset) -> list:
        result = []
        if slide.title:
            title_elem = TextElement(
                text=slide.title,
                style=self.theme.font_scheme.make_title_style(),
            )
            result.append((title_elem, self._to_emu(preset.title_zone)))

        text_elements = [e for e in slide.elements if isinstance(e, TextElement)]
        mid = max(1, len(text_elements) // 2) if text_elements else 0

        if text_elements[:mid] and preset.content_zone:
            result.append((self._combine_text_elements(text_elements[:mid]), self._to_emu(preset.content_zone)))
        if text_elements[mid:] and preset.secondary_zone:
            result.append((self._combine_text_elements(text_elements[mid:]), self._to_emu(preset.secondary_zone)))
        return result

    def _layout_chart(self, slide: SlideModel, preset) -> list:
        result = []
        if slide.title:
            title_elem = TextElement(
                text=slide.title,
                style=self.theme.font_scheme.make_title_style(),
            )
            result.append((title_elem, self._to_emu(preset.title_zone)))
        chart_elements = [e for e in slide.elements if isinstance(e, ChartElement)]
        if chart_elements and preset.content_zone:
            result.append((chart_elements[0], self._to_emu(preset.content_zone)))
        return result

    def _layout_table(self, slide: SlideModel, preset) -> list:
        result = []
        if slide.title:
            title_elem = TextElement(
                text=slide.title,
                style=self.theme.font_scheme.make_title_style(),
            )
            result.append((title_elem, self._to_emu(preset.title_zone)))
        table_elements = [e for e in slide.elements if isinstance(e, TableElement)]
        if table_elements and preset.content_zone:
            result.append((table_elements[0], self._to_emu(preset.content_zone)))
        return result

    def _layout_ending(self, slide: SlideModel, preset) -> list:
        result = []
        title_elem = TextElement(
            text=slide.title or "谢谢",
            style=self.theme.font_scheme.make_section_title_style(),
        )
        result.append((title_elem, self._to_emu(preset.title_zone)))
        if slide.subtitle and preset.content_zone:
            sub_elem = TextElement(
                text=slide.subtitle,
                style=self.theme.font_scheme.make_subtitle_style(),
            )
            result.append((sub_elem, self._to_emu(preset.content_zone)))
        return result

    def _combine_text_elements(self, elements: list[TextElement]) -> TextElement:
        """将多个 TextElement 合并为一个"""
        runs = []
        for elem in elements:
            if elem.text:
                runs.append(TextRun(text=elem.text, style=elem.style))
            runs.extend(elem.runs)
        return TextElement(
            runs=runs,
            style=self.theme.font_scheme.make_body_style(),
        )
