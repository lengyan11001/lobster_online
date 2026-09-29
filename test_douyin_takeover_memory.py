"""抖音私信循环接管（AI 记忆接管）：只按记忆文件 + 会话上下文回复，不拉群、不引导加微信。"""
import asyncio
import sys
from pathlib import Path


DOUYIN_ORIGIN_ROOT = Path(__file__).resolve().parent / "backend" / "douyin_origin"
sys.path.insert(0, str(DOUYIN_ORIGIN_ROOT))

import douyin_api  # noqa: E402
from douyin_api import (  # noqa: E402
    generate_douyin_stranger_reply_message,
    normalize_douyin_stranger_message_monitor_state,
    normalize_douyin_stranger_reply_mode,
    douyin_stranger_reply_mode_label,
)


MEMORY_TEXT = "## 百问百答\n问：多少钱？\n答：基础版 999 元，包含拍摄和剪辑。"


def _account():
    return {"id": 5, "status": "online", "port": 9336}


def _row(**extra):
    row = {
        "account_id": 5,
        "conversation_key": "conv-takeover-1",
        "username": "小亮",
        "incoming_message": "你们这个多少钱",
        "unread_count": 1,
        "is_unread": True,
        "time_text": "刚刚",
    }
    row.update(extra)
    return row


def test_ai_memory_mode_is_recognized():
    assert normalize_douyin_stranger_reply_mode("ai_memory") == "ai_memory"
    assert douyin_stranger_reply_mode_label("ai_memory") == "AI 记忆接管"


def test_monitor_state_keeps_selected_memory_doc():
    state = normalize_douyin_stranger_message_monitor_state(
        {
            "reply_mode": "ai_memory",
            "memory_doc_ids": ["faq", "faq", "  额外  ", ""],
            "memory_user_id": 1,
        },
        account_id=5,
    )

    assert state["reply_mode"] == "ai_memory"
    assert state["memory_doc_ids"] == ["faq", "额外"]
    assert state["memory_user_id"] == 1


def test_takeover_reply_uses_memory_and_conversation_context(monkeypatch):
    captured = {}

    def fake_ai(system_prompt, user_prompt, **kwargs):
        captured["system_prompt"] = system_prompt
        captured["user_prompt"] = user_prompt
        return "我们在做这个\n加个微信详聊"

    monkeypatch.setattr(douyin_api, "request_douyin_ai_comment", fake_ai)

    result = generate_douyin_stranger_reply_message(
        _row(
            messages=[
                {"text": "在吗", "is_incoming": True},
                {"text": "在的", "is_incoming": False},
                {"text": "你们这个多少钱", "is_incoming": True},
            ]
        ),
        mode="ai_memory",
        prompt_text="回答价格问题，别报价太硬",
        memory_context=MEMORY_TEXT,
    )

    # 命中记忆资料的内容可以回，引导加微信那条必须被丢掉。
    assert result.splitlines() == ["我们在做这个"]
    assert MEMORY_TEXT in captured["user_prompt"]
    assert "对方：你们这个多少钱" in captured["user_prompt"]
    assert "我：在的" in captured["user_prompt"]
    assert "回答价格问题，别报价太硬" in captured["user_prompt"]
    # 提示词里只能是"不许引导加微信"，不能出现旧的"强制补上绿泡泡"逻辑。
    assert "麻烦您绿泡泡" not in captured["system_prompt"]
    assert "不要主动提微信" in captured["system_prompt"]
    assert "不要引导对方换平台沟通" in captured["system_prompt"]


def test_takeover_reply_requires_memory_content():
    try:
        generate_douyin_stranger_reply_message(_row(), mode="ai_memory", memory_context="")
    except RuntimeError as exc:
        assert "记忆文件" in str(exc)
    else:
        raise AssertionError("没有记忆内容时不应该生成接管回复")


