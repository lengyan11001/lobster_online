"""YAML 输入解析器

将 YAML 格式的演示文稿定义转换为 PresentationModel。
YAML 格式与 JSON 结构完全相同，只是语法不同。
"""

import yaml

from ppt_maker.parser.json_parser import parse_json_dict
from ppt_maker.errors import FileParseError


def parse_yaml(filepath: str) -> "PresentationModel":
    """解析 YAML 文件为 PresentationModel"""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (FileNotFoundError, PermissionError) as e:
        raise FileParseError(filepath, str(e))
    except yaml.YAMLError as e:
        raise FileParseError(filepath, f"YAML 格式错误: {e}")
    return parse_json_dict(data)


def parse_yaml_string(content: str) -> "PresentationModel":
    """解析 YAML 字符串为 PresentationModel"""
    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError as e:
        raise FileParseError("<string>", f"YAML 格式错误: {e}")
    return parse_json_dict(data)


# 延迟导入避免循环引用
from ppt_maker.models import PresentationModel  # noqa: E402
