"""文本溢出检测与自适应

估算文本渲染后所需高度，溢出时自动缩小字号或截断。
"""

from ppt_maker.models import TextStyle
from ppt_maker.layout.cjk import is_cjk_char, estimate_char_width


# 1 point = 12700 EMU
PT_TO_EMU = 12700


class TextFitter:
    """文本溢出检测与自适应"""

    def __init__(self, min_font_size: int = 12, max_shrink_pt: int = 4):
        """
        Args:
            min_font_size: 缩小字号的下限 (pt)
            max_shrink_pt: 最多缩小的字号 (pt)
        """
        self.min_font_size = min_font_size
        self.max_shrink_pt = max_shrink_pt

    def estimate_text_height(
        self, text: str, style: TextStyle, container_width_emu: int
    ) -> int:
        """估算文本渲染后所需高度 (EMU)

        算法:
        1. 估算单行高度 = font_size * line_spacing
        2. 估算每行字符数 (CJK 字宽≈字号, 西文≈0.5字号)
        3. 计算总行数
        4. 返回总高度
        """
        if not text:
            return 0

        font_size_emu = style.font_size * PT_TO_EMU
        line_height_emu = int(font_size_emu * style.line_spacing)

        # 按换行符分段
        paragraphs = text.split("\n")
        total_lines = 0

        for para in paragraphs:
            if not para.strip():
                total_lines += 1  # 空行仍占一行
                continue

            chars_per_line = self._estimate_chars_per_line(
                para, style.font_size, container_width_emu
            )
            if chars_per_line <= 0:
                chars_per_line = 1
            lines = max(1, -(-len(para) // chars_per_line))  # ceil division
            total_lines += lines

        return total_lines * line_height_emu

    def fit_text(
        self, text: str, style: TextStyle, container_width_emu: int, container_height_emu: int
    ) -> tuple[str, TextStyle]:
        """自适应文本，确保不溢出

        策略:
        1. 先尝试原文本
        2. 若溢出，尝试缩小字号 (最多减 max_shrink_pt)
        3. 若仍溢出，截断文本并加 "..."

        Returns:
            (fitted_text, adjusted_style)
        """
        current_text = text
        current_style = style.model_copy()

        # 1. 检查原文本
        needed = self.estimate_text_height(current_text, current_style, container_width_emu)
        if needed <= container_height_emu:
            return current_text, current_style

        # 2. 缩小字号
        for shrink in range(1, self.max_shrink_pt + 1):
            new_size = style.font_size - shrink
            if new_size < self.min_font_size:
                break
            current_style = current_style.model_copy(update={"font_size": new_size})
            needed = self.estimate_text_height(current_text, current_style, container_width_emu)
            if needed <= container_height_emu:
                return current_text, current_style

        # 3. 截断文本
        current_style = style.model_copy(update={"font_size": max(style.font_size - self.max_shrink_pt, self.min_font_size)})
        max_chars = self._estimate_max_chars(
            container_width_emu, container_height_emu, current_style
        )
        if len(current_text) > max_chars:
            current_text = current_text[: max(max_chars - 3, 0)] + "..."

        return current_text, current_style

    def _estimate_chars_per_line(
        self, text: str, font_size_pt: int, container_width_emu: int
    ) -> int:
        """估算每行可容纳的字符数"""
        if not text:
            return 1

        # 计算混合文本的平均字宽
        cjk_count = sum(1 for ch in text if is_cjk_char(ch))
        total = len(text)
        cjk_ratio = cjk_count / total if total > 0 else 0

        avg_char_width_emu = (
            cjk_ratio * estimate_char_width(font_size_pt, True)
            + (1 - cjk_ratio) * estimate_char_width(font_size_pt, False)
        ) * PT_TO_EMU

        if avg_char_width_emu <= 0:
            return 1

        return max(1, int(container_width_emu / avg_char_width_emu))

    def _estimate_max_chars(
        self, container_width_emu: int, container_height_emu: int, style: TextStyle
    ) -> int:
        """估算容器可容纳的最大字符数"""
        font_size_emu = style.font_size * PT_TO_EMU
        line_height_emu = int(font_size_emu * style.line_spacing)

        max_lines = max(1, container_height_emu // line_height_emu)
        chars_per_line = self._estimate_chars_per_line(
            "测试text", style.font_size, container_width_emu
        )

        return max_lines * chars_per_line