def _install_takeover_harness(monkeypatch, rows, *, ai_reply="基础版 999 元，含拍摄和剪辑"):
    """装一套接管循环用的假环境，返回收集到的调用记录。"""
    account = _account()
    record = {"sent": [], "collect_kwargs": [], "seen": [], "ai_calls": 0}
    stored_rows = []

    def fake_ai(system_prompt, user_prompt, **kwargs):
        record["ai_calls"] += 1
        if isinstance(ai_reply, Exception):
            raise ai_reply
        return ai_reply

    class FakeScraper:
        async def collect_stranger_private_messages(self, **kwargs):
            record["collect_kwargs"].append(kwargs)
            results = []
            for raw in rows:
                if kwargs.get("should_read_detail") and not kwargs["should_read_detail"](raw):
                    continue
                merged = dict(raw)
                if kwargs.get("include_details"):
                    merged.update(
                        {
                            "detail_read_status": "ok",
                            "messages": [{"text": raw.get("incoming_message", ""), "is_incoming": True}],
                            "last_message_is_user": True,
                            "has_user_message": True,
                        }
                    )
                if kwargs.get("item_callback"):
                    result = await kwargs["item_callback"](merged, object())
                    if isinstance(result, dict):
                        merged.update(result)
                results.append(merged)
            return results

        async def send_open_chat_message(self, page, message, **kwargs):
            record["sent"].append({"username": kwargs.get("username"), "message": message})
            return {"success": True, "messages": [message], "message_count": 1}

        async def close(self):
            return None

    def store(account_id, incoming):
        by_key = {
            douyin_api.stranger_message_row_key(item): dict(item)
            for item in stored_rows
            if douyin_api.stranger_message_row_key(item)
        }
        for item in incoming:
            normalized = douyin_api.normalize_douyin_stranger_message_row({**item, "account_id": account_id})
            by_key[douyin_api.stranger_message_row_key(normalized)] = normalized
        stored_rows[:] = list(by_key.values())
        return len(stored_rows)

    def mark_seen(account_id, incoming):
        record["seen"].extend(
            douyin_api.stranger_message_row_key(item) for item in (incoming or [])
        )
        return len(incoming or [])

    monkeypatch.setattr(douyin_api, "load_global_config", lambda: {"douyin_accounts": [account]})
    monkeypatch.setattr(douyin_api, "get_douyin_account_by_id", lambda account_id, config: account)
    monkeypatch.setattr(douyin_api, "is_douyin_stranger_message_monitor_busy", lambda account_id: (False, ""))
    monkeypatch.setattr(douyin_api, "create_douyin_message_scraper", lambda account, config: FakeScraper())
    monkeypatch.setattr(douyin_api, "collect_douyin_stranger_message_results", lambda account_id=0: list(stored_rows))
    monkeypatch.setattr(douyin_api, "merge_douyin_stranger_message_results", store)
    monkeypatch.setattr(douyin_api, "mark_douyin_stranger_message_rows_seen", mark_seen)
    monkeypatch.setattr(douyin_api, "save_douyin_stranger_message_results", lambda: None)
    monkeypatch.setattr(douyin_api, "save_douyin_stranger_message_seen_records", lambda: None)
    monkeypatch.setattr(douyin_api, "save_douyin_stranger_message_takeover_attempts", lambda: None)
    monkeypatch.setattr(douyin_api, "save_douyin_stranger_message_monitor_config", lambda: None)
    monkeypatch.setattr(douyin_api, "schedule_next_douyin_stranger_message_monitor_run", lambda *a, **k: "")
    monkeypatch.setattr(
        douyin_api,
        "load_douyin_takeover_memory_context",
        lambda doc_ids, **kwargs: {
            "text": MEMORY_TEXT,
            "document_count": 1,
            "titles": ["百问百答 FAQ"],
            "user_id": 1,
        },
    )
    monkeypatch.setattr(douyin_api, "request_douyin_ai_comment", fake_ai)
    monkeypatch.setattr(douyin_api, "douyin_stranger_message_seen_records", {})
    monkeypatch.setattr(douyin_api, "douyin_stranger_message_takeover_attempts", {})

    state = normalize_douyin_stranger_message_monitor_state(
        {
            "enabled": True,
            "explicitly_started": True,
            "reply_mode": "ai_memory",
            "auto_reply_enabled": True,
            "memory_doc_ids": ["faq"],
            "memory_user_id": 1,
        },
        account_id=5,
    )
    monkeypatch.setitem(douyin_api.douyin_stranger_message_monitor_states, "5", state)
    return record, state, stored_rows


