"""通讯录「键盘走列表」快扫（2026-10-16）。

背景：原来的同步是"滚一屏 → 逐行点开资料卡读微信号"，点空了会读到上一个人的卡、
点偏了会点到粘性标题把分组收起、点完还要把列表恢复回去（线上那一堆
row_not_clickable / row_under_sticky_header / contacts_empty_round 都来自这里）。

新路径：只在第一行点一次建立锚点，之后只发 {DOWN}，资料卡跟着选中项刷新后再读。
这里测的是**不依赖 UIA 的纯逻辑**（模式开关、步进决策、名字对账），
真实键盘行为要在真机上验（LOBSTER_WECHAT_CONTACT_SCAN=click 可随时回退）。
"""
from __future__ import annotations

import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
ENGINE_PATH = ROOT / "backend" / "app" / "services" / "native_wechat_engine.py"


@pytest.fixture(scope="module")
def engine():
    import sys

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from backend.app.services import native_wechat_engine as module

    return module


def test_scan_mode_defaults_to_keyboard(engine, monkeypatch):
    monkeypatch.delenv("LOBSTER_WECHAT_CONTACT_SCAN", raising=False)
    assert engine._contact_scan_mode() == "keyboard", "默认走键盘快扫（可随时用环境变量回退）"


@pytest.mark.parametrize("raw", ["click", "legacy", "0", "off", "false", "CLICK"])
def test_scan_mode_env_falls_back_to_click(engine, monkeypatch, raw):
    monkeypatch.setenv("LOBSTER_WECHAT_CONTACT_SCAN", raw)
    assert engine._contact_scan_mode() == "click"


def test_scan_mode_ignores_unknown_values(engine, monkeypatch):
    monkeypatch.setenv("LOBSTER_WECHAT_CONTACT_SCAN", "whatever")
    assert engine._contact_scan_mode() == "keyboard"


def test_walk_decision_waits_on_empty_card(engine):
    assert engine._keyboard_walk_should_read(["张三"], []) == "wait"


def test_walk_decision_reads_when_card_changed(engine):
    assert engine._keyboard_walk_should_read(["张三", "广东 深圳"], ["李四", "广东 广州"]) == "read"


def test_walk_decision_stops_when_card_is_unchanged(engine):
    """卡片没换人就必须停：继续读会把上一个人的微信号写成本人的（线上就是这么错的）。"""
    same = ["张三", "微信号: zhangsan"]
    assert engine._keyboard_walk_should_read(same, list(same)) == "stop"
    assert engine._keyboard_walk_should_read(same, same) == "stop"


def test_match_card_texts_to_visible_name_exact(engine):
    rows = [{"name": "张三"}, {"name": "李四"}]
    assert engine._match_card_texts_to_visible_name(["李四", "微信号: lisi"], rows) == "李四"


def test_match_card_texts_to_visible_name_normalized(engine):
    """卡片上的名字常带空白；归一化只看"去掉空白后的名字"，返回值是库里那个（已 strip 的）名字。"""
    rows = [{"name": " 张 三 "}]
    assert engine._match_card_texts_to_visible_name(["张  三"], rows) == "张 三"
    assert engine._match_card_texts_to_visible_name([" 张三 "], rows) == "张 三"


def test_match_card_texts_returns_empty_when_not_found(engine):
    """对不上就返回空 —— 调用方据此跳过，绝不给不认识的人写号。"""
    rows = [{"name": "张三"}]
    assert engine._match_card_texts_to_visible_name(["王五", "微信号: wangwu"], rows) == ""
    assert engine._match_card_texts_to_visible_name([], rows) == ""
    assert engine._match_card_texts_to_visible_name(["张三"], []) == ""


