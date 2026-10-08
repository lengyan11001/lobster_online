# -*- coding: utf-8 -*-
"""猎聘（lpt.liepin.com 招聘端）浏览器自动化底座。

设计要点（与抖音发布/抖音获客同一套思路）：
1. 浏览器用客户端自带的 Chromium（browser_chromium），带 --remote-debugging-port 启动；
2. 登录态复用同一套用户资料目录，用户扫码/账号登录一次即可长期复用；
3. 所有"界面操作"都封装成小能力（搜索/翻页/读卡片/开简历/进沟通页/发消息），不暴露裸导航；
4. 猎聘有风控，会把页面置空（about:blank），因此每个动作都带「探测 -> 重启窗口 -> 重试」；
5. 所有写操作（发消息）默认拒绝，必须显式 confirm=True，并写台账防止重复发。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

CDP_PORT = int(os.environ.get("LIEPIN_CDP_PORT", "9222"))
SEARCH_URL = "https://lpt.liepin.com/search"
CHAT_URL = "https://lpt.liepin.com/chat/im"
SEARCH_BOX = 'input[placeholder*="搜职位"]'
DEAD_URL_PREFIXES = ("about:blank", "chrome-error://", "chrome://")

DATE_RE = re.compile(r"20\d{2}\.\d{2}-")
AGE_RE = re.compile(r"^\d{1,2}岁$")
NAME_RE = re.compile(r"^\S+\*\*$")


class LiepinError(RuntimeError):
    pass


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


def find_chromium(root: Optional[Path] = None) -> Optional[Path]:
    """定位客户端内置 Chromium。"""
    root = Path(root or _root())
    candidates: List[Path] = []
    base = root / "browser_chromium"
    if base.is_dir():
        for child in sorted(base.glob("chromium-*")):
            candidates.append(child / "chrome-win64" / "chrome.exe")
            candidates.append(child / "chrome-win" / "chrome.exe")
        candidates.append(base / "chrome.exe")
    for exe in ("chrome.exe", "msedge.exe"):
        found = shutil.which(exe)
        if found:
            candidates.append(Path(found))
    for c in candidates:
        if c.is_file():
            return c
    return None


def parse_card_text(text: str) -> Dict[str, Any]:
    """把猎聘列表卡片文本解析成结构化字段（已在 150+ 真实卡片上验证）。"""
    parts = [p.strip() for p in re.split(r"\s*\|\s*", text or "") if p.strip()]
    out: Dict[str, Any] = {"name": "", "age": None, "years": None, "edu": "", "city": "",
                           "expect": "", "industry": "", "companies": [], "active": "",
                           "school": ""}
    idx = None
    for i, x in enumerate(parts):
        if NAME_RE.match(x) or (i + 1 < len(parts) and AGE_RE.match(parts[i + 1])):
            idx = i
            break
    if idx is None:
        return out
    out["name"] = parts[idx]
    out["active"] = "、".join(parts[:idx])[:24]
    if idx + 1 < len(parts) and AGE_RE.match(parts[idx + 1]):
        out["age"] = int(parts[idx + 1][:-1])
    if idx + 2 < len(parts):
        m = re.match(r"^(\d{1,2})年$", parts[idx + 2])
        if m:
            out["years"] = int(m.group(1))
    if idx + 3 < len(parts):
        out["edu"] = parts[idx + 3]
    if idx + 4 < len(parts):
        out["city"] = parts[idx + 4]
    for i, x in enumerate(parts):
        if x.startswith("期望") and i + 1 < len(parts):
            out["expect"] = parts[i + 1]
            if i + 2 < len(parts) and not DATE_RE.search(parts[i + 2]):
                out["industry"] = parts[i + 2]
            break
    for i, x in enumerate(parts):
        if DATE_RE.search(x):
            company = parts[i - 1] if i >= 1 else ""
            if re.match(r"^\d{4}\.\d{2}", company):
                continue
            out["companies"].append({"company": company, "position": x})
    for x in parts:
        if re.search(r"大学|学院|学校", x) and "统招" in x or re.search(r"(大学|学院).*(本科|硕士|博士|大专)", x):
            out["school"] = x
            break
    return out


class LiepinSession:
    """一个猎聘浏览器会话（进程级单例由上层保证）。"""

    def __init__(self, root: Optional[Path] = None, port: int = CDP_PORT):
        self.root = Path(root or _root())
        self.port = port
        self.chrome = find_chromium(self.root)
        self._pw = None
        self._browser = None
        self._page = None

    # ---------- 台账 ----------
    @property
    def runtime_dir(self) -> Path:
        d = self.root / "_lobster_runtime" / "liepin"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def ledger_path(self) -> Path:
        return self.runtime_dir / "ledger.json"

    def ledger(self) -> Dict[str, Any]:
        if self.ledger_path.is_file():
            try:
                return json.loads(self.ledger_path.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {"messages": [], "searches": [], "details": []}

    def _save_ledger(self, data: Dict[str, Any]) -> None:
        self.ledger_path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    def record(self, kind: str, payload: Dict[str, Any]) -> None:
        data = self.ledger()
        data.setdefault(kind, []).append({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), **payload})
        self._save_ledger(data)

    # ---------- 浏览器进程 ----------
    def cdp_alive(self) -> bool:
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/json/version" % self.port, timeout=3) as r:
                return r.status == 200
        except Exception:
            return False

    def _port_owner(self) -> Optional[int]:
        if os.name != "nt":
            return None
        try:
            out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, timeout=20).stdout
        except Exception:
            return None
        for line in out.splitlines():
            if ":%d" % self.port in line and "LISTENING" in line:
                tail = line.split()[-1]
                if tail.isdigit():
                    return int(tail)
        return None

    def kill_browser(self) -> None:
        if os.name != "nt":
            return
        pid = self._port_owner()
        if not pid:
            return
        ps = ("$o=%d; Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
              "Where-Object { $_.ParentProcessId -eq $o -or $_.ProcessId -eq $o } | "
              "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; "
              "Stop-Process -Id $o -Force -ErrorAction SilentlyContinue") % pid
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True)
        time.sleep(4)

    def start_browser(self, url: str = SEARCH_URL, settle: float = 26.0) -> None:
        if not self.chrome:
            raise LiepinError("找不到客户端内置 Chromium（browser_chromium）")
        args = ["--remote-debugging-port=%d" % self.port, "--remote-allow-origins=*",
                "--no-first-run", "--no-default-browser-check", "--hide-crash-restore-bubble"]
        subprocess.Popen([str(self.chrome), *args, url], close_fds=True)
        time.sleep(settle)

    def restart_browser(self, url: str = SEARCH_URL) -> None:
        self.kill_browser()
        self._page = None
        self._browser = None
        self.start_browser(url)

    # ---------- 连接 ----------
    def _ensure_connected(self):
        if self._browser is not None:
            return self._browser
        from playwright.sync_api import sync_playwright  # 延迟导入，避免无 playwright 环境启动失败
        if self._pw is None:
            self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.connect_over_cdp("http://127.0.0.1:%d" % self.port)
        return self._browser

    def _liepin_page(self):
        if self._browser is None:
            return None
        for ctx in self._browser.contexts:
            for pg in ctx.pages:
                url = pg.url or ""
                if url.startswith("https://lpt.liepin.com") and not url.startswith(DEAD_URL_PREFIXES):
                    return pg
        return None

    def page(self, need_search_box: bool = True, retries: int = 2):
        """拿到一个可用的猎聘页面；被风控置空时自动重启窗口重试。"""
        for attempt in range(retries + 1):
            if not self.cdp_alive():
                self.start_browser()
            try:
                self._ensure_connected()
            except Exception as exc:
                raise LiepinError("连接调试端口失败：%s" % exc)
            pg = self._liepin_page()
            if pg is not None:
                if not need_search_box:
                    self._page = pg
                    return pg
                ok = False
                for _ in range(30):
                    try:
                        if pg.locator(SEARCH_BOX).count() > 0:
                            ok = True
                            break
                    except Exception:
                        break
                    pg.wait_for_timeout(1500)
                if ok:
                    self._page = pg
                    return pg
            time.sleep(2)
            self.restart_browser()
        raise LiepinError("猎聘页面不可用（多次重启后仍拿不到页面）")

    # ---------- 动作 ----------
    def login_state(self) -> Dict[str, Any]:
        pg = self.page(need_search_box=True, retries=1)
        body = pg.evaluate("() => document.body.innerText.slice(0, 400)")
        logged = "登录" not in body[:120] or "搜索人才" in body
        return {"logged_in": bool(logged), "url": pg.url, "account": _guess_account(body)}

    def search(self, query: str, limit: int = 20, retries: int = 3) -> Dict[str, Any]:
        """搜索人才并返回结构化卡片。每次搜索都确保页面是"新鲜"的（猎聘风控）。"""
        last_err = ""
        for attempt in range(retries):
            try:
                pg = self.page(need_search_box=True, retries=1)
                box = pg.locator(SEARCH_BOX).first
                box.click(force=True, timeout=20000)
                pg.keyboard.type(query, delay=30)
                pg.wait_for_timeout(800)
                try:
                    pg.locator('button:has-text("搜索")').first.click(force=True, timeout=8000)
                except Exception:
                    pg.keyboard.press("Enter")
                pg.wait_for_timeout(6000)
                for _ in range(10):
                    try:
                        if "岁" in pg.evaluate("() => document.body.innerText"):
                            break
                    except Exception:
                        break
                    pg.wait_for_timeout(1500)
                body = pg.evaluate("() => document.body.innerText")
                if "岁" not in body:
                    last_err = "搜索结果为空（可能被风控置空）"
                    self.restart_browser()
                    continue
                total = (re.search(r"共有[^\n]{0,20}", body) or [""])[0]
                cards = self._read_cards(pg, limit)
                self.record("searches", {"query": query, "total": total, "count": len(cards)})
                return {"ok": True, "url": pg.url, "total": total, "count": len(cards), "cards": cards}
            except Exception as exc:
                last_err = str(exc)[:200]
                self.restart_browser()
        return {"ok": False, "error": last_err or "搜索失败", "query": query}

    def _read_cards(self, pg, limit: int) -> List[Dict[str, Any]]:
        raw = pg.evaluate(
            """(limit) => {
              const out = [];
              document.querySelectorAll('div,li,article,section').forEach(el => {
                const t = (el.innerText || '').replace(/\\s*\\n\\s*/g, ' | ').replace(/\\s+/g, ' ').trim();
                if (!t.includes('岁') || t.length < 40 || t.length > 1500) return;
                const r = el.getBoundingClientRect();
                if (r.width < 600 || r.width > 1400) return;
                const hasAction = /立即沟通|继续沟通|打招呼/.test(t);
                out.push({t, hasAction, area: Math.round(r.width * r.height)});
              });
              out.sort((a, b) => (b.hasAction ? 1 : 0) - (a.hasAction ? 1 : 0) || a.area - b.area);
              return out.slice(0, limit).map(x => x.t);
            }""", limit)
        seen, cards = set(), []
        for text in raw or []:
            card = parse_card_text(text)
            if not card.get("name"):
                continue
            key = (card["name"], card.get("age"))
            if key in seen:
                continue
            seen.add(key)
            card["raw"] = text
            card["in_chat"] = "继续沟通" in text
            cards.append(card)
        return cards

    def chat_page(self, retries: int = 2):
        """打开「沟通」页（在线沟通 /chat/im）。"""
        for _ in range(retries + 1):
            pg = self.page(need_search_box=False, retries=1)
            if "/chat" in (pg.url or ""):
                return pg
            try:
                pg.locator('text="沟通"').first.click(force=True, timeout=8000)
                pg.wait_for_timeout(6000)
                if "/chat" in (pg.url or ""):
                    return pg
            except Exception:
                pass
            self.restart_browser()
        raise LiepinError("无法进入猎聘沟通页")

    def chat_list(self, limit: int = 20) -> Dict[str, Any]:
        pg = self.chat_page()
        rows = pg.evaluate(
            """(limit) => Array.from(document.querySelectorAll('div,li'))
                 .filter(e => /(先生|女士|\\d{2}岁)/.test(e.innerText || '')
                    && e.getBoundingClientRect().width > 150 && e.getBoundingClientRect().width < 520)
                 .slice(0, limit)
                 .map(e => (e.innerText || '').replace(/\\s*\\n\\s*/g, ' | ').trim().slice(0, 200))""", limit)
        return {"ok": True, "url": pg.url, "conversations": rows or []}

    def chat_send(self, target: str, text: str, confirm: bool = False) -> Dict[str, Any]:
        """给会话列表里的某人发消息（写操作：必须 confirm=True，且写入台账）。"""
        if not confirm:
            return {"ok": False, "error": "写操作需 confirm=True（发送消息会真实触达候选人）"}
        if not text or not text.strip():
            return {"ok": False, "error": "消息内容为空"}
        led = self.ledger()
        for item in led.get("messages", []):
            if item.get("target") == target and item.get("text") == text:
                return {"ok": False, "error": "已发送过相同内容，跳过（防重复）", "ledger": item}
        pg = self.chat_page()
        opened = pg.evaluate(
            """(target) => {
              const rows = Array.from(document.querySelectorAll('div,li'))
                .filter(e => (e.innerText || '').includes(target)
                   && e.getBoundingClientRect().width > 150 && e.getBoundingClientRect().width < 520);
              if (!rows.length) return false;
              rows[0].click();
              return true;
            }""", target)
        if not opened:
            return {"ok": False, "error": "沟通列表里找不到该联系人（需先建立沟通关系）"}
        pg.wait_for_timeout(4000)
        box = None
        for sel in ["textarea", '[contenteditable="true"]', 'input[placeholder*="输入"]']:
            loc = pg.locator(sel).last
            try:
                if loc.count() > 0 and loc.is_visible():
                    box = loc
                    break
            except Exception:
                continue
        if box is None:
            return {"ok": False, "error": "未找到输入框（页面结构可能被风控改版，需重新探测）"}
        box.click(force=True, timeout=8000)
        pg.keyboard.type(text, delay=20)
        pg.wait_for_timeout(1000)
        sent = False
        for sel in ['button:has-text("发送")', '[class*=send]']:
            loc = pg.locator(sel).last
            try:
                if loc.count() > 0 and loc.is_visible():
                    loc.click(force=True, timeout=6000)
                    sent = True
                    break
            except Exception:
                continue
        if not sent:
            pg.keyboard.press("Enter")
        pg.wait_for_timeout(3000)
        body = pg.evaluate("() => document.body.innerText")
        ok = text[:14] in body
        self.record("messages", {"target": target, "text": text, "verified": bool(ok)})
        return {"ok": bool(ok), "target": target, "verified": bool(ok),
                "note": "页面已出现该消息" if ok else "已提交但页面未回显，请人工确认"}

    def status(self) -> Dict[str, Any]:
        return {"ok": True, "cdp_alive": self.cdp_alive(), "cdp_port": self.port,
                "chromium": str(self.chrome) if self.chrome else None,
                "ledger": {k: len(v) for k, v in self.ledger().items() if isinstance(v, list)}}


def _guess_account(body: str) -> str:
    m = re.search(r"\|\s*([\u4e00-\u9fa5]{2,4})\s*\|\s*设置", body or "")
    return m.group(1) if m else ""
