"""AI 内容生成器

调用 LLM API 生成/扩展演示文稿内容。
使用 OpenAI SDK，通过 base_url 兼容 DeepSeek/Moonshot/Ollama 等国产模型。
"""

import json
import os
from typing import Optional

from openai import OpenAI

from ppt_maker.models import (
    PresentationModel,
    SlideModel,
    SlideType,
    TextElement,
    TextStyle,
    ChartElement,
    ChartSeries,
    ChartType,
    TableElement,
)
from ppt_maker.ai.prompts import (
    OUTLINE_SYSTEM_PROMPT,
    OUTLINE_USER_TEMPLATE,
    EXPAND_SYSTEM_PROMPT,
    EXPAND_USER_TEMPLATE,
    REFINE_SYSTEM_PROMPT,
    REFINE_USER_TEMPLATE,
)


# 预定义的 base_url 映射 (国产模型提供商)
PROVIDER_BASE_URLS = {
    "deepseek": "https://api.deepseek.com/v1",
    "moonshot": "https://api.moonshot.cn/v1",
    "dashscope": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "ollama": "http://localhost:11434/v1",
    "haoduomi": "https://api.lk888.ai/v1",
    "lk888": "https://api.lk888.ai/v1",
}


class AIGenerator:
    """AI 内容生成器"""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gpt-5.4",
        base_url: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ):
        """
        Args:
            api_key: LLM API 密钥 (也可从环境变量读取)
            model: 模型名称
            base_url: 自定义 API 端点
            temperature: 生成温度
            max_tokens: 最大 token 数
        """
        resolved_key = api_key or os.environ.get("OPENAI_API_KEY") or os.environ.get("HAODUOMI_API_KEY", "")
        resolved_url = self._resolve_base_url(base_url, model)
        if not resolved_url and os.environ.get("HAODUOMI_API_KEY"):
            resolved_url = os.environ.get("HAODUOMI_OPENAI_BASE_URL") or "https://api.lk888.ai/v1"

        self.client = OpenAI(api_key=resolved_key, base_url=resolved_url)
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

    @staticmethod
    def _resolve_base_url(base_url: str | None, model: str) -> str | None:
        """解析 base_url，支持 provider 短名称"""
        if base_url:
            # 检查是否是预定义的 provider 名称
            lower = base_url.lower().strip()
            if lower in PROVIDER_BASE_URLS:
                return PROVIDER_BASE_URLS[lower]
            return base_url

        # 根据 model 名称自动推断
        if model.startswith("deepseek"):
            return PROVIDER_BASE_URLS["deepseek"]
        elif model.startswith("moonshot") or model.startswith("kimi"):
            return PROVIDER_BASE_URLS["moonshot"]
        elif model.startswith("qwen"):
            return PROVIDER_BASE_URLS["dashscope"]

        return None  # 使用 OpenAI 默认

    def generate_outline(
        self,
        topic: str,
        slide_count: int = 10,
        language: str = "zh-CN",
        instructions: str = "",
    ) -> PresentationModel:
        """根据主题生成完整大纲

        Args:
            topic: 演示文稿主题
            slide_count: 幻灯片数量
            language: 语言
            instructions: 附加指令

        Returns:
            包含所有幻灯片标题和要点的 PresentationModel
        """
        user_prompt = OUTLINE_USER_TEMPLATE.format(
            slide_count=slide_count,
            topic=topic,
            instructions=f"\n附加要求：{instructions}" if instructions else "",
        )

        response = self._call_llm(OUTLINE_SYSTEM_PROMPT, user_prompt)
        return self._parse_outline_response(response, language)

    def expand_content(
        self,
        presentation: PresentationModel,
        slide_index: int | None = None,
    ) -> PresentationModel:
        """扩展现有大纲的详细内容

        Args:
            presentation: 现有演示文稿模型
            slide_index: 指定扩展某一页 (None 则扩展全部)
        """
        # 序列化现有内容
        outline_data = presentation.model_dump()
        if slide_index is not None:
            # 只序列化指定页
            outline_data["slides"] = [outline_data["slides"][slide_index]]

        outline_json = json.dumps(outline_data, ensure_ascii=False, indent=2)
        user_prompt = EXPAND_USER_TEMPLATE.format(outline_json=outline_json)

        response = self._call_llm(EXPAND_SYSTEM_PROMPT, user_prompt)
        return self._parse_outline_response(response, presentation.language)

    def refine_text(self, text: str, instruction: str = "精简为要点") -> str:
        """优化文本内容"""
        user_prompt = REFINE_USER_TEMPLATE.format(
            instruction=instruction,
            text=text,
        )
        return self._call_llm(REFINE_SYSTEM_PROMPT, user_prompt)

    def _call_llm(self, system_prompt: str, user_prompt: str) -> str:
        """调用 LLM API"""
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        return response.choices[0].message.content

    def _parse_outline_response(
        self, response_text: str, language: str
    ) -> PresentationModel:
        """解析 LLM 返回的 JSON 为 PresentationModel"""
        # 尝试从响应中提取 JSON
        json_str = self._extract_json(response_text)

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            # 如果解析失败，返回空的 PresentationModel
            return PresentationModel(
                title="生成失败",
                language=language,
            )

        return self._dict_to_presentation(data, language)

    @staticmethod
    def _extract_json(text: str) -> str:
        """从文本中提取 JSON 内容"""
        text = text.strip()

        # 尝试提取 ```json ... ``` 代码块
        if "```json" in text:
            start = text.index("```json") + 7
            end = text.find("```", start)
            if end > start:
                return text[start:end].strip()

        # 尝试提取 ``` ... ``` 代码块
        if "```" in text:
            start = text.index("```") + 3
            end = text.find("```", start)
            if end > start:
                return text[start:end].strip()

        # 尝试提取 { ... }
        if "{" in text and "}" in text:
            start = text.index("{")
            # 找到最后一个 }
            end = text.rindex("}") + 1
            return text[start:end]

        return text

    def _dict_to_presentation(self, data: dict, language: str) -> PresentationModel:
        """将字典转换为 PresentationModel"""
        slides = []
        for slide_data in data.get("slides", []):
            slide = self._dict_to_slide(slide_data)
            slides.append(slide)

        return PresentationModel(
            title=data.get("title", "Untitled"),
            author=data.get("author", ""),
            language=language,
            slides=slides,
        )

    def _dict_to_slide(self, data: dict) -> SlideModel:
        """将字典转换为 SlideModel"""
        slide_type_str = data.get("slide_type", "content")
        try:
            slide_type = SlideType(slide_type_str)
        except ValueError:
            slide_type = SlideType.CONTENT

        elements = []
        for elem_data in data.get("elements", []):
            element = self._dict_to_element(elem_data)
            if element:
                elements.append(element)

        return SlideModel(
            slide_type=slide_type,
            title=data.get("title", ""),
            subtitle=data.get("subtitle", ""),
            elements=elements,
            notes=data.get("notes", ""),
        )

    def _dict_to_element(self, data: dict):
        """将字典转换为元素模型"""
        elem_type = data.get("element_type", "text")

        if elem_type == "chart":
            try:
                chart_type = ChartType(data.get("chart_type", "bar"))
            except ValueError:
                chart_type = ChartType.BAR
            return ChartElement(
                chart_type=chart_type,
                title=data.get("title", ""),
                categories=data.get("categories", []),
                series=[
                    ChartSeries(
                        name=s.get("name", ""),
                        values=s.get("values", []),
                    )
                    for s in data.get("series", [])
                ],
                show_legend=data.get("show_legend", True),
                show_data_labels=data.get("show_data_labels", False),
            )

        elif elem_type == "table":
            return TableElement(
                headers=data.get("headers", []),
                rows=data.get("rows", []),
            )

        else:
            # text 元素
            style_data = data.get("style", {})
            style = TextStyle(
                bullet=style_data.get("bullet", False),
                bullet_level=style_data.get("bullet_level", 0),
            )
            return TextElement(
                text=data.get("text", ""),
                style=style,
            )
