#!/usr/bin/env python3
"""Telegram（MTProto / Telethon）桥接 —— 与 scripts/whatsapp_web_bridge.mjs 同构。

命令：
  login    --auth <dir> --api-id <id> --api-hash <hash> [--phone +86...] [--proxy socks5://...]
           常驻进程，stdout 逐行 JSON 事件、stdin 逐行 JSON 回执（这是与 WhatsApp 扫码最大的不同）：
             out: {"event":"need_phone"} / need_code / need_password / linked / error
             in : {"cmd":"phone|code|password","value":"..."} / {"cmd":"cancel"}
           登录成功退出 0，session 落 --auth 目录，之后重启免登录。
  status   --auth <dir>                 {"ok":true,"linked":bool,"dependency":{...}}
  contacts --auth <dir> [--wait ms]     通讯录（含手机号）
  dialogs  --auth <dir> [--wait ms]     会话列表
  messages --auth <dir> --peer <key>    读历史
  groups   --auth <dir>                 群 / 频道列表
  profile  --auth <dir> --peer <key>    对方资料（bio / 头像）
  resolve  --auth <dir> --phones a,b    手机号反查
  send     --auth <dir> --to <peer> --text <s> [--image p | --file p] [--caption s]
  watch    --auth <dir> [--interval s]  常驻，把新消息按 JSONL 推出来
  logout   --auth <dir>                 解除登录 + 删本地 session
  selftest                              纯函数自检（不需要 telethon、不需要账号）

全局：--ledger <file> 关键节点只追加记账，事后排查只看这一份就够。
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime


def args():
    out = {"_": []}
    items = sys.argv[1:]
    i = 0
    while i < len(items):
        item = items[i]
        if item.startswith("--"):
            nxt = items[i + 1] if i + 1 < len(items) else ""
            if nxt and not nxt.startswith("--"):
                out[item[2:]] = nxt
                i += 1
            else:
                out[item[2:]] = "1"
        else:
            out["_"].append(item)
        i += 1
    return out


LEDGER = ""


def append_ledger(payload):
    """关键节点记账：只追加、不覆盖，写失败不影响主流程。"""
    if not LEDGER:
        return
    try:
        directory = os.path.dirname(LEDGER)
        if directory:
            os.makedirs(directory, exist_ok=True)
        record = {"at": datetime.now().astimezone().isoformat(), "source": "bridge"}
        record.update(payload)
        with open(LEDGER, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass


def emit(payload, stream=None):
    target = stream or sys.stdout
    target.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    target.flush()


def emit_all(stream, events):
    for event in events or []:
        emit(event, stream)


def _now():
    return datetime.now().astimezone().isoformat()


# ── 纯函数（selftest 覆盖，不需要 telethon / 不需要账号）─────────────────────
def normalize_phone(raw, default_country="+86"):
    """手机号规范化：00 前缀、缺国家码、带空格/横杠都归一成 +8613800000000 这种形态。"""
    text = re.sub(r"[^\d+]", "", str(raw or ""))
    if not text:
        return ""
    if text.startswith("00"):
        text = "+" + text[2:]
    if text.startswith("+"):
        digits = re.sub(r"\D", "", text)
        return "+" + digits if digits else ""
    digits = re.sub(r"\D", "", text)
    if not digits:
        return ""
    country = re.sub(r"\D", "", str(default_country or "")) or "86"
    if digits.startswith(country) and len(digits) > len(country) + 6:
        return "+" + digits
    return "+" + country + digits.lstrip("0")


def peer_key(peer_type, value):
    """Telegram 的 peer 语义比 WhatsApp 的 jid 复杂：统一成 user:/chat:/channel: 三种前缀。"""
    kind = str(peer_type or "user").strip().lower()
    if kind not in ("user", "chat", "channel"):
        raise ValueError("peer_type 只支持 user / chat / channel")
    ident = str(value if value is not None else "").strip()
    if not ident:
        raise ValueError("peer 值不能为空")
    return kind + ":" + re.sub(r"\s+", "", ident)


def _module_probe(name):
    import importlib.util

    try:
        spec = importlib.util.find_spec(name)
    except Exception:
        spec = None
    return (spec is not None, str(getattr(spec, "origin", "") or ""))


def _session_file(a):
    auth = str(a.get("auth") or "").strip()
    if not auth:
        auth = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "native_telegram", "session")
    return os.path.join(auth, "telegram.session")


def _load_telethon():
    """唯一 import telethon 的地方：单测里换掉它就能把登录全流程跑完。"""
    import telethon  # noqa: F401
    from telethon import errors

    return telethon, errors


class LoginSession:
    """手机号 → 验证码 →（2FA 密码）登录状态机。

    和 WhatsApp 扫二维码最大的不同：这是三方交互，不能一次性跑完，
    所以设计成「喂一个 cmd 进去、吐一串 event 出来」的状态机，
    与 telethon 的接触面收敛到 client() 一处 —— 测试塞个假 telethon 就能全流程验证。
    """

    def __init__(self, auth_dir, api_id, api_hash, phone="", proxy="", ledger=""):
        self.auth_dir = str(auth_dir or "")
        self.api_id = str(api_id or "")
        self.api_hash = str(api_hash or "")
        self.proxy = str(proxy or "")
        self.ledger = ledger
        self.phone = normalize_phone(phone)
        self.step = "need_phone"
        self.error = ""
        self.me = {}
        self._client = None

    @property
    def session_file(self):
        return os.path.join(self.auth_dir, "telegram.session")

    def client(self):
        if self._client is not None:
            return self._client
        telethon, _errors = _load_telethon()
        if self.auth_dir:
            os.makedirs(self.auth_dir, exist_ok=True)
        kwargs = {}
        if self.proxy:
            kwargs["proxy"] = self.proxy
        self._client = telethon.TelegramClient(self.session_file, int(self.api_id), self.api_hash, **kwargs)
        return self._client

    def close(self):
        if self._client is None:
            return
        try:
            self._client.disconnect()
        except Exception:
            pass

    @staticmethod
    def _me_payload(entity):
        return {
            "id": str(getattr(entity, "id", "") or ""),
            "username": str(getattr(entity, "username", "") or ""),
            "first_name": str(getattr(entity, "first_name", "") or ""),
            "last_name": str(getattr(entity, "last_name", "") or ""),
            "phone": str(getattr(entity, "phone", "") or ""),
        }

    def handle(self, cmd):
        """返回本次要 emit 的事件列表。"""
        name = str((cmd or {}).get("cmd") or "").strip().lower()
        value = str((cmd or {}).get("value") or "").strip()
        if name == "cancel":
            self.step = "cancelled"
            append_ledger({"event": "login_cancelled"})
            return [{"event": "cancelled", "message": "已取消登录"}]
        if name == "phone":
            return self._do_phone(value)
        if name == "code":
            return self._do_code(value)
        if name == "password":
            return self._do_password(value)
        return [{"event": "error", "error": "unknown_command", "message": "不认识的指令：" + name}]

    def _do_phone(self, value):
        phone = normalize_phone(value) or self.phone
        if not phone:
            return [{"event": "error", "error": "bad_phone", "message": "手机号为空"}]
        self.phone = phone
        try:
            client = self.client()
            client.connect()
            client.send_code_request(phone)
        except Exception as exc:
            self.step = "error"
            append_ledger({"event": "login_send_code_failed", "error": str(exc)[:300]})
            return [{"event": "error", "error": "send_code_failed", "message": str(exc)[:500]}]
        self.step = "need_code"
        append_ledger({"event": "login_code_sent", "phone": phone})
        return [{"event": "need_code", "phone": phone, "message": "验证码已下发（Telegram App 里那条），填进来"}]

    def _do_code(self, value):
        code = re.sub(r"\D", "", str(value or ""))
        if not code:
            return [{"event": "error", "error": "bad_code", "message": "验证码为空"}]
        _telethon, errors = _load_telethon()
        password_cls = getattr(errors, "SessionPasswordNeededError", None)
        try:
            self.client().sign_in(phone=self.phone, code=code)
        except Exception as exc:
            if password_cls is not None and isinstance(exc, password_cls):
                self.step = "need_password"
                append_ledger({"event": "login_need_password"})
                return [{"event": "need_password", "message": "这个号开了两步验证，把 2FA 密码填进来"}]
            self.step = "error"
            append_ledger({"event": "login_sign_in_failed", "error": str(exc)[:300]})
            return [{"event": "error", "error": "sign_in_failed", "message": str(exc)[:500]}]
        return self._linked_events()

    def _do_password(self, value):
        if not str(value or "").strip():
            return [{"event": "error", "error": "bad_password", "message": "2FA 密码为空"}]
        try:
            self.client().sign_in(password=str(value))
        except Exception as exc:
            self.step = "error"
            append_ledger({"event": "login_password_failed", "error": str(exc)[:300]})
            return [{"event": "error", "error": "password_failed", "message": str(exc)[:500]}]
        return self._linked_events()

    def _linked_events(self):
        events = []
        try:
            entity = self.client().get_me()
            self.me = self._me_payload(entity)
            events.append(
                {
                    "event": "linked",
                    "me": self.me,
                    "session_file": self.session_file,
                    "message": "登录成功，session 已落盘，之后重启不用再登",
                }
            )
        except Exception as exc:
            events.append({"event": "error", "error": "get_me_failed", "message": str(exc)[:500]})
        self.step = "linked" if events and events[0].get("event") == "linked" else "error"
        append_ledger({"event": "login_linked" if self.step == "linked" else "login_get_me_failed"})
        self.close()
        return events

    def run(self, stdin, stdout):
        emit({"event": self.step, "message": "把手机号喂进来（{\"cmd\":\"phone\",\"value\":\"+86...\"}）"}, stdout)
        for line in stdin:
            line = str(line).strip()
            if not line:
                continue
            try:
                cmd = json.loads(line)
            except Exception:
                cmd = {"cmd": line}
            if not isinstance(cmd, dict):
                cmd = {"cmd": "unknown"}
            emit_all(stdout, self.handle(cmd))
            if self.step in ("linked", "error", "cancelled"):
                break
        self.close()
        return 0 if self.step == "linked" else 2


# ── 命令 ─────────────────────────────────────────────────────────────────
def cmd_selftest(a):
    checks = []
    assert normalize_phone("13800000000") == "+8613800000000"
    checks.append("phone_plain")
    assert normalize_phone("+86 138 0000 0000") == "+8613800000000"
    checks.append("phone_spaced")
    assert normalize_phone("008613800000000") == "+8613800000000"
    checks.append("phone_00_prefix")
    assert normalize_phone("8613800000000") == "+8613800000000"
    checks.append("phone_with_country")
    assert normalize_phone("") == ""
    checks.append("phone_empty")
    assert peer_key("user", "12345") == "user:12345"
    assert peer_key("chat", " 777 ") == "chat:777"
    assert peer_key("channel", 888) == "channel:888"
    checks.append("peer_key")
    try:
        peer_key("group", "1")
        raise AssertionError("非法 peer_type 应该抛错")
    except ValueError:
        checks.append("peer_key_rejects_unknown_type")
    telethon_ok, _origin = _module_probe("telethon")
    return {
        "ok": True,
        "checks": checks,
        "count": len(checks),
        "dependency": {"telethon": telethon_ok},
    }


def cmd_status(a):
    telethon_ok, origin = _module_probe("telethon")
    session_file = _session_file(a)
    try:
        linked = bool(os.path.exists(session_file) and os.path.getsize(session_file) > 0)
    except Exception:
        linked = False
    return {
        "ok": True,
        "linked": linked,
        "session_file": session_file,
        "dependency": {"telethon": telethon_ok, "telethon_source": origin},
        "me": {},
        "at": _now(),
    }


def _not_implemented(command, phase, note=""):
    return {
        "ok": False,
        "error": "not_implemented",
        "phase": phase,
        "command": command,
        "message": note or (command + " 属于 " + phase + "，当前是 P0 骨架，还没接入"),
    }


def cmd_login(a):
    try:
        _load_telethon()
    except Exception as exc:
        return {
            "event": "error",
            "error": "telethon_missing",
            "message": "本机没装 telethon，登录跑不起来：" + str(exc)[:300],
            "hint": "在联网环境把 telethon / pyaes 的 wheel 放进 deps/wheels 再装（见规划稿 P5）",
        }
    session = LoginSession(
        auth_dir=a.get("auth") or "",
        api_id=a.get("api-id") or a.get("api_id") or "",
        api_hash=a.get("api-hash") or a.get("api_hash") or "",
        phone=a.get("phone") or "",
        proxy=a.get("proxy") or "",
        ledger=LEDGER,
    )
    if not session.api_id or not session.api_hash:
        return {"event": "error", "error": "api_id_missing", "message": "缺少 --api-id / --api-hash"}
    if a.get("phone"):
        emit_all(sys.stdout, session.handle({"cmd": "phone", "value": a.get("phone")}))
        if session.step in ("linked", "error", "cancelled"):
            session.close()
            return None
    code = session.run(sys.stdin, sys.stdout)
    return {"_exit": code}


HANDLERS = {
    "selftest": cmd_selftest,
    "status": cmd_status,
    "login": cmd_login,
    "logout": lambda a: _not_implemented("logout", "P1"),
    "contacts": lambda a: _not_implemented("contacts", "P2"),
    "dialogs": lambda a: _not_implemented("dialogs", "P2"),
    "messages": lambda a: _not_implemented("messages", "P2"),
    "groups": lambda a: _not_implemented("groups", "P2"),
    "profile": lambda a: _not_implemented("profile", "P2"),
    "resolve": lambda a: _not_implemented("resolve", "P2"),
    "send": lambda a: _not_implemented("send", "P3"),
    "watch": lambda a: _not_implemented("watch", "P4"),
}


def main():
    global LEDGER
    # 中文 json 不能跟着 Windows 控制台代码页走：两端统一 UTF-8
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    a = args()
    LEDGER = str(a.get("ledger") or "")
    name = a["_"][0] if a["_"] else "status"
    handler = HANDLERS.get(name)
    if handler is None:
        emit({"ok": False, "error": "unknown_command", "command": name, "known": sorted(HANDLERS)})
        return 2
    try:
        result = handler(a)
    except Exception as exc:
        append_ledger({"event": "command_failed", "command": name, "error": str(exc)[:300]})
        emit({"ok": False, "error": "command_failed", "command": name, "message": str(exc)[:500]})
        return 3
    if result is None:
        return 0
    if isinstance(result, dict) and "_exit" in result:
        return int(result["_exit"])
    ok = bool(result.get("ok", True))
    if name != "login":
        result.setdefault("command", name)
        append_ledger({"event": "command_done", "command": name, "ok": ok})
    emit(result)
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
