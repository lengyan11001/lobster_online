# -*- coding: utf-8 -*-
"""猎聘「协议直连」客户端：不驱动浏览器界面，直接用登录态调 api-lpt.liepin.com 的接口。

已实测（2026-10-08）：
  POST api-lpt.liepin.com/api/com.liepin.searchfront4r.b.search
       form: cvSearchConditionInputVo=<json 59 字段>&logForm=<json>
       头: X-Fscp-Version: 1.1 / X-Fscp-Std-Info: {"client_id":"40156"} / X-Client-Type: web ...（**不要**带 X-Fscp-Bi-Stat，带了会 400）
  → {"flag":1,"data":{"cvSearchResultForm":{"totalCount":510,"searchKey":"...","cvSearchListFormList":[...]}}}
  候选人字段：resName / resSex / ageShow / workYearsShow / resDqName / wantJobTitle / wantSalary /
              eduExpList[] / workExpList[] / tags[] / activeStatus / resIdEncode / resumeUrl / imId / chat

登录态来源：本机已登录的 Chrome（CDP 读 cookie）。所以流程仍是「先扫码登录，再跑协议」。
"""
from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any, Dict, List, Optional

import requests

API_BASE = "https://api-lpt.liepin.com/api/"
SEARCH_API = API_BASE + "com.liepin.searchfront4r.b.search"
SUGGEST_API = API_BASE + "com.liepin.searchfront4r.b.get-suggest"
CHAT_UNREAD_API = API_BASE + "com.liepin.im.b.chat.unread-count"
IM_LOGIN_USER_API = API_BASE + "com.liepin.im.common.login-user-info"
IM_NEW_HELLO_API = API_BASE + "com.liepin.im.contact.get-new-hello"
IM_CONTACT_LIST_API = API_BASE + "com.liepin.im.b.contact.get-contact-list"
PRIVILEGE_API = API_BASE + "com.liepin.privilege.bpc.index.user-privilege"
APPLY_LIST_API = API_BASE + "com.liepin.rapply.platform.e.serch-by-usere.v2"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/145.0.0.0 Safari/537.36")
CLIENT_ID = "40156"

# 城市/地区代码（接口里 dqs 用代码；已知常用值，未知的直接传代码）
DQ_CODES = {
    "印度尼西亚": "350150", "印尼": "350150", "雅加达": "350150",
    "越南": "350", "马来西亚": "350070", "新加坡": "050090", "菲律宾": "050040",
    "泰国": "350150", "东南亚": "350",
}
PROTO_FILTER_KEYS = {"dqs", "want_dqs", "jobtitles", "company", "workyears", "edu_levels",
                     "language", "age", "sex", "active_status", "manage_exp", "title_keys"}

DEFAULT_CONDITION: Dict[str, Any] = {
    "suggestKey": "", "searchRefer": "1", "cvSearchForm": 0, "searchKey": "", "filterKey": "", "degrade": "",
    "csCreateTimeFlag": "", "csCreateTime": "", "csId": "", "curPage": 0, "keys": "", "showKeys": "",
    "searchLevel": "", "dqs": "", "wantDqs": "", "workyears": "0,99", "eduLevels": [], "industrys": "",
    "jobtitles": "", "wantIndustrys": "", "wantJobTitles": "", "activeStatus": "", "userStatus": "",
    "yearSalarylow": "", "yearSalaryhigh": "", "wantYearSalaryLow": "", "wantYearSalaryHigh": "", "sex": "",
    "age": "", "special": "", "sortflag": "", "abroadEdu": "", "abroadExp": "", "manageExp": "",
    "language_skills": "", "language_content": "", "filterRead": "", "filterChat": "", "filterDownload": "",
    "lastWork": "", "titleKeys": "", "titleSearchFilter": "0", "companyKeys": "", "compSearchFilter": "0",
    "interactiveVersion": "v2", "jobId": "", "pubJobTitle": "", "schoolKindList": [], "graduationYearList": [],
    "eduLevelTzCode": "", "resLanguage": "", "pushId": "", "jobStability": "", "school": "",
    "smartReadToken": "", "taskId": "", "searchBatchId": "", "aimultikeyId": "", "keysRelation": "",
}


class LiepinProtocolError(RuntimeError):
    pass


def cookies_from_browser(port: int = 9222) -> Dict[str, str]:
    """从本机已登录的 Chrome 里读猎聘 cookie（走 CDP，不碰用户密码）。"""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp("http://127.0.0.1:%d" % port)
        ctx = browser.contexts[0]
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        cdp = ctx.new_cdp_session(page)
        ck = cdp.send("Network.getCookies", {"urls": ["https://api-lpt.liepin.com", "https://lpt.liepin.com"]})
        jar = {c["name"]: c["value"] for c in ck.get("cookies", [])}
    if not jar:
        raise LiepinProtocolError("拿不到猎聘 cookie：请先在技能页点「启动浏览器」并扫码登录")
    return jar


