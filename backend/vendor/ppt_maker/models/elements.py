from pydantic import BaseModel, Field
from typing import Optional, Union
from enum import Enum

from .layout import LayoutZone
from .style import TextStyle, TextRun


# --- 文本元素 ---


class TextElement(BaseModel):
    """文本内容元素"""

    element_type: str = "text"
    position: Optional[LayoutZone] = None
    style: TextStyle = Field(default_factory=TextStyle)
    runs: list[TextRun] = Field(default_factory=list)
    text: str = ""


# --- 图片元素 ---


class ImageElement(BaseModel):
    """图片内容元素"""

    element_type: str = "image"
    position: Optional[LayoutZone] = None
    source: str = ""
    alt_text: str = ""
    scaling: str = "fit"


# --- 图表元素 ---


class ChartType(str, Enum):
    BAR = "bar"
    BAR_STACKED = "bar_stacked"
    LINE = "line"
    PIE = "pie"
    AREA = "area"
    SCATTER = "scatter"


class ChartSeries(BaseModel):
    """图表数据系列"""

    name: str = ""
    values: list[float] = Field(default_factory=list)


class ChartElement(BaseModel):
    """图表内容元素"""

    element_type: str = "chart"
    position: Optional[LayoutZone] = None
    chart_type: ChartType = ChartType.BAR
    title: str = ""
    categories: list[str] = Field(default_factory=list)
    series: list[ChartSeries] = Field(default_factory=list)
    show_legend: bool = True
    show_data_labels: bool = False


# --- 表格元素 ---


class TableElement(BaseModel):
    """表格内容元素"""

    element_type: str = "table"
    position: Optional[LayoutZone] = None
    headers: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)
    header_style: Optional[TextStyle] = None
    cell_style: Optional[TextStyle] = None
    first_column_highlight: bool = True


# --- 联合类型 ---
ElementModel = Union[TextElement, ImageElement, ChartElement, TableElement]