def test_keyboard_scan_is_wired_into_contact_sync():
    source = ENGINE_PATH.read_text(encoding="utf-8")
    assert "def _contact_sync_keyboard_scan(" in source
    assert "_contact_scan_mode() == \"keyboard\"" in source, "同步入口要按模式分流"
    assert "_contact_sync_keyboard_scan(" in source.split("def _sync_local_contacts_from_uia(")[1], \
        "键盘路径必须挂在 _sync_local_contacts_from_uia 里"
    assert 'stage="contacts_keyboard_fallback"' in source, "键盘路径失败要能自动回落并留痕"
    assert '_key_down_fast("down")' in source, "步进必须走键盘（按住），不移动鼠标"
    assert "def _send_hotkey_fast(" in source
    # 每按一次 {DOWN} 都不能再走 human pause：_send_hotkey 结尾的 _human_pause 默认 0.35~1.2 秒
    fast_body = source.split("def _send_hotkey_fast(")[1].split("def _send_keys(")[0]
    assert "_human_pause(" not in fast_body, "无延迟按键里不能再睡"
    assert "def _contact_sync_card_probe" in source, "原有诊断路径保持不变"


def test_step_key_never_sleeps_like_a_human(engine):
    """每按一次 {DOWN} 都睡 0.35~1.2 秒是上一版的瓶颈（739 个好友 = 5~15 分钟）。"""
    source = ENGINE_PATH.read_text(encoding="utf-8")
    fast_body = source.split("def _send_hotkey_fast(")[1].split("def _send_keys(")[0]
    assert "time.sleep" not in fast_body and "_human_pause(" not in fast_body
    scan_body = source.split("def _contact_sync_keyboard_scan(")[1].split("def _sync_local_contacts_from_uia(")[0]
    assert "_key_down_fast(\"down\")" in scan_body
    assert "_human_pause(" not in scan_body, "键盘走列表里每一步都不该再睡人工延迟"


def test_card_read_escalates_the_budget_instead_of_reporting_empty(engine):
    """2026-10-16 实测：预算压到 8 层/500 节点时一个文本都读不到（texts=0），
    被当成"卡片没换人"→ 3 次就收工（同步只走 1 步、只认一屏 11 个）。
    所以每步读取必须**自适应放大预算**，并且"读不到"绝不能算"没换人"。"""
    source = ENGINE_PATH.read_text(encoding="utf-8")
    assert "def _quick_texts(wide: bool = False)" in source
    assert "max_depth=12, max_nodes=1200" in source
    assert "return _card_texts(root_now, max_depth=20, max_nodes=3000)" in source, "读不到就放大到满预算"
    assert "def _card_texts(card: Any, *, max_depth: int = 20, max_nodes: int = 3000)" in source, \
        "默认预算保持不变，避免影响其它调用方"
    assert '_CONTACT_KB_UNREADABLE_LIMIT' in source, "连续读不到要有上限，防死循环"
    assert 'stage="keyboard_card_unreadable"' in source


def test_click_mode_still_leaves_the_legacy_path_untouched():
    """回退模式必须还是原来那条路：逐行点击的读取器仍在、且没被改名/删除。"""
    source = ENGINE_PATH.read_text(encoding="utf-8")
    assert "def _read_contact_profile_wx_no_for_cell(" in source
    assert "wx_fill_click" in source
    assert "def _sync_local_contacts_from_uia(" in source


def test_contacts_group_is_expanded_by_verified_row_click_not_coordinates():
    """2026-10-16：展开「联系人」必须用带校验的分组行点击（鹰牛 Tools.collapse_contacts 的做法），
    不能用坐标去点粘性标题 —— 粘性标题在 UIA 里没节点，点偏一点就把档收起，
    线上表现就是"它自己不展开、要人手动展开"。"""
    source = ENGINE_PATH.read_text(encoding="utf-8")
    body = source.split("def _open_contacts_sublist(")[1].split("\ndef ")[0]
    assert "_contacts_click_group_row(hwnd, _prefix)" in body, "展开要走带校验的分组行点击"
    assert "_contacts_entry_ready(hwnd)" in body, "已经展开就直接返回，别乱点"
    assert '_send_hotkey_fast("home")' in body, "点开后要 {HOME} 回顶（鹰牛 switch_to_first_friend 同款）"


