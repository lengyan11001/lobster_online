"""各 SlideType 的布局区域预设

所有坐标为 0.0~1.0 的比例值，渲染时乘以实际尺寸 (EMU)。
"""

from ppt_maker.models import SlideType, LayoutZone, SlideLayout


LAYOUT_PRESETS: dict[SlideType, SlideLayout] = {
    SlideType.TITLE: SlideLayout(
        slide_type="title",
        title_zone=LayoutZone(x=0.10, y=0.30, width=0.80, height=0.20),
        content_zone=LayoutZone(x=0.10, y=0.55, width=0.80, height=0.15),
    ),
    SlideType.SECTION: SlideLayout(
        slide_type="section",
        title_zone=LayoutZone(x=0.15, y=0.35, width=0.70, height=0.25),
    ),
    SlideType.CONTENT: SlideLayout(
        slide_type="content",
        title_zone=LayoutZone(x=0.06, y=0.06, width=0.88, height=0.10),
        content_zone=LayoutZone(x=0.06, y=0.18, width=0.88, height=0.74),
    ),
    SlideType.TWO_COLUMN: SlideLayout(
        slide_type="two_column",
        title_zone=LayoutZone(x=0.06, y=0.06, width=0.88, height=0.10),
        content_zone=LayoutZone(x=0.06, y=0.18, width=0.42, height=0.74),
        secondary_zone=LayoutZone(x=0.52, y=0.18, width=0.42, height=0.74),
    ),
    SlideType.IMAGE_TEXT: SlideLayout(
        slide_type="image_text",
        title_zone=LayoutZone(x=0.06, y=0.06, width=0.88, height=0.10),
        content_zone=LayoutZone(x=0.06, y=0.18, width=0.42, height=0.74),
        secondary_zone=LayoutZone(x=0.52, y=0.18, width=0.42, height=0.74),
    ),
    SlideType.CHART: SlideLayout(
        slide_type="chart",
        title_zone=LayoutZone(x=0.06, y=0.06, width=0.88, height=0.10),
        content_zone=LayoutZone(x=0.06, y=0.18, width=0.88, height=0.74),
    ),
    SlideType.TABLE: SlideLayout(
        slide_type="table",
        title_zone=LayoutZone(x=0.06, y=0.06, width=0.88, height=0.10),
        content_zone=LayoutZone(x=0.06, y=0.18, width=0.88, height=0.74),
    ),
    SlideType.QUOTE: SlideLayout(
        slide_type="quote",
        content_zone=LayoutZone(x=0.15, y=0.25, width=0.70, height=0.50),
    ),
    SlideType.BLANK: SlideLayout(
        slide_type="blank",
    ),
    SlideType.ENDING: SlideLayout(
        slide_type="ending",
        title_zone=LayoutZone(x=0.10, y=0.35, width=0.80, height=0.20),
        content_zone=LayoutZone(x=0.10, y=0.60, width=0.80, height=0.15),
    ),
}
