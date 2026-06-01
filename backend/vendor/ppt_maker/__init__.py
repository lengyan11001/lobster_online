"""ppt-maker: 专业 PPT 创建工具

公共 API:
    create_from_outline(input_path, output_path, theme, template, config) -> str
    create_from_model(model, output_path, theme) -> str
    create_from_ai(topic, output_path, slides, theme, ai_config) -> str
"""

from ppt_maker.models import PresentationModel, ThemeModel
from ppt_maker.parser import parse, parse_string
from ppt_maker.template.builtin import load_builtin_theme, list_builtin_themes
from ppt_maker.renderer.core import Renderer
from ppt_maker.ai.generator import AIGenerator


def create_from_outline(
    input_path: str,
    output_path: str = "output.pptx",
    theme_name: str = "default",
    template_path: str | None = None,
) -> str:
    """从大纲文件创建 PPT

    Args:
        input_path: 输入文件路径 (Markdown/JSON/YAML)
        output_path: 输出 .pptx 文件路径
        theme_name: 内置主题名称
        template_path: 外部 .pptx 模板路径 (优先于 theme_name)

    Returns:
        输出文件路径
    """
    model = parse(input_path)
    if template_path:
        model.template_path = template_path
    if theme_name != "default":
        model.theme_name = theme_name
    return create_from_model(model, output_path)


def create_from_model(
    model: PresentationModel,
    output_path: str = "output.pptx",
    theme: ThemeModel | None = None,
) -> str:
    """从 PresentationModel 创建 PPT

    Args:
        model: 演示文稿数据模型
        output_path: 输出 .pptx 文件路径
        theme: 主题 (None 则从 model.theme_name 加载)

    Returns:
        输出文件路径
    """
    renderer = Renderer(model, theme)
    return renderer.render(output_path)


def create_from_ai(
    topic: str,
    output_path: str = "output.pptx",
    slide_count: int = 10,
    theme_name: str = "default",
    language: str = "zh-CN",
    instructions: str = "",
    ai_model: str = "gpt-5.4",
    api_key: str | None = None,
    base_url: str | None = None,
    template_path: str | None = None,
) -> str:
    """使用 AI 生成 PPT

    Args:
        topic: 演示文稿主题
        output_path: 输出 .pptx 文件路径
        slide_count: 幻灯片数量
        theme_name: 内置主题名称
        language: 语言
        instructions: 附加指令
        ai_model: AI 模型名称
        api_key: API 密钥
        base_url: API 端点 URL
        template_path: 外部模板路径

    Returns:
        输出文件路径
    """
    api_key = api_key or os.environ.get("HAODUOMI_API_KEY") or os.environ.get("OPENAI_API_KEY")
    base_url = base_url or os.environ.get("HAODUOMI_OPENAI_BASE_URL") or "https://api.lk888.ai/v1"
    generator = AIGenerator(api_key=api_key, model=ai_model, base_url=base_url)
    model = generator.generate_outline(
        topic=topic,
        slide_count=slide_count,
        language=language,
        instructions=instructions,
    )

    if template_path:
        model.template_path = template_path
    if theme_name != "default":
        model.theme_name = theme_name

    return create_from_model(model, output_path)


__all__ = [
    "create_from_outline",
    "create_from_model",
    "create_from_ai",
    "PresentationModel",
    "ThemeModel",
    "parse",
    "parse_string",
    "load_builtin_theme",
    "list_builtin_themes",
    "Renderer",
    "AIGenerator",
]
