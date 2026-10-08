# -*- coding: utf-8 -*-
"""猎聘技能的能力分发层：把「能力 id + 参数」翻译成浏览器动作。

能力清单（同时登记在 skill_registry.json）：
  liepin.browser.open      {action: start|restart|status}
  liepin.login.status      {}
  liepin.candidates.search {query, pages?, limit?, filters?, mode?}
  liepin.candidates.suggest {keyword}
  liepin.candidates.detail {name, age?, confirm}
  liepin.session.info      {}                      # 账号/权益额度/未读/新招呼
  liepin.chat.list         {page, page_size}      # 沟通会话列表（分页）
  liepin.applications.list {page, page_size}      # 求职者投递列表（分页）
  liepin.chat.send         {target, text, confirm}
  liepin.ledger.read       {kind?}
  liepin.report.export     {rows, format, filename}
"""
from __future__ import annotations

import csv
import io
import json
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional

from .liepin_browser import LiepinError, LiepinSession, parse_card_text

_SESSION: Optional[LiepinSession] = None


def session() -> LiepinSession:
    global _SESSION
    if _SESSION is None:
        _SESSION = LiepinSession()
    return _SESSION


def _filter_cards(cards, filters: Optional[Dict[str, Any]]):
    if not filters:
        return cards
    out = []
    must = filters.get("must_include") or []
    cities = filters.get("cities") or []
    min_years = filters.get("min_years")
    for c in cards:
        blob = c.get("raw", "")
        if must and not all(k in blob for k in must):
            continue
        if cities and not any(city in (c.get("city") or "") + blob for city in cities):
            continue
        if min_years and (c.get("years") or 0) < int(min_years):
            continue
        out.append(c)
    return out


