"""CJK (中日韩) 文本处理工具"""

CJK_LINE_SPACING = 1.4
LATIN_LINE_SPACING = 1.2


def is_cjk_char(ch: str) -> bool:
    """判断字符是否为 CJK 字符"""
    cp = ord(ch)
    return (
        (0x4E00 <= cp <= 0x9FFF)   # CJK Unified Ideographs
        or (0x3400 <= cp <= 0x4DBF)  # CJK Extension A
        or (0x3000 <= cp <= 0x303F)  # CJK Symbols and Punctuation
        or (0xFF00 <= cp <= 0xFFEF)  # Fullwidth Forms
        or (0xF900 <= cp <= 0xFAFF)  # CJK Compatibility Ideographs
    )


def get_cjk_ratio(text: str) -> float:
    """计算文本中 CJK 字符的比例 (0.0~1.0)"""
    if not text:
        return 0.0
    cjk_count = sum(1 for ch in text if is_cjk_char(ch))
    return cjk_count / len(text)


def get_default_line_spacing(language: str) -> float:
    """根据语言获取默认行间距"""
    if language.startswith("zh") or language.startswith("ja") or language.startswith("ko"):
        return CJK_LINE_SPACING
    return LATIN_LINE_SPACING


def estimate_char_width(font_size_pt: int, is_cjk: bool) -> float:
    """估算单个字符宽度 (pt)

    CJK 字符宽度约等于字号，西文字符约为字号的一半。
    """
    return float(font_size_pt) if is_cjk else float(font_size_pt) * 0.5
