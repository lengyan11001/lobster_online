"""统一的「内容语种」口径。

个人 IP 设置里的模板语种是唯一事实来源：
- 客户端写入模板 requirements.language / target_language / common；
- 所有会产出发布内容的 AI 提示词都必须带上同一条语言规则，避免中英混排。
"""
from __future__ import annotations

import re
from typing import Any, Optional

DEFAULT_LANGUAGE = "zh-CN"

LANGUAGES: tuple[tuple[str, str], ...] = (
    ("zh-CN", "简体中文"),
    ("zh-TW", "繁體中文"),
    ("en", "English"),
    ("ja", "日本語"),
    ("ko", "한국어"),
    ("th", "ไทย"),
    ("vi", "Tiếng Việt"),
    ("id", "Bahasa Indonesia"),
    ("ms", "Bahasa Melayu"),
    ("es", "Español"),
    ("pt", "Português"),
    ("fr", "Français"),
    ("de", "Deutsch"),
    ("ru", "Русский"),
    ("ar", "العربية"),
)

_BY_CODE = {code.lower(): code for code, _ in LANGUAGES}
_BY_LABEL = {label.strip().lower(): code for code, label in LANGUAGES}

_ALIASES = {
    "zh": "zh-CN",
    "zh-cn": "zh-CN",
    "zh-hans": "zh-CN",
    "zh-sg": "zh-CN",
    "zh-tw": "zh-TW",
    "zh-hant": "zh-TW",
    "zh-hk": "zh-TW",
    "en-us": "en",
    "en-gb": "en",
    "en-au": "en",
    "jp": "ja",
    "ja-jp": "ja",
    "kr": "ko",
    "ko-kr": "ko",
    "id-id": "id",
    "ms-my": "ms",
    "es-es": "es",
    "es-mx": "es",
    "pt-br": "pt",
    "pt-pt": "pt",
    "fr-fr": "fr",
    "de-de": "de",
    "ru-ru": "ru",
    "ar-sa": "ar",
    "th-th": "th",
    "vi-vn": "vi",
}


def normalize(value: Any) -> str:
    """把任意写法的语种（zh / zh-CN / 简体中文 / English）归一成标准 code。"""
    raw = str(value or "").strip()
    if not raw:
        return DEFAULT_LANGUAGE
    low = raw.lower().replace("_", "-")
    if low in _BY_CODE:
        return _BY_CODE[low]
    if low in _ALIASES:
        return _ALIASES[low]
    if low in _BY_LABEL:
        return _BY_LABEL[low]
    base = low.split("-")[0]
    if base in _ALIASES:
        return _ALIASES[base]
    if base in _BY_CODE:
        return _BY_CODE[base]
    return DEFAULT_LANGUAGE


def label(code: Any) -> str:
    norm = normalize(code)
    for c, text in LANGUAGES:
        if c == norm:
            return text
    return "简体中文"


def instruction(code: Any) -> str:
    norm = normalize(code)
    lab = label(norm)
    return (
        f"【目标语种·最高优先级】{lab}（{norm}）。"
        f"标题、正文、开场钩子、收口、话题标签、配图/画面中出现的文字都必须使用{lab}输出；"
        "不得中英混排，不得逐条切换语种；品牌名、产品名、专有名词可保留原文。"
    )


_RE_COMMON_LANG = re.compile(r"目标语种\s*[:：]\s*([^\s。；;，,]+)")


def from_requirements(requirements: Any) -> str:
    """从个人 IP 模板 requirements 里取语种；兼容语言名和 common 文案。"""
    if not isinstance(requirements, dict):
        return DEFAULT_LANGUAGE
    for key in (
        "language",
        "target_language",
        "content_language",
        "profile_language",
        "lang",
    ):
        value = requirements.get(key)
        if str(value or "").strip():
            return normalize(value)
    common = str(requirements.get("common") or "")
    match = _RE_COMMON_LANG.search(common)
    if match:
        return normalize(match.group(1))
    return DEFAULT_LANGUAGE


def from_template(requirements: Any, meta: Any = None) -> str:
    """模板行可能把语种放在 requirements，也可能只在 meta（个人默认配置行）。"""
    for source in (requirements, meta):
        if not isinstance(source, dict):
            continue
        for key in ("language", "target_language", "content_language", "profile_language", "lang"):
            value = source.get(key)
            if str(value or "").strip():
                return normalize(value)
        common = str(source.get("common") or "")
        match = _RE_COMMON_LANG.search(common)
        if match:
            return normalize(match.group(1))
    return DEFAULT_LANGUAGE


def with_instruction(text: str, code: Any) -> str:
    """在已有提示词前面补语言规则；已有同一条规则时不重复。"""
    rule = instruction(code)
    body = str(text or "").strip()
    if not body:
        return rule
    if "【目标语种" in body:
        return body
    return rule + "\n\n" + body
