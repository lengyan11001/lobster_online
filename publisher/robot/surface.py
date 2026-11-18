"""Surface：先认清"我现在在哪个界面"，再决定能不能动手。

这是上一版能力层最大的漏洞来源：能力只检查"有没有某个控件/文字"，于是
  - 在聊天页读会话，把聊天消息当成会话行；
  - 不在「新的朋友」页，也把聊天记录当成好友申请（pending=21 的假数据）。
新的规则：每个操作声明它要求的 surface；surface 不匹配就直接失败关闭（fail closed），
绝不"看起来像就干"。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

WECHAT_CHAT = "wechat.chat"
WECHAT_CONTACTS = "wechat.contacts"
WECHAT_FRIEND_REQUESTS = "wechat.friend_requests"
WECHAT_MOMENTS = "wechat.moments"
DOUYIN_UPLOAD = "douyin.upload"
DOUYIN_EDITOR = "douyin.editor"
DOUYIN_MANAGE = "douyin.manage"
UNKNOWN = "unknown"


@dataclass
class Surface:
    id: str
    anchors: Dict[str, Any] = field(default_factory=dict)
    evidence: Dict[str, Any] = field(default_factory=dict)

    @property
    def known(self) -> bool:
        return self.id != UNKNOWN


@dataclass
class Snapshot:
    """结构指纹：界面 + 条目列表 + 摘要。

    验证靠"前后两份快照的差异"，而不是"页面上某处出现过某段文字"——
    后者在抖音隐藏 toast、微信聊天记录上全都误判过。
    """

    surface: str
    items: List[str] = field(default_factory=list)
    anchors: Dict[str, Any] = field(default_factory=dict)

    @property
    def count(self) -> int:
        return len(self.items)

    @property
    def digest(self) -> str:
        raw = self.surface + "|" + "|".join(self.items[:40])
        return hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest()[:16]

    def has(self, text: str) -> bool:
        return any(str(text) in item for item in self.items)

    def to_dict(self) -> Dict[str, Any]:
        return {"surface": self.surface, "count": self.count, "digest": self.digest,
                "items": self.items[:20], "anchors": self.anchors}


def _aids(window: Any) -> set:
    from publisher.agent.desktop import _iter_controls, _visible

    out = set()
    for c, info in _iter_controls(window, limit=800):
        try:
            if not _visible(c):
                continue
            aid = str(getattr(info, "automation_id", "") or "")
            if aid:
                out.add(aid)
        except Exception:
            continue
    return out


def _left_rows(window: Any) -> List[Any]:
    """左侧列（会话列/联系人列）的 ListItem。"""
    from publisher.agent.desktop import _chat_left, _iter_controls, _rect, _visible

    rect = _rect(window) or (0, 0, 0, 0)
    left, width = rect[0], rect[2] - rect[0]
    chat_left = _chat_left(window, left, width)
    rows = []
    for c, info in _iter_controls(window, limit=800):
        try:
            if str(info.control_type or "") != "ListItem" or not _visible(c):
                continue
            rc = c.rectangle()
            if int(rc.left) >= chat_left:
                continue
            if (rc.right - rc.left) > max(260, int(width * 0.32)):
                continue
            rows.append(c)
        except Exception:
            continue
    return rows


def _name(control: Any) -> str:
    try:
        return " ".join(str(control.element_info.name or "").split())
    except Exception:
        return ""


def detect_wechat(window: Any) -> Surface:
    from publisher.agent.desktop import _window_visible

    if window is None or not _window_visible(window):
        return Surface(UNKNOWN, evidence={"reason": "window_not_visible"})
    aids = _aids(window)
    rows = [_name(r) for r in _left_rows(window)]
    if "session_list" in aids:
        return Surface(WECHAT_CHAT, anchors={"session_list": True, "rows": len(rows)},
                       evidence={"aids": sorted(a for a in aids if "list" in a)[:6]})
    # 「新的朋友」页：列表行里带“已添加/验证语/请求”，别只认 contact_list 这个 aid
    #（真机上这个 aid 时有时无，之前因此被判定 unknown，所有 op 都被失败关闭）
    requestish = [r for r in rows if ("已添加" in r or "验证" in r or "请求添加" in r or "接受" in r)]
    if requestish:
        return Surface(WECHAT_FRIEND_REQUESTS,
                       anchors={"request_rows": len(requestish), "contact_list": "contact_list" in aids},
                       evidence={"sample": requestish[0][:60]})
    if "contact_list" in aids:
        return Surface(WECHAT_CONTACTS, anchors={"contact_list": True, "rows": len(rows)})
    return Surface(UNKNOWN, evidence={"aids": sorted(aids)[:8], "rows": rows[:3]})


def detect_desktop(window: Any) -> Surface:
    """桌面端总入口：先判断是不是朋友圈窗口，再按微信主页判断。"""
    try:
        cls = str(window.class_name() or "")
    except Exception:
        cls = ""
    if "SNSWindow" in cls:
        return Surface(WECHAT_MOMENTS, anchors={"sns": True})
    return detect_wechat(window)


def snapshot_desktop(window: Any) -> Snapshot:
    surf = detect_desktop(window)
    if surf.id == WECHAT_MOMENTS:
        from publisher.agent.desktop import _iter_controls, _visible

        items = []
        for c, info in _iter_controls(window, limit=800):
            try:
                if str(info.control_type or "") != "ListItem" or not _visible(c):
                    continue
                text = " ".join(str(info.name or "").split())
                if len(text) > 6:
                    items.append(text[:160])
            except Exception:
                continue
        return Snapshot(surf.id, items, surf.anchors)
    rows = [_name(r) for r in _left_rows(window)]
    anchors = dict(surf.anchors)
    if surf.id == WECHAT_CHAT:
        from publisher.agent.desktop import _chat_left, _current_chat_name, _iter_controls, _rect, _visible

        anchors["chat"] = _current_chat_name(window)
        rect = _rect(window) or (0, 0, 0, 0)
        chat_left = _chat_left(window, rect[0], rect[2] - rect[0])
        msgs = []
        for c, info in _iter_controls(window, limit=800):
            try:
                if str(info.control_type or "") != "ListItem" or not _visible(c):
                    continue
                if int(c.rectangle().left) < chat_left:
                    continue
                text = " ".join(str(info.name or "").split())
                if text:
                    msgs.append(text[:160])
            except Exception:
                continue
        anchors["messages"] = msgs[-10:]
    return Snapshot(surf.id, [r for r in rows if r], anchors)


def detect_browser(page: Any) -> Surface:
    url = str(getattr(page, "url", "") or "")
    if "/content/manage" in url:
        return Surface(DOUYIN_MANAGE, anchors={"url": url})
    if "/content/upload" in url:
        return Surface(DOUYIN_UPLOAD, anchors={"url": url})
    if "/content/post/" in url:
        return Surface(DOUYIN_EDITOR, anchors={"url": url})
    return Surface(UNKNOWN, evidence={"url": url})


async def snapshot_browser(page: Any) -> Snapshot:
    """浏览器侧快照：surface + 关键地标文本（用来做前后差异）。"""
    surf = detect_browser(page)
    items: List[str] = []
    try:
        titles = await page.locator("h1,h2,button,[role=button]").all_inner_texts()
        items = [" ".join(t.split())[:60] for t in titles if t and t.strip()][:40]
    except Exception:
        items = []
    return Snapshot(surf.id, items, dict(surf.anchors))
