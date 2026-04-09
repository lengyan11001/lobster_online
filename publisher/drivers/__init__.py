# 发布驱动从独立 skill 加载，便于单独维护与开关
from skills.douyin_publish import DouyinDriver
from skills.toutiao_publish import ToutiaoDriver
from skills.xiaohongshu_publish import XiaohongshuDriver

DRIVERS = {
    "douyin": DouyinDriver,
    "xiaohongshu": XiaohongshuDriver,
    "toutiao": ToutiaoDriver,
}