class LiepinProtocol:
    """协议直连客户端（无浏览器界面操作）。"""

    def __init__(self, cookies: Optional[Dict[str, str]] = None, port: int = 9222, timeout: int = 25):
        self.port = port
        self.timeout = timeout
        self.cookies = cookies or cookies_from_browser(port)
        self.session = requests.Session()
        self.session.cookies.update(self.cookies)
        self.last_search: Dict[str, Any] = {}

    # ---------- 基础 ----------
    def _headers(self, bi_location: str = "https://lpt.liepin.com/search") -> Dict[str, str]:
        return {
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Requested-With": "XMLHttpRequest",
            "X-Client-Type": "web",
            "X-Fscp-Version": "1.1",
            "X-Fscp-Std-Info": json.dumps({"client_id": CLIENT_ID}),
            "X-Fscp-Trace-Id": str(uuid.uuid4()),
            "Referer": bi_location,
            "User-Agent": UA,
        }

    def _post(self, url: str, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            resp = self.session.post(url, data=data, headers=self._headers(), timeout=self.timeout)
        except Exception as exc:
            raise LiepinProtocolError("请求失败：%s" % exc)
        if resp.status_code != 200:
            raise LiepinProtocolError("HTTP %s：%s" % (resp.status_code, resp.text[:120]))
        try:
            payload = resp.json()
        except Exception:
            raise LiepinProtocolError("返回不是 JSON：%s" % resp.text[:120])
        if payload.get("flag") != 1:
            raise LiepinProtocolError("接口返回错误：%s %s" % (payload.get("code"), payload.get("msg")))
        return payload

    # ---------- 能力 ----------
    def suggest(self, keyword: str) -> List[str]:
        payload = self._post(SUGGEST_API, {"keyword": keyword, "version": "v2"})
        return [x.get("keyword", "") for x in (payload.get("data") or {}).get("cvSuggestList", [])]

    def unread_count(self) -> Any:
        try:
            return self._post(CHAT_UNREAD_API, {}).get("data")
        except Exception:
            return None

    # ---------- IM / 沟通（协议） ----------
    def session_info(self) -> Dict[str, Any]:
        """登录账号信息 + 权益/额度 + 未读 + 新招呼（都是只读接口）。"""
        out: Dict[str, Any] = {"mode": "protocol"}
        try:
            info = self._post(IM_LOGIN_USER_API, {"imId": "", "imApp": "1"})
            d = info.get("data") or {}
            out.update({"user_name": d.get("userNameShow"), "im_user_id": d.get("userId")})
        except Exception as exc:
            out["login_error"] = str(exc)[:120]
        try:
            pr = self._post(PRIVILEGE_API, {})
            d = pr.get("data") or {}
            privs = {p.get("operation"): {"used": p.get("usedCount"), "total": p.get("privilegeCount"),
                                          "reset": p.get("nextResetTime"), "name": p.get("privilegeName")}
                     for p in (d.get("identityPrivilege") or [])}
            out["identity"] = d.get("identity")
            out["expire_time"] = d.get("expireTime")
            out["privileges"] = privs
            out["chat_quota"] = privs.get("b_open_chat")
        except Exception as exc:
            out["privilege_error"] = str(exc)[:120]
        for key, api, data in (("unread", CHAT_UNREAD_API, {"imUserType": "2", "imApp": "1"}),
                               ("new_hello", IM_NEW_HELLO_API, {"imUserType": "2", "imApp": "1"})):
            try:
                r = self._post(api, data)
                val = (r.get("data") or {})
                out[key] = val.get("count") if key == "unread" else val.get("result")
            except Exception:
                out[key] = None
        return out

    def chat_list(self, page: int = 0, page_size: int = 30) -> Dict[str, Any]:
        """沟通会话列表（分页）。"""
        payload = self._post(IM_CONTACT_LIST_API, {"imUserType": "2", "imApp": "1",
                                                   "pageSize": str(page_size), "curPage": str(page)})
        d = payload.get("data") or {}
        rows = []
        for it in d.get("list") or []:
            rows.append({
                "im_id": it.get("oppositeImId") or it.get("imId"),
                "name": it.get("oppositeName") or it.get("name") or it.get("userName"),
                "title": it.get("oppositeTitle") or it.get("title"),
                "company": it.get("oppositeCompany") or it.get("company"),
                "unread": it.get("unReadCnt"),
                "user_type": it.get("imUserType"),
                "time": it.get("lastMessageTime") or it.get("modifyTime"),
                "raw": json.dumps(it, ensure_ascii=False)[:800],
            })
        return {"total": d.get("totalCount"), "page": d.get("curPage"), "page_size": d.get("pageSize"),
                "has_more": d.get("hasMore"), "count": len(rows), "list": rows}

    def applications(self, page: int = 0, page_size: int = 10) -> Dict[str, Any]:
        """求职者投递列表（分页）。"""
        cond = {"applyStatus": 0, "pageSize": int(page_size), "curPage": int(page),
                "applyTimeRange": "1", "filterAiReadNotMatch": True, "jobId": ""}
        payload = self._post(APPLY_LIST_API, {"imId": "", "imApp": "1",
                                              "applyCondition": json.dumps(cond, ensure_ascii=False),
                                              "sfrom": "RES_IM_APPLY_LIST"})
        d = payload.get("data") or {}
        return {"total": d.get("totalCnt"), "page": d.get("curPage"), "count": len(d.get("datas") or []),
                "list": d.get("datas") or []}

    def search(self, keys: str = "", page: int = 0, *, dqs: str = "", want_dqs: str = "",
               jobtitles: str = "", company: str = "", workyears: str = "0,99", edu_levels: Optional[list] = None,
               language: str = "", age: str = "", sex: str = "", active_status: str = "",
               manage_exp: str = "", title_keys: str = "") -> Dict[str, Any]:
        """一页人才搜索（20 条左右）。keys 为关键词，如「东南亚 电表」。"""
        if dqs in DQ_CODES:
            dqs = DQ_CODES[dqs]
        if want_dqs in DQ_CODES:
            want_dqs = DQ_CODES[want_dqs]
        cond = dict(DEFAULT_CONDITION)
        cond.update({
            "keys": keys, "curPage": int(page), "dqs": dqs, "wantDqs": want_dqs, "jobtitles": jobtitles,
            "companyKeys": company, "workyears": workyears, "eduLevels": edu_levels or [],
            "language_skills": language, "age": age, "sex": sex, "activeStatus": active_status,
            "manageExp": manage_exp, "titleKeys": title_keys,
        })
        log_form = json.dumps({"skId": "", "fkId": "", "ckId": "", "searchScene": "button" if page == 0 else "page"})
        payload = self._post(SEARCH_API, {"cvSearchConditionInputVo": json.dumps(cond, ensure_ascii=False),
                                          "logForm": log_form})
        form = ((payload.get("data") or {}).get("cvSearchResultForm") or {})
        self.last_search = {"keys": keys, "page": page, "total": form.get("totalCount"),
                            "searchKey": form.get("searchKey")}
        return {"total": form.get("totalCount"), "page": form.get("curPage"), "keys": keys,
                "search_key": form.get("searchKey"), "list": form.get("cvSearchListFormList") or []}

    def search_cards(self, keys: str = "", pages: int = 1, **filters) -> Dict[str, Any]:
        """连续抓 N 页并映射成与界面卡片一致的结构。"""
        cards, total, page0 = [], None, 0
        for p in range(max(1, int(pages))):
            res = self.search(keys, page=p, **filters)
            if total is None:
                total = res.get("total")
            if not res.get("list"):
                break
            cards.extend(to_card(c) for c in res["list"])
            page0 = p
            time.sleep(0.4)
        return {"ok": True, "mode": "protocol", "keys": keys, "total": total, "pages": page0 + 1,
                "count": len(cards), "cards": cards}


def _int_of(text: Any) -> Optional[int]:
    m = re.search(r"\d+", str(text or ""))
    return int(m.group(0)) if m else None


def to_card(c: Dict[str, Any]) -> Dict[str, Any]:
    """把协议返回的候选人对象映射成与界面解析一致的卡片结构。"""
    works = c.get("workExpList") or []
    edus = c.get("eduExpList") or []
    expect = " ".join([x for x in (c.get("wantJobTitle"), c.get("wantSalary")) if x])
    school = ""
    if edus:
        e0 = edus[0] or {}
        school = " ".join([x for x in (e0.get("schoolName"), e0.get("major"), e0.get("eduDegreeName")) if x])
    return {
        "name": c.get("resName"),
        "sex": c.get("resSex"),
        "age": _int_of(c.get("ageShow")),
        "years": _int_of(c.get("workYearsShow")),
        "edu": c.get("resEdulevelName"),
        "city": c.get("resDqName"),
        "want_city": c.get("wantDq"),
        "expect": expect,
        "industry": (c.get("tags") or [""])[0] if c.get("tags") else "",
        "tags": c.get("tags") or [],
        "companies": [{"company": w.get("compName"),
                       "position": "%s %s-%s" % (w.get("title"), w.get("workStart"), w.get("workEnd"))}
                      for w in works],
        "school": school,
        "active": (c.get("activeStatus") or {}).get("name") or c.get("offLineOrRefreshTime"),
        "in_chat": bool(c.get("chat")),
        "read": bool(c.get("read")),
        "res_id": c.get("resIdEncode"),
        "resume_url": c.get("resumeUrl"),
        "im_id": c.get("imId"),
        "updated": c.get("resModifytime"),
        "raw": json.dumps(c, ensure_ascii=False)[:3000],
    }
