from pydantic import BaseModel, Field
from typing import Optional

from .slide import SlideModel


class PresentationModel(BaseModel):
    """演示文稿完整数据模型"""

    title: str = "Untitled Presentation"
    author: str = ""
    subject: str = ""
    language: str = "zh-CN"
    slide_size: str = "16:9"
    theme_name: str = "default"
    template_path: Optional[str] = None
    slides: list[SlideModel] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
