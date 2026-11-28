"""引擎侧 OCR 兜底：UIA 原语失败后，换一套"看屏幕点"的原语。

对应 docs\个微接管-流程盘点-20261118.md §6.3 的形状：
    引擎原语 → 失败 → 换原语（这里） → 仍失败才报错

只做三件事（都已真机跑通或部分跑通）：
    add_friend      加好友（已验证到真实提交）
    send_text       回消息（已验证文件传输助手收发）
    publish_moments 朋友圈图文（到编辑页已通，发表后确认待补）

开关：环境变量 LOBSTER_OCR_FALLBACK=0 可整体关掉（默认开）。
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

from . import wechat_ocr_actions as actions


def enabled() -> bool:
    return str(os.environ.get("LOBSTER_OCR_FALLBACK", "1")).strip() not in ("0", "false", "False", "")


# ------------------------------------------------------------------ 名字解析

def _display_name(account_id: str, peer_id: str) -> str:
    """peer_id(wxid) → 会话显示名，OCR 搜索框只能按名字搜。"""
    from . import native_wechat_engine as eng

    pid = str(peer_id or "").strip()
    if not pid:
        return ""
    try:
        with eng._connect() as conn:
            row = conn.execute(
                "select display_name from wechat_peers where account_id=? and peer_id=? limit 1",
                (account_id, pid),
            ).fetchone()
            if row and str(row[0] or "").strip():
                return str(row[0]).strip()
            row = conn.execute(
                "select remark, display_name from wechat_contacts "
                "where account_id=? and (contact_key=? or wx_no=?) limit 1",
                (account_id, pid, pid),
            ).fetchone()
            if row:
                return str(row[0] or row[1] or "").strip()
    except Exception:
        pass
    return ""


# ------------------------------------------------------------------ 三个兜底

def add_friend(account_id: str, keyword: str, *, apply_message: str = "",
               remark: str = "", submit: bool = True) -> Dict[str, Any]:
    res = actions.add_friend(keyword, hello=apply_message, remark=remark, submit=submit)
    status = str(res.get("status") or "")
    return {
        "ok": bool(res.get("ok")),
        "status": {"pending_submit": "prepared"}.get(status, status or "failed"),
        "submitted": status == "submitted",
        "already_friend": status == "already_friend",
        "message": (
            res.get("error")
            or ("好友申请已提交（OCR）" if status == "submitted"
                else "搜不到该账号（OCR）" if status == "not_found"
                else "已进入申请页待提交（OCR）" if status == "pending_submit"
                else "OCR 加好友未完成")
        ),
        "raw": {"driver": "ocr", "notes": res.get("notes") or [], "verified": res.get("verified")},
    }


def send_text(account_id: str, peer_id: str, text: str, *, reason: str = "") -> Dict[str, Any]:
    name = _display_name(account_id, peer_id)
    if not name:
        raise RuntimeError(f"OCR 兜底失败：解析不到 {peer_id} 的会话名（UIA 错误：{reason[:120]}）")
    res = actions.send_text(name, text)
    if not res.get("ok"):
        raise RuntimeError(f"OCR 发送失败：{res.get('error') or res.get('notes')}（UIA 错误：{reason[:120]}）")
    return {
        "ok": True,
        "driver": "ocr_send_text",
        "message": {"id": f"ocr-{int(time.time() * 1000)}", "text": text, "to": name},
        "raw": {"driver": "ocr", "chat": name, "verified": res.get("verified"), "uia_error": reason[:200]},
    }


def _attachment_paths(attachments: Optional[List[Dict[str, Any]]]) -> List[str]:
    out: List[str] = []
    for att in attachments or []:
        if isinstance(att, str):
            out.append(att)
            continue
        if not isinstance(att, dict):
            continue
        for key in ("path", "file_path", "local_path", "file"):
            value = str(att.get(key) or "").strip()
            if value and os.path.exists(value):
                out.append(value)
                break
    return out


def publish_moments(account_id: str, content: str, *, attachments: Optional[List[Dict[str, Any]]] = None,
                    visibility: str = "public") -> Dict[str, Any]:
    paths = _attachment_paths(attachments)
    if not paths:
        raise RuntimeError("OCR 兜底只支持本地图片文件（attachments 里没有可用的本地路径）")
    res = actions.post_moments(paths, content,
                               visibility="private" if visibility == "private" else "public",
                               submit=True)
    if not res.get("ok"):
        raise RuntimeError(f"OCR 发朋友圈失败：{res.get('error')}")
    return {
        "ok": True,
        "driver": "ocr_moments",
        "message": {"text": content, "images": len(paths)},
        "raw": {"driver": "ocr", "submitted": res.get("submitted"), "notes": res.get("notes")},
    }