def test_monitor_cycle_replies_inside_opened_conversation(monkeypatch):
    record, state, stored_rows = _install_takeover_harness(monkeypatch, [_row()])

    async def forbidden(*args, **kwargs):
        raise AssertionError("记忆接管不能走固定的私信群发通道")

    monkeypatch.setattr(douyin_api, "send_douyin_stranger_messages_for_monitor", forbidden)

    result = asyncio.run(douyin_api.run_douyin_stranger_message_monitor_cycle(5))

    assert result["status"] == "completed"
    assert record["collect_kwargs"][0]["include_details"] is True
    assert "item_callback" in record["collect_kwargs"][0]
    assert [item["message"] for item in record["sent"]] == ["基础版 999 元，含拍摄和剪辑"]
    assert "绿泡泡" not in record["sent"][0]["message"]
    key = douyin_api.stranger_message_row_key(douyin_api.normalize_douyin_stranger_message_row(_row()))
    assert record["seen"] == [key]
    assert stored_rows[0]["reply_status"] == "sent"
    assert "百问百答 FAQ" in state["message"]


def test_monitor_cycle_skips_rows_whose_last_message_is_ours(monkeypatch):
    rows = [_row(last_message_is_user=None)]
    record, _state, _stored = _install_takeover_harness(monkeypatch, rows)

    class OwnLastScraper:
        async def collect_stranger_private_messages(self, **kwargs):
            merged = dict(rows[0])
            merged.update(
                {
                    "detail_read_status": "ok",
                    "messages": [{"text": "资料发你了", "is_incoming": False}],
                    "last_message_is_user": False,
                    "has_user_message": True,
                }
            )
            if kwargs.get("item_callback"):
                result = await kwargs["item_callback"](merged, object())
                if isinstance(result, dict):
                    merged.update(result)
            return [merged]

        async def send_open_chat_message(self, page, message, **kwargs):
            record["sent"].append({"username": kwargs.get("username"), "message": message})
            return {"success": True}

        async def close(self):
            return None

    monkeypatch.setattr(douyin_api, "create_douyin_message_scraper", lambda account, config: OwnLastScraper())

    result = asyncio.run(douyin_api.run_douyin_stranger_message_monitor_cycle(5))

    assert result["status"] == "completed"
    assert record["sent"] == []
    assert record["ai_calls"] == 0
    assert len(record["seen"]) == 1


def test_failed_generation_is_retried_until_success(monkeypatch):
    record, _state, _stored = _install_takeover_harness(
        monkeypatch,
        [_row()],
        ai_reply=RuntimeError("AI 接口调用失败：HTTP 502"),
    )

    first = asyncio.run(douyin_api.run_douyin_stranger_message_monitor_cycle(5))

    assert first["status"] == "completed"
    assert record["sent"] == []
    # 生成失败不记去重指纹，下一轮还会重试。
    assert record["seen"] == []
    assert douyin_api.douyin_stranger_message_takeover_attempts["5"]

    monkeypatch.setattr(
        douyin_api,
        "request_douyin_ai_comment",
        lambda system_prompt, user_prompt, **kwargs: "基础版 999 元，含拍摄和剪辑",
    )
    second = asyncio.run(douyin_api.run_douyin_stranger_message_monitor_cycle(5))

    assert second["status"] == "completed"
    assert [item["message"] for item in record["sent"]] == ["基础版 999 元，含拍摄和剪辑"]
    assert len(record["seen"]) == 1
    assert not douyin_api.douyin_stranger_message_takeover_attempts.get("5")


