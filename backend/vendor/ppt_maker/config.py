"""配置文件加载器"""

import os
from pathlib import Path
from typing import Optional

import yaml


class Config:
    """应用配置"""

    def __init__(self, data: dict | None = None):
        self._data = data or {}

    @classmethod
    def from_file(cls, filepath: str) -> "Config":
        """从 YAML 文件加载配置"""
        with open(filepath, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return cls(data)

    @classmethod
    def from_env(cls) -> "Config":
        """从环境变量构建配置"""
        haoduomi_key = os.environ.get("HAODUOMI_API_KEY", "")
        if haoduomi_key:
            return cls({
                "ai": {
                    "api_key_env": "HAODUOMI_API_KEY",
                    "base_url": os.environ.get("HAODUOMI_OPENAI_BASE_URL") or "https://api.lk888.ai/v1",
                }
            })
        return cls({
            "ai": {
                "api_key_env": "OPENAI_API_KEY",
                "base_url": os.environ.get("OPENAI_BASE_URL"),
            }
        })

    def merge(self, other: "Config") -> "Config":
        """合并两个配置 (other 覆盖 self)"""
        merged = _deep_merge(self._data, other._data)
        return Config(merged)

    # --- 便捷属性 ---

    @property
    def output_filename(self) -> str:
        return self._data.get("output", {}).get("default_filename", "output.pptx")

    @property
    def slide_size(self) -> str:
        return self._data.get("output", {}).get("slide_size", "16:9")

    @property
    def theme_name(self) -> str:
        return self._data.get("theme", {}).get("name", "default")

    @property
    def template_path(self) -> Optional[str]:
        return self._data.get("theme", {}).get("template_path")

    @property
    def ai_model(self) -> str:
        return self._data.get("ai", {}).get("model", "gpt-5.4")

    @property
    def ai_base_url(self) -> Optional[str]:
        return self._data.get("ai", {}).get("base_url")

    @property
    def ai_temperature(self) -> float:
        return self._data.get("ai", {}).get("temperature", 0.7)

    @property
    def ai_max_tokens(self) -> int:
        return self._data.get("ai", {}).get("max_tokens", 4096)

    @property
    def cjk_font(self) -> str:
        return self._data.get("cjk", {}).get("default_font", "微软雅黑")

    @property
    def cjk_latin_font(self) -> str:
        return self._data.get("cjk", {}).get("latin_font", "Calibri")

    @property
    def cjk_line_spacing(self) -> float:
        return self._data.get("cjk", {}).get("line_spacing", 1.4)

    @property
    def overflow_strategy(self) -> str:
        return self._data.get("text_overflow", {}).get("strategy", "shrink")

    @property
    def overflow_min_font_size(self) -> int:
        return self._data.get("text_overflow", {}).get("min_font_size", 12)

    @property
    def overflow_max_shrink_pt(self) -> int:
        return self._data.get("text_overflow", {}).get("max_shrink_pt", 4)

    @property
    def template_layout_map(self) -> Optional[dict]:
        return self._data.get("template_layout_map")

    @property
    def raw(self) -> dict:
        return self._data


def _deep_merge(base: dict, override: dict) -> dict:
    """深度合并字典，override 的值覆盖 base"""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result
