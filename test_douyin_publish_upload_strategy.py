from __future__ import annotations

import inspect

from skills.douyin_publish.driver import DouyinDriver


def test_video_upload_prefers_video_input_over_file_chooser():
    src = inspect.getsource(DouyinDriver._publish_video)
    assert src.index("_douyin_find_video_file_input") < src.index("expect_file_chooser")
    assert "page.set_input_files('input[type=\"file\"]'" not in src
