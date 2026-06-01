"""主渲染器 - 将 PresentationModel 转换为 .pptx 文件"""

import os
from pptx import Presentation
from pptx.util import Emu
from pptx.dml.color import RGBColor

from ppt_maker.models import (
    PresentationModel,
    SlideModel,
    SlideType,
    TextElement,
    ChartElement,
    TableElement,
    ImageElement,
    ElementModel,
    LayoutZone,
    ThemeModel,
)
from ppt_maker.template.builtin import load_builtin_theme
from ppt_maker.template.external import ExternalTemplate
from ppt_maker.layout.engine import LayoutEngine
from ppt_maker.renderer.text import TextRenderer
from ppt_maker.renderer.chart import ChartRenderer
from ppt_maker.renderer.table import TableRenderer
from ppt_maker.renderer.image import ImageRenderer

# 进度条工具
try:
    from ppt_maker.progress import make_slide_progress
except ImportError:
    try:
        from progress_utils import make_slide_progress
    except ImportError:
        make_slide_progress = None


class Renderer:
    """PPT 渲染器"""

    def __init__(self, model: PresentationModel, theme: ThemeModel | None = None):
        self.model = model
        self.theme = theme or load_builtin_theme(model.theme_name)
        self.layout_engine = LayoutEngine(self.theme, model.slide_size)
        self.text_renderer = TextRenderer(self.theme)
        self.chart_renderer = ChartRenderer(self.theme)
        self.table_renderer = TableRenderer(self.theme)
        self.image_renderer = ImageRenderer(self.theme)
        self.prs: Presentation | None = None
        self._external_template: ExternalTemplate | None = None

    def render(self, output_path: str, show_progress: bool = True) -> str:
        """渲染完整演示文稿并保存"""
        self._init_presentation()

        total = len(self.model.slides)
        use_progress = show_progress and make_slide_progress and total > 1

        if use_progress:
            with make_slide_progress() as progress:
                task = progress.add_task("生成PPT", total=total)
                for slide_model in self.model.slides:
                    self._render_slide(slide_model)
                    progress.advance(task)
        else:
            for slide_model in self.model.slides:
                self._render_slide(slide_model)

        self._set_metadata()
        self.prs.save(output_path)
        return output_path

    def _init_presentation(self):
        """初始化 python-pptx Presentation 对象"""
        if self.model.template_path:
            self._external_template = ExternalTemplate(self.model.template_path)
            self.prs = self._external_template.presentation
            # 尝试从模板提取主题并合并
            try:
                extracted = self._external_template.extract_theme()
                # 合并模板颜色到当前主题
                self.theme = extracted
                self.layout_engine.theme = self.theme
                self.text_renderer.theme = self.theme
                self.chart_renderer.theme = self.theme
                self.table_renderer.theme = self.theme
                self.image_renderer.theme = self.theme
            except Exception:
                pass
        else:
            self.prs = Presentation()
            # 设置幻灯片尺寸
            if self.model.slide_size == "4:3":
                self.prs.slide_width = 9144000
                self.prs.slide_height = 6858000
            else:
                self.prs.slide_width = 12192000
                self.prs.slide_height = 6858000

    def _render_slide(self, slide_model: SlideModel):
        """渲染单页幻灯片"""
        # 选择布局
        if self._external_template:
            layout = self._external_template.get_layout_for_type(slide_model.slide_type)
            slide = self.prs.slides.add_slide(layout)
        else:
            blank_layout = self.prs.slide_layouts[6]  # 空白布局
            slide = self.prs.slides.add_slide(blank_layout)
            self._set_background(slide)

        # 布局计算
        layout_result = self.layout_engine.layout_slide(slide_model)

        # 渲染每个元素
        for element, zone in layout_result:
            self._render_element(slide, element, zone)

        # 设置演讲者备注
        if slide_model.notes:
            slide.notes_slide.notes_text_frame.text = slide_model.notes

    def _set_background(self, slide):
        """设置幻灯片背景色"""
        bg = slide.background
        fill = bg.fill
        fill.solid()
        color_str = self.theme.color_scheme.background.lstrip("#")
        fill.fore_color.rgb = RGBColor.from_string(color_str)

    def _render_element(self, slide, element: ElementModel, zone: LayoutZone):
        """渲染单个元素"""
        if isinstance(element, TextElement):
            self._render_text_element(slide, element, zone)
        elif isinstance(element, ChartElement):
            self.chart_renderer.render_chart(slide, element, zone)
        elif isinstance(element, TableElement):
            self.table_renderer.render_table(slide, element, zone)
        elif isinstance(element, ImageElement):
            self.image_renderer.render_image(slide, element, zone)

    def _render_text_element(self, slide, element: TextElement, zone: LayoutZone):
        """渲染文本元素"""
        left = Emu(int(zone.x))
        top = Emu(int(zone.y))
        width = Emu(int(zone.width))
        height = Emu(int(zone.height))

        shape = slide.shapes.add_textbox(left, top, width, height)
        self.text_renderer.render_text(shape, element, zone)

    def _set_metadata(self):
        """设置演示文稿元数据"""
        if self.model.title:
            self.prs.core_properties.title = self.model.title
        if self.model.author:
            self.prs.core_properties.author = self.model.author
        if self.model.subject:
            self.prs.core_properties.subject = self.model.subject