def test_monitor_start_requires_memory_doc(monkeypatch):
    account = _account()
    monkeypatch.setattr(douyin_api, "load_global_config", lambda: {"douyin_accounts": [account]})
    monkeypatch.setattr(douyin_api, "get_douyin_account_by_id", lambda account_id, config: account)
    monkeypatch.setattr(douyin_api, "save_douyin_stranger_message_monitor_config", lambda: None)
    monkeypatch.setattr(douyin_api, "douyin_ai_available", lambda config=None, request=None: True)

    async def ensure_scheduler():
        return None

    monkeypatch.setattr(douyin_api, "ensure_douyin_stranger_message_monitor_scheduler", ensure_scheduler)
    try:
        missing = asyncio.run(
            douyin_api.douyin_start_stranger_message_monitor(
                request={"account_id": 5, "reply_mode": "ai_memory", "auto_reply_enabled": True}
            )
        )
        assert missing["code"] == 400
        assert "记忆文件" in missing["msg"]

        monkeypatch.setattr(
            douyin_api,
            "load_douyin_takeover_memory_context",
            lambda doc_ids, **kwargs: {
                "text": MEMORY_TEXT,
                "document_count": 1,
                "titles": ["百问百答"],
                "user_id": 1,
            },
        )
        ok = asyncio.run(
            douyin_api.douyin_start_stranger_message_monitor(
                request={
                    "account_id": 5,
                    "reply_mode": "ai_memory",
                    "auto_reply_enabled": True,
                    "memory_doc_ids": ["faq"],
                }
            )
        )
    finally:
        douyin_api.douyin_stranger_message_monitor_states.pop("5", None)

    assert ok["code"] == 200
    assert ok["monitor"]["reply_mode"] == "ai_memory"
    assert ok["monitor"]["memory_doc_ids"] == ["faq"]
    assert "记忆文件" in ok["msg"]


def test_light_page_offers_memory_takeover_option():
    root = Path(__file__).resolve().parent
    html = (root / "static" / "douyin-origin" / "douyin-stranger-leads.html").read_text(encoding="utf-8")
    script = (root / "static" / "douyin-origin" / "douyin-workbench-shared.js").read_text(encoding="utf-8")

    assert '<option value="ai_memory">AI 记忆接管（循环按记忆文件回复）</option>' in html
    assert 'id="stranger-message-memory-doc"' in html
    assert "loadDouyinStrangerMemoryDocs" in html
    assert '"/api/openclaw/memory/list"' in script
    assert '"ai_memory"' in script
    assert "memory_doc_ids:memoryDocId?[memoryDocId]:[]" in script
    assert "isDouyinStrangerReplyReady()" in script
    # 选中记忆文件就直接切到记忆接管，用户只需要选一个记忆文件。
    assert "onDouyinStrangerMemoryDocChange" in html
    assert 'el.value="ai_memory"' in script


def _install_h5_task_harness(monkeypatch, rows, *, ai_reply="基础版 999 元，含拍摄和剪辑"):
    """H5 一次性节点（工作流节点）用的假环境。"""
    account = {"id": 5, "status": "online", "port": 9336}
    record = {"sent": [], "ai_prompts": []}

    def fake_ai(system_prompt, user_prompt, **kwargs):
        record["ai_prompts"].append(user_prompt)
        if isinstance(ai_reply, Exception):
            raise ai_reply
        return ai_reply

    class FakeScraper:
        async def collect_chat_page_private_messages(self, **kwargs):
            return []

        async def collect_stranger_private_messages(self, **kwargs):
            results = []
            for raw in rows:
                merged = dict(raw)
                if kwargs.get("item_callback"):
                    result = await kwargs["item_callback"](merged, object())
                    if isinstance(result, dict):
                        merged.update(result)
                results.append(merged)
            return results

        async def send_open_chat_message(self, page, message, **kwargs):
            record["sent"].append(message)
            return {"success": True, "messages": [message], "message_count": 1}

        async def close(self):
            return None

    monkeypatch.setattr(douyin_api, "load_global_config", lambda: {"douyin_accounts": [account]})
    monkeypatch.setattr(douyin_api, "get_douyin_account_by_id", lambda account_id, config: account)
    monkeypatch.setattr(douyin_api, "is_douyin_stranger_message_monitor_busy", lambda account_id: (False, ""))
    monkeypatch.setattr(douyin_api, "create_douyin_message_scraper", lambda account, config: FakeScraper())
    monkeypatch.setattr(douyin_api, "collect_douyin_stranger_message_results", lambda account_id=0: [])
    monkeypatch.setattr(douyin_api, "merge_douyin_stranger_message_results", lambda account_id, incoming: len(incoming or []))
    monkeypatch.setattr(douyin_api, "merge_douyin_inbox_results", lambda account_id, incoming: len(incoming or []))
    monkeypatch.setattr(douyin_api, "save_douyin_stranger_message_results", lambda: None)
    monkeypatch.setattr(douyin_api, "request_douyin_ai_comment", fake_ai)
    monkeypatch.setattr(
        douyin_api,
        "load_douyin_takeover_memory_context",
        lambda doc_ids, **kwargs: {
            "text": MEMORY_TEXT,
            "document_count": 1,
            "titles": ["百问百答"],
            "user_id": 1,
        },
    )
    return record


