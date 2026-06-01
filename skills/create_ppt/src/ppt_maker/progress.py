"""PPT 生成进度显示工具

基于 rich.progress，为 PPT 生成提供统一的进度条。
"""

from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn, MofNCompleteColumn


def make_slide_progress() -> Progress:
    """创建幻灯片生成进度条

    Usage:
        with make_slide_progress() as progress:
            task = progress.add_task("生成PPT", total=24)
            # ... 每完成一页 ...
            progress.advance(task)
    """
    return Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
    )


def make_image_progress() -> Progress:
    """创建 AI 图片生成进度条

    Usage:
        with make_image_progress() as progress:
            img_task = progress.add_task("生成背景图", total=24)
            # ... 每完成一张图 ...
            progress.advance(img_task)
    """
    return Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
    )
