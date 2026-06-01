from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum

from .elements import ElementModel
from .layout import LayoutZone


class SlideType(str, Enum):
    """幻灯片布局类型"""

    TITLE = "title"
    SECTION = "section"
    CONTENT = "content"
    TWO_COLUMN = "two_column"
    IMAGE_TEXT = "image_text"
    CHART = "chart"
    TABLE = "table"
    QUOTE = "quote"
    BLANK = "blank"
    ENDING = "ending"


class SlideModel(BaseModel):
    """单页幻灯片数据模型"""

    slide_type: SlideType = SlideType.CONTENT
    title: str = ""
    subtitle: str = ""
    elements: list[ElementModel] = Field(default_factory=list)
    notes: str = ""
    layout_override: Optional[dict] = None
