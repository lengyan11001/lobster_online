# -*- coding: utf-8 -*-
"""猎聘（lpt.liepin.com 招聘端）浏览器自动化底座。

与抖音发布/抖音获客同一套思路：用本机正常 Chrome + 独立资料目录，用户扫码登录一次，
之后由客户端驱动浏览器做事；简单只读的抓取走协议直连（liepin_protocol.py）。

线程模型（重要）：
  客户端后端是 FastAPI，同步端点跑在线程池里，而 Playwright 的 sync 对象**绑定线程**，
  长期缓存连接会报 greenlet.error / TargetClosedError。所以这里**每次调用都新建连接、
  用完立刻关闭**，只有 cookie 做短时缓存（避免重复连接）。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import urllib.request
from contextlib import contextmanager
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
SCHOOL_RE = re.compile(r"大学|学院|学校|职院|职业技术学院")

_COOKIE_CACHE: Dict[str, Any] = {"ts": 0.0, "jar": None}
COOKIE_TTL = 120.0


class LiepinError(RuntimeError):
    pass


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


def find_chrome(root: Optional[Path] = None) -> Optional[Path]:
    """定位「正常的 Chrome」：注册表 App Paths -> Program Files -> 用户目录 -> PATH -> 内置 Chromium 兜底。"""
    root = Path(root or _root())
    candidates: List[Path] = []
    if os.name == "nt":
        try:
            import winreg  # type: ignore

            for hive, sub in (
                (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"),
                (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"),
                (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"),
            ):
                try:
                    with winreg.OpenKey(hive, sub) as key:
                        value, _ = winreg.QueryValueEx(key, "")
                        if value:
                            candidates.append(Path(str(value).strip('"').strip()))
                except OSError:
                    continue
        except Exception:
            pass
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), os.environ.get("LOCALAPPDATA")):
        if base:
            candidates.append(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe")
    for exe in ("chrome.exe", "msedge.exe"):
        found = shutil.which(exe)
        if found:
            candidates.append(Path(found))
    bundled = root / "browser_chromium"
    if bundled.is_dir():
        for child in sorted(bundled.glob("chromium-*")):
            candidates.append(child / "chrome-win64" / "chrome.exe")
            candidates.append(child / "chrome-win" / "chrome.exe")
        candidates.append(bundled / "chrome.exe")
    for c in candidates:
        if c.is_file():
            return c
    return None


def find_chromium(root: Optional[Path] = None) -> Optional[Path]:
    """兼容旧调用。"""
    return find_chrome(root)


def parse_card_text(text: str) -> Dict[str, Any]:
    """把猎聘列表卡片文本解析成结构化字段。"""
    parts = [p.strip() for p in re.split(r"\s*\|\s*", text or "") if p.strip()]
    out: Dict[str, Any] = {"name": "", "age": None, "years": None, "edu": "", "city": "",
                           "expect": "", "industry": "", "companies": [], "active": "", "school": ""}
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
            if re.match(r"^\d{4}\.\d{2}", company) or not company:
                continue
            if SCHOOL_RE.search(company) or re.search(r"统招", x):
                out["school"] = "%s %s" % (company, x)
                continue
            out["companies"].append({"company": company, "position": x})
    for x in parts:
        if not out["school"] and SCHOOL_RE.search(x) and ("统招" in x or "非统招" in x):
            out["school"] = x
            break
    return out


def _guess_account(body: str) -> str:
    text = re.sub(r"\s*\n\s*", " | ", body or "")
    text = re.sub(r"\s*\|\s*", " | ", text)
    for pat in (r"我的权益\s*\|\s*([\u4e00-\u9fa5A-Za-z]{2,6})\s*\|",
                r"\|\s*([\u4e00-\u9fa5A-Za-z]{2,6})\s*\|\s*设置",
                r"设置\s*\|\s*([\u4e00-\u9fa5A-Za-z]{2,6})"):
        m = re.search(pat, text)
        if m:
            return m.group(1)
    return ""


class LiepinSession:
    """猎聘浏览器会话（无状态连接：每次调用新建/关闭，避免线程绑定问题）。"""

    def __init__(self, root: Optional[Path] = None, port: int = CDP_PORT):
        self.root = Path(root or _root())
        self.port = port
        self.chrome = find_chrome(self.root)

    # ---------- 目录 / 台账 ----------
    @property
    def runtime_dir(self) -> Path:
        d = self.root / "_lobster_runtime" / "liepin"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def profile_dir(self) -> Path:
        d = self.runtime_dir / "chrome-profile"
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
        _COOKIE_CACHE["jar"] = None
        time.sleep(3)

    def start_browser(self, url: str = SEARCH_URL, settle: float = 22.0) -> None:
        if not self.chrome:
            raise LiepinError("找不到 Chrome（本机 Chrome 或客户端内置 Chromium）")
        if self.cdp_alive():
            return
        args = ["--remote-debugging-port=%d" % self.port, "--remote-allow-origins=*",
                "--no-first-run", "--no-default-browser-check", "--hide-crash-restore-bubble",
                "--user-data-dir=%s" % self.profile_dir]
        subprocess.Popen([str(self.chrome), *args, url], close_fds=True)
        _COOKIE_CACHE["jar"] = None
        time.sleep(settle)

    def restart_browser(self, url: str = SEARCH_URL) -> None:
        self.kill_browser()
        self.start_browser(url, settle=24.0)

    # ---------- 连接（每次调用新建） ----------
    @contextmanager
    def _connect(self):
        from playwright.sync_api import sync_playwright  # 延迟导入

        pw = sync_playwright().start()
        try:
            browser = pw.chromium.connect_over_cdp("http://127.0.0.1:%d" % self.port)
        except Exception as exc:
            pw.stop()
            raise LiepinError("连接调试端口失败：%s" % exc)
        try:
            yield browser
        finally:
            try:
                browser.close()
            except Exception:
                pass
            try:
                pw.stop()
            except Exception:
                pass

    @staticmethod
    def _liepin_page(browser):
        for ctx in browser.contexts:
            for pg in ctx.pages:
                url = pg.url or ""
                if url.startswith("https://lpt.liepin.com") and not url.startswith(DEAD_URL_PREFIXES):
                    return pg
        return None

    def _with_page(self, fn, *, need_search_box: bool = False, tries: int = 2):
        """打开连接 -> 找到可用页面（必要时重启窗口）-> 执行 fn(page) -> 关闭连接。"""
        last = ""
        for attempt in range(tries + 1):
            if not self.cdp_alive():
                self.start_browser(settle=22.0)
            with self._connect() as browser:
                pg = self._liepin_page(browser)
                if pg is not None:
                    ready = True
                    if need_search_box:
                        ready = False
                        for _ in range(20):
                            try:
                                if pg.locator(SEARCH_BOX).count() > 0:
                                    ready = True
                                    break
                            except Exception:
                                break
                            pg.wait_for_timeout(1000)
                    if ready:
                        return fn(pg)
                    last = "页面还没加载好"
                else:
                    last = "没有猎聘页面"
            if attempt >= tries:
                break
            time.sleep(2)
            self.restart_browser()
        raise LiepinError("猎聘页面不可用（%s）" % (last or "多次重启后仍拿不到页面"))

    # ---------- 能力 ----------
    def status(self) -> Dict[str, Any]:
        return {"ok": True, "cdp_alive": self.cdp_alive(), "cdp_port": self.port,
                "chromium": str(self.chrome) if self.chrome else None,
                "profile_dir": str(self.profile_dir),
                "ledger": {k: len(v) for k, v in self.ledger().items() if isinstance(v, list)}}

    def cookies(self) -> Dict[str, str]:
        """读猎聘 cookie（带 120s 缓存）。"""
        now = time.time()
        if _COOKIE_CACHE["jar"] and now - float(_COOKIE_CACHE["ts"] or 0) < COOKIE_TTL:
            return dict(_COOKIE_CACHE["jar"])

        def _read(pg):
            cdp = pg.context.new_cdp_session(pg)
            data = cdp.send("Network.getCookies", {"urls": ["https://api-lpt.liepin.com", "https://lpt.liepin.com"]})
            return {c["name"]: c["value"] for c in data.get("cookies", [])}

        jar = self._with_page(_read, need_search_box=False, tries=1)
        if not jar:
            raise LiepinError("拿不到猎聘 cookie：请先点「启动浏览器」扫码登录")
        _COOKIE_CACHE["jar"] = jar
        _COOKIE_CACHE["ts"] = now
        return dict(jar)

    def login_state(self) -> Dict[str, Any]:
        """只读检查登录态：不重启窗口；先等页面加载，再判断是否登录。"""
        if not self.cdp_alive():
            return {"logged_in": False, "cdp_alive": False,
                    "note": "浏览器未启动，先点「启动浏览器」（会打开正常 Chrome 供扫码登录）"}

        def _check(pg):
            body = pg.evaluate("() => document.body.innerText.slice(0, 800)")
            url = pg.url or ""
            has_box = pg.locator(SEARCH_BOX).count() > 0
            account = _guess_account(body)
            need_login = ("搜索人才" not in body and "登录" in body) or "/login" in url
            return {"logged_in": bool(has_box or account) and not need_login, "url": url,
                    "account": account, "need_scan": bool(need_login),
                    "hint": body.replace("\n", " | ")[:160]}

        try:
            return self._with_page(_check, need_search_box=False, tries=1)
        except LiepinError as exc:
            return {"logged_in": False, "cdp_alive": True, "note": str(exc)[:160]}

    def account_name(self) -> str:
        try:
            return self._with_page(lambda pg: _guess_account(pg.evaluate("() => document.body.innerText.slice(0, 800)")),
                                   tries=1) or ""
        except Exception:
            return ""

    def search(self, query: str, limit: int = 20, retries: int = 3) -> Dict[str, Any]:
        """搜索人才并返回结构化卡片（浏览器路径；协议路径见 liepin_protocol）。"""
        last_err = ""
        for attempt in range(retries):
            try:
                def _do(pg):
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
                        pg.wait_for_timeout(1200)
                    body = pg.evaluate("() => document.body.innerText")
                    if "岁" not in body:
                        raise LiepinError("搜索结果为空（可能被风控置空）")
                    total = (re.search(r"共有[^\n]{0,20}", body) or [""])[0]
                    return {"total": total, "cards": self._read_cards(pg, limit)}

                res = self._with_page(_do, need_search_box=True, tries=1)
                self.record("searches", {"query": query, "total": res.get("total"), "count": len(res["cards"])})
                return {"ok": True, "url": SEARCH_URL, "total": res.get("total"),
                        "count": len(res["cards"]), "cards": res["cards"]}
            except Exception as exc:
                last_err = str(exc)[:200]
                try:
                    self.restart_browser()
                except Exception:
                    pass
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
                out.push({t, hasAction: /立即沟通|继续沟通|打招呼/.test(t), area: Math.round(r.width * r.height)});
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

    def chat_list(self, limit: int = 20) -> Dict[str, Any]:
        def _do(pg):
            rows = pg.evaluate(
                """(limit) => Array.from(document.querySelectorAll('div,li'))
                     .filter(e => /(先生|女士|\\d{2}岁)/.test(e.innerText || '')
                        && e.getBoundingClientRect().width > 150 && e.getBoundingClientRect().width < 560)
                     .slice(0, limit)
                     .map(e => (e.innerText || '').replace(/\\s*\\n\\s*/g, ' | ').trim().slice(0, 200))""", limit)
            return {"ok": True, "url": pg.url, "conversations": rows or []}
        try:
            return self._with_page(_do, tries=1)
        except LiepinError as exc:
            return {"ok": False, "error": str(exc)[:160]}

    def chat_send(self, target: str, text: str, confirm: bool = False) -> Dict[str, Any]:
        if not confirm:
            return {"ok": False, "error": "写操作需 confirm=True（发送消息会真实触达候选人）"}
        if not text or not text.strip():
            return {"ok": False, "error": "消息内容为空"}
        led = self.ledger()
        for item in led.get("messages", []):
            if item.get("target") == target and item.get("text") == text:
                return {"ok": False, "error": "已发送过相同内容，跳过（防重复）", "ledger": item}

        def _do(pg):
            opened = pg.evaluate(
                """(target) => {
                  const rows = Array.from(document.querySelectorAll('div,li'))
                    .filter(e => (e.innerText || '').includes(target)
                       && e.getBoundingClientRect().width > 150 && e.getBoundingClientRect().width < 560);
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
                return {"ok": False, "error": "未找到输入框（页面可能被风控置空，稍后再试）"}
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

        try:
            return self._with_page(_do, tries=1)
        except LiepinError as exc:
            return {"ok": False, "error": str(exc)[:160]}

    def open_detail(self, name: str, age: Optional[int] = None) -> Dict[str, Any]:
        """打开候选人简历详情并抓取可见文本（消耗查看权益，上层强制 confirm=True）。"""
        res = self.search(name, limit=20)
        if not res.get("ok"):
            return {"ok": False, "error": "详情前置搜索失败：%s" % res.get("error")}
        if not any(c.get("name") == name for c in res.get("cards", [])):
            return {"ok": False, "error": "搜索结果里没找到 %s（可能翻页或换词）" % name}

        def _do(pg):
            clicked = pg.evaluate(
                """(args) => {
                  const [name, ageS] = args;
                  const cards = Array.from(document.querySelectorAll('div,li,article,section'))
                    .filter(e => { const t = e.innerText || ''; const r = e.getBoundingClientRect();
                                   return t.includes(name) && (!ageS || t.includes(ageS)) && r.width > 600 && r.width < 1400 && t.length < 1500; });
                  if (!cards.length) return false;
                  cards.sort((a, b) => { const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
                                         return ra.width * ra.height - rb.width * rb.height; });
                  const card = cards[0];
                  const target = Array.from(card.querySelectorAll('a,span,div'))
                    .find(n => (n.innerText || '').trim() === name) || card;
                  target.click();
                  return true;
                }""", [name, ("%d岁" % age) if age else ""])
            if not clicked:
                return {"ok": False, "error": "定位不到该候选人的卡片"}
            pg.wait_for_timeout(6000)
            detail = pg.evaluate(
                """() => {
                  const panel = document.querySelector('[class*=resume-detail],[class*=detail-panel],[class*=drawer],[class*=modal]');
                  const src = panel || document.body;
                  return (src.innerText || '').replace(/\\n{2,}/g, '\\n').slice(0, 6000);
                }""")
            self.record("details", {"name": name, "age": age, "chars": len(detail or "")})
            if not detail or len(detail) < 120:
                return {"ok": False, "error": "详情面板没抓到内容（可能选择器变化或权益不足）", "url": pg.url}
            return {"ok": True, "name": name, "url": pg.url, "text": detail}

        try:
            return self._with_page(_do, need_search_box=True, tries=1)
        except LiepinError as exc:
            return {"ok": False, "error": str(exc)[:160]}
