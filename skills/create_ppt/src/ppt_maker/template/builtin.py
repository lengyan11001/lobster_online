"""内置主题加载器"""

import os
from pathlib import Path

import yaml

from ppt_maker.models.style import ThemeModel


_THEMES_DIR = Path(__file__).parent.parent.parent.parent / "themes"


def load_builtin_theme(name: str = "default") -> ThemeModel:
    """加载内置主题

    从 themes/{name}.yaml 读取主题配置，返回 ThemeModel。
    若文件不存在则返回默认 ThemeModel。
    """
    theme_file = _THEMES_DIR / f"{name}.yaml"
    if not theme_file.exists():
        return ThemeModel(name=name)

    with open(theme_file, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        return ThemeModel(name=name)

    return ThemeModel(**data)


def list_builtin_themes() -> list[str]:
    """列出所有可用内置主题名"""
    if not _THEMES_DIR.exists():
        return ["default"]
    return [
        f.stem
        for f in _THEMES_DIR.glob("*.yaml")
    ]
