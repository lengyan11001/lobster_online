"""自定义错误类型

提供结构化的错误信息，方便 CLI 展示和程序处理。
"""


class PptMakerError(Exception):
    """基础错误类"""

    def __init__(self, message: str, detail: str = ""):
        self.message = message
        self.detail = detail
        super().__init__(message)


class FileParseError(PptMakerError):
    """文件解析错误"""

    def __init__(self, filepath: str, reason: str = ""):
        self.filepath = filepath
        super().__init__(
            f"无法解析文件: {filepath}",
            detail=reason,
        )


class TemplateError(PptMakerError):
    """模板相关错误"""

    def __init__(self, template_path: str, reason: str = ""):
        self.template_path = template_path
        super().__init__(
            f"模板错误: {template_path}",
            detail=reason,
        )


class AIGenerationError(PptMakerError):
    """AI 生成错误"""

    def __init__(self, reason: str = "", model: str = ""):
        self.model = model
        super().__init__(
            f"AI 生成失败" + (f" (模型: {model})" if model else ""),
            detail=reason,
        )


class RenderError(PptMakerError):
    """渲染错误"""

    def __init__(self, reason: str = ""):
        super().__init__("渲染失败", detail=reason)
