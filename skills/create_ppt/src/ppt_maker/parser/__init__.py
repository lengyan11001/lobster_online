from pathlib import Path

from .markdown_parser import parse as parse_markdown
from .markdown_parser import parse_string as parse_markdown_string
from .json_parser import parse_json, parse_json_string
from .yaml_parser import parse_yaml, parse_yaml_string


def parse(filepath: str, format: str = "auto") -> "PresentationModel":
    """解析输入文件为 PresentationModel

    Args:
        filepath: 输入文件路径
        format: "markdown" | "json" | "yaml" | "auto"(按扩展名推断)
    """
    if format == "auto":
        ext = Path(filepath).suffix.lower()
        format_map = {
            ".md": "markdown",
            ".markdown": "markdown",
            ".json": "json",
            ".yaml": "yaml",
            ".yml": "yaml",
        }
        format = format_map.get(ext, "markdown")

    if format == "json":
        return parse_json(filepath)
    elif format in ("yaml", "yml"):
        return parse_yaml(filepath)
    else:
        return parse_markdown(filepath)


def parse_string(content: str, format: str = "markdown") -> "PresentationModel":
    """解析字符串内容为 PresentationModel"""
    if format == "json":
        return parse_json_string(content)
    elif format in ("yaml", "yml"):
        return parse_yaml_string(content)
    else:
        return parse_markdown_string(content)


from ppt_maker.models import PresentationModel  # noqa: E402

__all__ = ["parse", "parse_string", "parse_markdown", "parse_json", "parse_yaml"]