def test_scroll_to_head_prefers_the_home_key():
    """回顶优先 {HOME}，滚轮只做兜底：滚轮会让虚拟列表渲染出 0 行的空档，
    以前被误判成"分组被收起"，还白白滚 20 轮 × 0.35s。"""
    source = ENGINE_PATH.read_text(encoding="utf-8")
    body = source.split("def _contacts_scroll_to_head(")[1].split("def _open_contacts_sublist(")[0]
    home_at = body.find('_send_hotkey_fast("home")')
    wheel_at = body.find("WheelUp(")
    assert home_at >= 0, "要有 {HOME} 快路径"
    assert '"mode": "home_key"' in body
    assert wheel_at > home_at, "滚轮必须排在 {HOME} 后面（只做兜底）"


def test_key_sends_are_gated_on_wechat_being_foreground():
    """2026-10-16 实测事故：悬浮键弹出后抢走键盘焦点，{HOME}/{DOWN} 全打到它身上，
    同步只走 1 步、只认了一屏 11 个人。所以每次发键前都要确认前台是微信。"""
    source = ENGINE_PATH.read_text(encoding="utf-8")
    assert "def _ensure_wechat_foreground_for_keys(" in source
    scan = source.split("def _contact_sync_keyboard_scan(")[1].split("def _sync_local_contacts_from_uia(")[0]
    assert "_ensure_wechat_foreground_for_keys(hwnd, force=True)" in scan, "走进列表前先要回前台"
    # 每一按 {DOWN} 之前都要过前台闸门（现在推进段带"点下一行"兜底，缩进会变，
    # 所以按"闸门必须出现在发键之前"来判断，而不是钉死缩进）。
    guard_at = scan.index("_ensure_wechat_foreground_for_keys(hwnd)")
    send_at = scan.index('_key_down_fast("down")')
    assert guard_at < send_at, "每一按 {DOWN} 之前都要过前台闸门"
    head = source.split("def _contacts_scroll_to_head(")[1].split("def _open_contacts_sublist(")[0]
    assert "_ensure_wechat_foreground_for_keys(hwnd" in head, "回顶发 {HOME} 前也要确认前台"


def test_overlay_window_never_takes_focus():
    """悬浮窗必须 WS_EX_NOACTIVATE：否则它一弹出来就把键盘焦点从微信抢走。"""
    script = (ROOT / "scripts" / "task_overlay.py").read_text(encoding="utf-8")
    assert "WS_EX_NOACTIVATE" in script
    assert "SWP_NOACTIVATE" in script
    assert "_keep_non_activating(" in script, "样式要反复重打（Show() 会把它盖回去）"
    assert "hidden=True" in script, "先隐藏创建，再打样式，最后 show"
    assert "SetForegroundWindow(previous)" in script, "show 那一下的激活要当场还给上一个窗口"


def test_advance_uses_hold_down_like_they_do(engine):
    """照鹰牛 Contacts.get_friends_detail：pyautogui.keyDown('down') + sleep(interval) +
    每步 win32gui.GetForegroundWindow() 校验（不等于微信就把焦点要回来）。
    我们要的是同一效果 —— 不再有任何"点鼠标换行"的兜底。"""
    source = ENGINE_PATH.read_text(encoding="utf-8")
    assert "def _key_down_fast(" in source and "def _key_up_fast(" in source, "要有按住/松开"
    scan = source.split("def _contact_sync_keyboard_scan(")[1].split("def _sync_local_contacts_from_uia(")[0]
    assert '_key_down_fast("down")' in scan and '_key_up_fast("down")' in scan
    assert "time.sleep(step_interval)" in scan, "按住之后要按 interval 睡（他们就是这么控制速度）"
    assert "_click_next_row" not in scan, "不要回退到点鼠标"
    assert "_contacts_point_click" not in scan.split("def _quick_texts")[1] if "def _quick_texts" in scan else True


def test_step_interval_matches_their_tiers(engine):
    """他们的分档：>2000 → 0.2s；1000~2000 → 0.1s；<1000 → 0（他们不睡）。
    这就是"他们非常快"的一半原因 —— 好友少时几乎零等待。"""
    assert engine._contact_kb_interval(500) <= 0.02
    assert engine._contact_kb_interval(1500) == 0.1
    assert engine._contact_kb_interval(3000) == 0.2
    assert engine._contact_kb_interval(0) <= 0.02