def run(action: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    params = params or {}
    sess = session()
    try:
        if action == "liepin.browser.open":
            mode = (params.get("action") or "start").lower()
            if mode == "status":
                return sess.status()
            if mode == "restart":
                sess.restart_browser()
            elif not sess.cdp_alive():
                sess.start_browser()
            state = sess.login_state()
            return {"ok": True, "browser": sess.status(), "login": state,
                    "note": "若未登录，请在打开的窗口里扫码/账号登录后再继续"}

        if action == "liepin.login.status":
            return {"ok": True, **sess.login_state()}

        if action == "liepin.candidates.search":
            query = (params.get("query") or "").strip()
            limit = int(params.get("limit") or 20)
            pages = int(params.get("pages") or 1)
            filters = params.get("filters") or {}
            mode = (params.get("mode") or "auto").lower()   # auto / protocol / browser
            if mode in ("auto", "protocol"):
                proto_err = ""
                try:
                    from .liepin_protocol import PROTO_FILTER_KEYS, LiepinProtocol, to_card

                    proto = LiepinProtocol(port=int(params.get("cdp_port") or 9222))
                    kw = {k: v for k, v in filters.items() if k in PROTO_FILTER_KEYS}
                    page_no = int(params.get("page") or 0)
                    if page_no > 0 or pages <= 1:
                        one = proto.search(query, page=page_no, **kw)
                        cards = _filter_cards([to_card(c) for c in one.get("list") or []], filters)
                        total, has_more = one.get("total"), bool(one.get("list"))
                        return {"ok": True, "mode": "protocol", "query": query, "total": total,
                                "page": page_no, "page_size": len(cards), "has_more": has_more,
                                "count": len(cards), "cards": cards[:limit]}
                    res = proto.search_cards(query, pages=pages, **kw)
                    cards = _filter_cards(res["cards"], filters)
                    return {"ok": True, "mode": "protocol", "query": query, "total": res.get("total"),
                            "page": 0, "pages": res.get("pages"), "count": len(cards), "cards": cards[:limit]}
                except Exception as exc:
                    proto_err = "%s: %s" % (type(exc).__name__, exc)
                    if mode == "protocol":
                        return {"ok": False, "mode": "protocol", "error": proto_err,
                                "hint": "协议直连需要本机已登录的 Chrome（先 liepin.browser.open 扫码登录）"}
            if not query:
                return {"ok": False, "error": "缺少 query（如：东南亚 销售总监 电表）"}
            res = sess.search(query, limit=max(limit, 20))
            if not res.get("ok"):
                res["protocol_error"] = locals().get("proto_err", "")
                return res
            cards = _filter_cards(res["cards"], filters)
            return {"ok": True, "mode": "browser", "query": query, "total": res.get("total"),
                    "count": len(cards), "cards": cards[:limit]}

        if action == "liepin.candidates.suggest":
            keyword = (params.get("keyword") or "").strip()
            if not keyword:
                return {"ok": False, "error": "缺少 keyword"}
            from .liepin_protocol import LiepinProtocol
            return {"ok": True, "keyword": keyword, "suggestions": LiepinProtocol().suggest(keyword)}

        if action == "liepin.candidates.detail":
            name = (params.get("name") or "").strip()
            if not name:
                return {"ok": False, "error": "缺少 name"}
            if not params.get("confirm"):
                return {"ok": False, "error": "打开简历详情会消耗猎聘查看权益，需 confirm=True"}
            return sess.open_detail(name, params.get("age"))

        if action == "liepin.session.info":
            from .liepin_protocol import LiepinProtocol

            return {"ok": True, **LiepinProtocol(port=int(params.get("cdp_port") or 9222)).session_info()}

        if action == "liepin.chat.list":
            page = int(params.get("page") or 0)
            page_size = int(params.get("page_size") or params.get("limit") or 30)
            mode = (params.get("mode") or "auto").lower()
            if mode in ("auto", "protocol"):
                try:
                    from .liepin_protocol import LiepinProtocol

                    res = LiepinProtocol(port=int(params.get("cdp_port") or 9222)).chat_list(page=page, page_size=page_size)
                    return {"ok": True, "mode": "protocol", **res}
                except Exception as exc:
                    if mode == "protocol":
                        return {"ok": False, "mode": "protocol", "error": str(exc)[:200]}
                    proto_err = str(exc)[:200]
            res = sess.chat_list(limit=page_size)
            res["mode"] = "browser"
            res["protocol_error"] = locals().get("proto_err", "")
            return res

        if action == "liepin.applications.list":
            from .liepin_protocol import LiepinProtocol

            return {"ok": True, **LiepinProtocol(port=int(params.get("cdp_port") or 9222)).applications(
                page=int(params.get("page") or 0), page_size=int(params.get("page_size") or 10))}

        if action == "liepin.chat.send":
            return sess.chat_send((params.get("target") or "").strip(),
                                  (params.get("text") or "").strip(),
                                  confirm=bool(params.get("confirm")))

        if action == "liepin.ledger.read":
            data = sess.ledger()
            kind = params.get("kind")
            return {"ok": True, kind: data.get(kind, []) if kind else data}

        if action == "liepin.report.export":
            return export_rows(params.get("rows") or [], params.get("format") or "csv",
                               params.get("filename") or ("猎聘候选人_%s.csv" % time.strftime("%Y%m%d_%H%M")))

        return {"ok": False, "error": "未知能力：%s" % action}
    except LiepinError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # pragma: no cover - 兜底
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}


def export_rows(rows, fmt: str, filename: str) -> Dict[str, Any]:
    if not rows:
        return {"ok": False, "error": "rows 为空"}
    cols = ["name", "age", "years", "edu", "city", "expect", "industry", "companies", "active", "raw"]
    out_dir = session().runtime_dir / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / filename
    header = ["姓名", "年龄", "工作年限", "学历", "现居", "期望职位/薪资", "行业", "履历", "活跃", "原始文本"]
    if fmt == "xlsx":
        try:
            from openpyxl import Workbook  # 客户端已带 openpyxl
            wb = Workbook()
            ws = wb.active
            ws.append(header)
            for r in rows:
                ws.append([_cell(r, c) for c in cols])
            path = path.with_suffix(".xlsx")
            wb.save(path)
            return {"ok": True, "path": str(path), "count": len(rows)}
        except Exception as exc:
            fmt = "csv"
            path = path.with_suffix(".csv")
    with io.open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        for r in rows:
            w.writerow([_cell(r, c) for c in cols])
    return {"ok": True, "path": str(path), "count": len(rows), "format": fmt}


def _cell(row: Dict[str, Any], col: str) -> str:
    v = row.get(col)
    if isinstance(v, list):
        return " ／ ".join("%s %s" % (x.get("company", ""), x.get("position", "")) for x in v if isinstance(x, dict))
    return "" if v is None else str(v)
