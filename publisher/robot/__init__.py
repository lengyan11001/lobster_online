"""Robot：surface 闸门 + 快照校验的操作层（Agent 的工具面）。

用法：
    python -m publisher.robot doctor     # 看当前界面识别结果 + 可用操作清单
"""
from . import douyin_ops, wechat_ops  # noqa: F401  注册
from .ops import OPS, Result, describe  # noqa: F401
from .surface import (  # noqa: F401
    DOUYIN_EDITOR,
    DOUYIN_MANAGE,
    DOUYIN_UPLOAD,
    WECHAT_CHAT,
    WECHAT_CONTACTS,
    WECHAT_FRIEND_REQUESTS,
    WECHAT_MOMENTS,
    Snapshot,
    Surface,
    detect_browser,
    detect_desktop,
    snapshot_browser,
    snapshot_desktop,
)

__all__ = ["OPS", "Result", "describe", "detect_desktop", "detect_browser",
           "snapshot_desktop", "snapshot_browser", "Snapshot", "Surface"]
