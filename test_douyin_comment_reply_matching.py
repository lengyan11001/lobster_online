"""评论回复的目标匹配护栏（2026-09-21 真机探测后收紧）。

真机证据（本机 CDP 9332，视频 7613050528716869474，首屏 10 条顶层评论 + 5 条嵌套回复）：
- 顶层评论目标：改动前后都 10/10 命中且同作者同正文（无回归）。
- 嵌套回复目标：改动前 3/3 被"命中"到父评论（同作者不同正文 = 错楼），改动后 3/3 判未找到（宁缺勿错楼）。

这些护栏写在 page.evaluate 的 JS 里，所以这里对 JS 文本做一次结构断言，防止以后改回去。
"""

import ast
import re

import backend.app.api.h5_chat_channel as h5_chat_channel

h5_chat_channel._install_douyin_origin_import_path()
import douyin_comment_scraper as scraper_module  # type: ignore  # noqa: E402


def _match_js() -> str:
    source = open(scraper_module.__file__, encoding="utf-8", errors="replace").read()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "_find_and_click_comment_reply_button":
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call) and getattr(sub.func, "attr", "") == "evaluate" and sub.args:
                    arg = sub.args[0]
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and "comment-item" in arg.value:
                        return arg.value
    raise AssertionError("找不到评论匹配 JS")


def test_body_text_excludes_nested_replies():
    js = _match_js()

    assert "readBodyText" in js, "必须只取主评论正文"
    assert "nextElementSibling" in js, "正文取 info-wrap 的下一个兄弟"
    assert re.search(r"closest\(['\"]\.replyContainer['\"]\)", js), "正文不能包含嵌套回复子树"


def test_username_badges_are_stripped():
    js = _match_js()

    assert "cleanUsername" in js
    assert "作者" in js and "人赞了" in js, "抖音会把「作者」「等N人赞了你」拼进作者名"


def test_selection_requires_reliable_evidence():
    js = _match_js()

    # 入选三选一：主页精确命中 / 作者名+正文同时命中 / 正文唯一完全命中
    assert "profileHit" in js and "nameHit" in js and "exactContentHit" in js
    assert "exactContentCount === 1" in js, "正文完全命中只在全页唯一时才接受"
    assert "prefixContentHit" in js, "前缀命中只用于排序"
    assert ">= 8" not in js, "旧的分数门槛（>=8）会把前缀命中当命中，已废弃"


def test_miss_reports_candidates_for_diagnosis():
    js = _match_js()

    assert "debug_candidates" in js
    source = open(scraper_module.__file__, encoding="utf-8", errors="replace").read()
    assert "首轮未命中目标评论，候选 top3" in source, "Python 侧要把候选打进日志"