def _h5_task_row():
    return {
        "conversation_key": "h5-takeover-1",
        "username": "小亮",
        "incoming_message": "你们这个多少钱",
        "preview_text": "你们这个多少钱",
        "last_message_text": "你们这个多少钱",
        "last_message_is_user": True,
        "has_user_message": True,
        "detail_read_status": "ok",
        "messages": [{"text": "你们这个多少钱", "is_incoming": True}],
    }


def test_h5_node_memory_mode_replies_from_memory_file(monkeypatch):
    record = _install_h5_task_harness(monkeypatch, [_h5_task_row()])

    result = asyncio.run(
        douyin_api.run_douyin_h5_stranger_message_task_once(
            account_id=5,
            reply_mode="ai_memory",
            memory_context=MEMORY_TEXT,
        )
    )

    assert result["status"] == "completed"
    assert result["reply_mode"] == "ai_memory"
    assert record["sent"] == ["基础版 999 元，含拍摄和剪辑"]
    assert "绿泡泡" not in record["sent"][0]
    assert MEMORY_TEXT in record["ai_prompts"][0]
    assert "对方：你们这个多少钱" in record["ai_prompts"][0]
    assert result["reply"]["success"] == 1


def test_h5_node_memory_mode_resolves_doc_ids_locally(monkeypatch):
    record = _install_h5_task_harness(monkeypatch, [_h5_task_row()])
    result = asyncio.run(
        douyin_api.run_douyin_h5_stranger_message_task_once(
            account_id=5,
            reply_mode="ai_memory",
            memory_doc_ids=["faq"],
        )
    )

    assert result["status"] == "completed"
    assert result["memory_titles"] == ["百问百答"]
    assert record["sent"] == ["基础版 999 元，含拍摄和剪辑"]


def test_h5_node_memory_mode_requires_memory_file(monkeypatch):
    record = _install_h5_task_harness(monkeypatch, [_h5_task_row()])
    monkeypatch.setattr(
        douyin_api,
        "load_douyin_takeover_memory_context",
        lambda doc_ids, **kwargs: {"text": "", "document_count": 0, "titles": [], "user_id": None},
    )

    result = asyncio.run(
        douyin_api.run_douyin_h5_stranger_message_task_once(account_id=5, reply_mode="ai_memory")
    )

    assert result["status"] == "failed"
    assert result["code"] == 400
    assert "记忆文件" in result["message"]
    assert record["sent"] == []


def test_h5_channel_passes_memory_takeover_mode():
    root = Path(__file__).resolve().parent
    source = (root / "backend" / "app" / "api" / "h5_chat_channel.py").read_text(encoding="utf-8")

    assert 'if douyin_reply_mode not in {"fixed", "ai_lead", "ai_memory"}:' in source
    assert "memory_context=_douyin_takeover_memory_text(source)" in source
    assert "memory_doc_ids=_douyin_takeover_memory_doc_ids(source)" in source


