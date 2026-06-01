"""表格渲染器"""

from pptx.util import Emu, Pt
from pptx.dml.color import RGBColor
from pptx.oxml.ns import qn
from lxml import etree

from ppt_maker.models import TableElement, TextStyle, LayoutZone, ThemeModel


class TableRenderer:
    """表格渲染器"""

    def __init__(self, theme: ThemeModel):
        self.theme = theme

    def render_table(self, slide, element: TableElement, zone: LayoutZone):
        """渲染表格元素到幻灯片"""
        if not element.headers and not element.rows:
            return

        num_rows = len(element.rows) + (1 if element.headers else 0)
        num_cols = len(element.headers) or max(len(r) for r in element.rows) if element.rows else 1

        if num_rows == 0 or num_cols == 0:
            return

        left = Emu(int(zone.x))
        top = Emu(int(zone.y))
        width = Emu(int(zone.width))
        height = Emu(int(zone.height))

        table_shape = slide.shapes.add_table(num_rows, num_cols, left, top, width, height)
        table = table_shape.table

        # 设置列宽均匀分布
        col_width = int(width / num_cols)
        for i in range(num_cols):
            table.columns[i].width = col_width

        # 渲染表头
        header_style = element.header_style or self._default_header_style()
        if element.headers:
            for j, header_text in enumerate(element.headers):
                cell = table.cell(0, j)
                cell.text = header_text
                self._apply_cell_style(cell, header_style, is_header=True)

        # 渲染数据行
        cell_style = element.cell_style or self._default_cell_style()
        start_row = 1 if element.headers else 0
        for i, row_data in enumerate(element.rows):
            for j, cell_text in enumerate(row_data):
                if j >= num_cols:
                    break
                cell = table.cell(start_row + i, j)
                cell.text = cell_text
                is_first_col = element.first_column_highlight and j == 0
                self._apply_cell_style(cell, cell_style, is_first_col=is_first_col)

    def _apply_cell_style(self, cell, style: TextStyle, is_header: bool = False, is_first_col: bool = False):
        """应用单元格样式"""
        # 填充色
        try:
            fill = cell.fill
            fill.solid()
            if is_header:
                fill.fore_color.rgb = RGBColor.from_string(
                    self.theme.color_scheme.primary.lstrip("#")
                )
            elif is_first_col:
                fill.fore_color.rgb = RGBColor.from_string(
                    self.theme.color_scheme.background.lstrip("#")
                )
            else:
                fill.fore_color.rgb = RGBColor.from_string(
                    self.theme.color_scheme.background.lstrip("#")
                )
        except Exception:
            pass

        # 文本样式
        for paragraph in cell.text_frame.paragraphs:
            paragraph.alignment = self._alignment_value(style.alignment)

            for run in paragraph.runs:
                run.font.size = Pt(style.font_size)
                run.font.bold = style.bold or is_header or is_first_col
                run.font.italic = style.italic

                # 颜色
                if is_header:
                    color = self.theme.color_scheme.text_light
                else:
                    color = style.color
                try:
                    run.font.color.rgb = RGBColor.from_string(color.lstrip("#"))
                except Exception:
                    pass

                # CJK 字体 workaround
                run.font.name = style.font_name_latin
                rPr = run._r.get_or_add_rPr()
                ea = rPr.find(qn("a:ea"))
                if ea is None:
                    ea = etree.SubElement(rPr, qn("a:ea"))
                ea.set("typeface", style.font_name)
                latin = rPr.find(qn("a:latin"))
                if latin is None:
                    latin = etree.SubElement(rPr, qn("a:latin"))
                latin.set("typeface", style.font_name_latin)

        # 单元格边距
        cell.margin_left = Emu(int(91440))    # ~0.1 inch
        cell.margin_right = Emu(int(91440))
        cell.margin_top = Emu(int(45720))     # ~0.05 inch
        cell.margin_bottom = Emu(int(45720))

    def _default_header_style(self) -> TextStyle:
        """默认表头样式"""
        return TextStyle(
            font_name=self.theme.font_scheme.body_font,
            font_name_latin=self.theme.font_scheme.body_font_latin,
            font_size=self.theme.font_scheme.body_size,
            bold=True,
            color=self.theme.color_scheme.text_light,
            alignment="center",
        )

    def _default_cell_style(self) -> TextStyle:
        """默认单元格样式"""
        return TextStyle(
            font_name=self.theme.font_scheme.body_font,
            font_name_latin=self.theme.font_scheme.body_font_latin,
            font_size=self.theme.font_scheme.body_size - 2,
            color=self.theme.color_scheme.text_dark,
            alignment="center",
        )

    @staticmethod
    def _alignment_value(alignment: str):
        from pptx.enum.text import PP_ALIGN
        return {
            "left": PP_ALIGN.LEFT,
            "center": PP_ALIGN.CENTER,
            "right": PP_ALIGN.RIGHT,
        }.get(alignment, PP_ALIGN.CENTER)
