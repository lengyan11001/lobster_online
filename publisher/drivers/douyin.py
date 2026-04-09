"""抖音发布驱动：与 skills.douyin_publish 同源，避免 publisher 与 skill 两套实现分叉。"""
from skills.douyin_publish.driver import DouyinDriver, UPLOAD_URL

__all__ = ["DouyinDriver", "UPLOAD_URL"]