def test_online_employee_node_picker_offers_memory_takeover():
    """客户端 Online「我的AI员工 → 添加节点」里必须能看到「抖音私信记忆接管」。"""
    root = Path(__file__).resolve().parent
    js = (root / "static" / "js" / "views" / "h5-employees.js").read_text(encoding="utf-8")
    html = (root / "static" / "views" / "h5-employees.html").read_text(encoding="utf-8")

    assert "'抖音私信记忆接管'" in js
    # 同 action 的节点靠 @memory 身份区分，否则会被去重掉（添加时就看不到）。
    assert "? '@memory' : ''" in js
    assert "memory_takeover:true" in js
    assert "reply_mode:'ai_memory'" in js
    assert "wantsMemoryTakeover" in js
    assert '<option value="ai_memory">AI 记忆接管（按记忆文件回复）</option>' in html


def test_online_memory_takeover_node_only_asks_for_memory_file():
    """Online 节点不再有记忆文件选择（统一到「抖音获客 → 私信引流」里选），也不能冒出采集参数。"""
    root = Path(__file__).resolve().parent
    js = (root / "static" / "js" / "views" / "h5-employees.js").read_text(encoding="utf-8")
    html = (root / "static" / "views" / "h5-employees.html").read_text(encoding="utf-8")

    # 节点上不再有记忆文件选择
    assert "oeNodeDouyinMemoryField" not in html
    assert "oeNodeDouyinMemoryDoc" not in js
    assert "fillDouyinMemoryDocSelect" not in js
    # 节点自己带 ai_memory / memory_takeover 时必须按私信接管表单渲染
    assert "var memoryTakeover=" in js
    assert "el('oeNodeDouyinCollectionField').hidden=!douyinCollection" in js
    assert "el('oeNodeWechatAddFriendField').hidden=!douyinPrivate || memoryTakeover" in js
    # 保存节点时不要写、也不留节点级记忆文件参数
    assert "delete row.params.memory_doc_ids;" in js


def test_online_node_save_is_robust_when_dropdown_resets():
    """Online 保存节点：下拉被重置时按标题回查节点，动作优先取节点自带 sales_action。"""
    root = Path(__file__).resolve().parent
    js = (root / "static" / "js" / "views" / "h5-employees.js").read_text(encoding="utf-8")

    assert "var optionSalesAction=option[5] && typeof option[5]==='object' ? String(option[5].sales_action||'').trim().toLowerCase() : ''" in js
    assert "selectedSalesAction=key === 'douyin_leads' ? (optionSalesAction || salesAction(option[2] || option[1])) : ''" in js
    assert "if (typedLabel && option[1] !== typedLabel)" in js
    assert "item[1]===typedLabel || (!!typedNote && item[2]===typedNote)" in js


def test_local_douyin_lock_and_idle_waits_are_bounded():
    """本地抖音"等空闲/拿执行锁"必须有上限：否则被取消的任务会把机器卡死（演示点不动）。"""
    root = Path(__file__).resolve().parent
    src = (root / "backend" / "app" / "api" / "h5_chat_channel.py").read_text(encoding="utf-8")

    assert "_SCHEDULED_DOUYIN_IDLE_WAIT_SECONDS = 300.0" in src
    assert "_SCHEDULED_DOUYIN_LOCK_WAIT_SECONDS = 120.0" in src
    assert "local Douyin worker still busy after %ss" in src
    assert "local Douyin execution lock busy > %ss" in src
    assert "asyncio.wait_for(" in src


def test_douyin_takeover_runs_multi_round_loop_until_node_window_ends():
    """抖音私信接管对齐个微：多轮循环（默认 15s 间隔）跑到节点时间窗结束，并汇报。"""
    root = Path(__file__).resolve().parent
    src = (root / "backend" / "app" / "api" / "h5_chat_channel.py").read_text(encoding="utf-8")
    start = src.index('if action == "stranger_message":')
    body = src[start : start + 9000]

    assert 'or 15, 300))' in body, "默认 15 秒一轮"
    assert "deadline_monotonic = _takeover_monotonic() + float(session_minutes * 60)" in body
    assert "await asyncio.sleep(interval_seconds)" in body, "轮与轮之间要休眠"
    assert "while True:" in body, "必须是循环而不是单轮"
    assert 'normalized["takeover_rounds"] = rounds' in body
    assert "抖音私信记忆接管正常收工" in body, "结束要有收工汇报"
    assert 'session_window_elapsed' in body


