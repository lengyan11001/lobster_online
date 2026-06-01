from pydantic import BaseModel


class LayoutZone(BaseModel):
    """布局区域定义 (比例坐标 0.0~1.0)"""

    x: float
    y: float
    width: float
    height: float


class SlideLayout(BaseModel):
    """单页布局定义"""

    slide_type: str
    title_zone: LayoutZone | None = None
    content_zone: LayoutZone | None = None
    secondary_zone: LayoutZone | None = None
    notes_zone: LayoutZone | None = None