def test_memory_file_is_selected_in_douyin_leads_page():
    """记忆文件唯一入口：Online「抖音获客 → 私信引流」页里的记忆文件下拉。"""
    root = Path(__file__).resolve().parent
    html = (root / "static" / "douyin-origin" / "douyin-stranger-leads.html").read_text(encoding="utf-8")
    js = (root / "static" / "douyin-origin" / "douyin-workbench-shared.js").read_text(encoding="utf-8")

    assert 'id="stranger-message-memory-doc"' in html
    assert "onDouyinStrangerMemoryDocChange" in html
    assert "loadDouyinStrangerMemoryDocs" in js
    assert "memory_doc_ids:memoryDocId?[memoryDocId]:[]" in js
    # 选择结果要随私信接管配置存到本机（monitor/config）
    assert '"/api/douyin/stranger-messages/monitor/config"' in js


def test_memory_doc_choice_is_echoed_after_page_switch():
    """切换界面/刷新后要回显上次选的记忆文件（选项还没加载完也要能显示）。"""
    root = Path(__file__).resolve().parent
    js = (root / "static" / "douyin-origin" / "douyin-workbench-shared.js").read_text(encoding="utf-8")

    assert "function applyDouyinStrangerMemoryDocSelection(" in js
    assert "function douyinStrangerSavedMemoryDocId(" in js
    assert "douyinStrangerMemoryDocPending" in js
    # 选中就记住 + 存一份 + 同步到本机接管配置
    assert "douyinStrangerMemoryDocTouched=true;saveDouyinStrangerMessagePresetAndSync(false);" in js
    # 记忆文件列表加载完 / 切换到预设 / 刷新监控状态 都要回显
    assert "select.innerHTML=html;if(current)select.value=current});applyDouyinStrangerMemoryDocSelection();" in js
    assert "writeDouyinStrangerMessagePresetForm(douyinStrangerMessagePresetState.presets[douyinStrangerMessagePresetState.activeIndex]||{});applyDouyinStrangerMemoryDocSelection();" in js
    assert "ensureDouyinStrangerLeadAccountSelection();applyDouyinStrangerMemoryDocSelection();" in js
    # 列表读不到时把已选那份补成选项，保证看得见
    assert "已选记忆文件（" in js


def test_h5_node_memory_requires_online_douyin_config(monkeypatch):
    """Online 抖音获客里没选记忆文件 → 明确报错，指向那个位置。"""
    record = _install_h5_task_harness(monkeypatch, [_h5_task_row()])
    monkeypatch.setattr(
        douyin_api,
        "get_douyin_stranger_message_monitor_state",
        lambda account_id, create=False: {},
    )

    result = asyncio.run(
        douyin_api.run_douyin_h5_stranger_message_task_once(account_id=5, reply_mode="ai_memory")
    )

    assert result["status"] == "failed"
    assert result["code"] == 400
    assert "抖音获客" in result["message"]
    assert record["sent"] == []


def test_h5_node_memory_uses_online_douyin_config(monkeypatch):
    """下发时记忆文件取 Online「抖音获客 → 私信引流」里选的那份。"""
    record = _install_h5_task_harness(monkeypatch, [_h5_task_row()])
    calls = []

    def fake_load(doc_ids, **kwargs):
        calls.append(list(doc_ids))
        return {"text": MEMORY_TEXT, "document_count": 1, "titles": ["百问百答"], "user_id": 1}

    monkeypatch.setattr(douyin_api, "load_douyin_takeover_memory_context", fake_load)
    monkeypatch.setattr(
        douyin_api,
        "get_douyin_stranger_message_monitor_state",
        lambda account_id, create=False: {"memory_doc_ids": ["online-faq"]},
    )

    result = asyncio.run(
        douyin_api.run_douyin_h5_stranger_message_task_once(account_id=5, reply_mode="ai_memory")
    )

    assert calls == [["online-faq"]]
    assert result["status"] == "completed"
    assert result["memory_titles"] == ["百问百答"]
    assert record["sent"] == ["基础版 999 元，含拍摄和剪辑"]
