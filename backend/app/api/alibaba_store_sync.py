"""准备工作②：同步「店铺资料（公司信息）」与「线上产品清单」。

数据来源（2026-09-21 实机探测确认）：
* 店铺资料：https://mycompany.alibaba.com/sp/form/selectBizType.htm （管理公司信息，传统表单可直接解析）
* 产品清单：https://hz-productposting.alibaba.com/product/products_manage.htm
            默认视图 = 营销信息状态「审核通过」（即"只看线上"），行结构：
            <标题> 视频 型号: R36U 分组: Industrial Handhelds ID: 1601948848850 已优化
            商机品 US $375.00 - 415.00/ Unit 大陆仓：10000 审核通过 已上架 国别管控中(原因) 0 服务力 样品 定制力
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from .alibaba_inquiries import _account_lock, _account_or_404, _get_account_page, _goto, _now
from .auth import _ServerUser, get_current_user_for_local
from ..db import get_db
from ..models import AlibabaProduct, AlibabaStoreProfile

logger = logging.getLogger(__name__)
router = APIRouter()

STORE_URL = "https://mycompany.alibaba.com/sp/form/selectBizType.htm"
PRODUCT_URL = "https://hz-productposting.alibaba.com/product/products_manage.htm"
AUDIT_STATUSES = ("审核通过", "审核不通过", "审核中", "草稿")
SHELF_STATUSES = ("已上架", "已下架", "未上架")
PRODUCT_TAGS = ("服务力", "样品", "定制力", "趋势力", "价格力", "新品", "爆品")

_MODEL_RE = re.compile(r"型号\s*[:：]\s*([^\s]+)")
_GROUP_RE = re.compile(r"分组\s*[:：]\s*(.+?)\s*(?:ID\s*[:：]|$)")
_ID_RE = re.compile(r"ID\s*[:：]\s*(\d{6,})")
_PRICE_RE = re.compile(
    r"(?:US\s*)?\$\s*([\d,]+(?:\.\d+)?)\s*(?:-\s*([\d,]+(?:\.\d+)?))?\s*/?\s*([A-Za-z\u4e00-\u9fa5]+)?"
)
_STOCK_RE = re.compile(r"(?:大陆仓|海外仓|总库存|库存)\s*[:：]\s*([\d,]+)")
_EXPOSURE_RE = re.compile(r"(?:已上架|已下架|未上架)\s*(?:国别管控中\(原因\))?\s*(\d{1,9})")

_STORE_LABELS: Dict[str, tuple] = {
    "company_name": ("公司名称",),
    "registered_place": ("公司注册地",),
    "street_address": ("街道地址",),
    "city": ("城市",),
    "province": ("省份",),
    "country": ("国家/地区",),
    "main_category": ("主营一级类目",),
    "main_business": ("主营业务",),
    "register_year": ("公司注册年份",),
    "employees": ("公司员工总数",),
    "website": ("公司网址",),
}


# ------------------------------------------------------------------ 纯解析函数（可单测）

def _to_float(value: str) -> Optional[float]:
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def parse_product_row(text: str) -> Dict[str, Any]:
    """把商品列表一行的文本解析成结构化字段。"""
    body = re.sub(r"\s+", " ", str(text or "")).strip()
    if not body:
        return {}
    out: Dict[str, Any] = {"subject": body.split(" 视频 ")[0].split(" 型号")[0].strip()}

    match = _MODEL_RE.search(body)
    if match:
        out["model_no"] = match.group(1)
    match = _GROUP_RE.search(body)
    if match:
        out["group_name"] = match.group(1).strip()
    match = _ID_RE.search(body)
    if match:
        out["product_id"] = match.group(1)

    for status in AUDIT_STATUSES:
        if status in body:
            out["audit_status"] = status
            break
    for status in SHELF_STATUSES:
        if status in body:
            out["shelf_status"] = status
            break
    for kind in ("商机品", "交易品", "认证品", "半托管商品"):
        if kind in body:
            out["product_type"] = kind
            break

    match = _PRICE_RE.search(body)
    if match:
        low, high, unit = match.group(1), match.group(2), match.group(3)
        out["price_min"] = _to_float(low)
        out["price_max"] = _to_float(high) if high else _to_float(low)
        out["price_unit"] = (unit or "").strip() or None
        out["price_text"] = ("%s - %s / %s" % (low, high, unit)).strip() if high else "%s / %s" % (low, unit)

    match = _STOCK_RE.search(body)
    if match:
        out["stock_text"] = match.group(1)
    match = _EXPOSURE_RE.search(body)
    if match:
        try:
            out["monthly_exposure"] = int(match.group(1))
        except ValueError:
            pass
    out["tags"] = [tag for tag in PRODUCT_TAGS if tag in body]
    if "国别管控中" in body:
        out["note"] = "国别管控中"
    return out


def parse_store_text(text: str) -> Dict[str, Any]:
    """解析「管理公司信息」页的可见文本（标签：值 或 标签/值 分两行）。"""
    raw_lines = [re.sub(r"\s+", " ", line).strip() for line in str(text or "").splitlines()]
    lines = [line for line in raw_lines if line]
    out: Dict[str, Any] = {}
    for field, labels in _STORE_LABELS.items():
        for index, line in enumerate(lines):
            for label in labels:
                if not line.startswith(label):
                    continue
                if "：" in line or ":" in line:
                    value = re.split(r"[:：]", line, 1)[1].strip()
                else:
                    # 有的行是「主营一级类目 消费电子 重选类目」这种没有冒号的写法
                    value = line[len(label):].strip()
                if not value and index + 1 < len(lines):
                    value = lines[index + 1]
                # 去掉页面上的操作词/噪音
                value = re.sub(r"\s*(重选类目|添加|编辑|查看)\s*$", "", value).strip()
                if value:
                    out[field] = value
                break
            if field in out:
                break
    match = re.search(r"信息完整度\s*[:：]?\s*(\d{1,3}\s*%)", " ".join(lines))
    if match:
        out["completeness"] = re.sub(r"\s+", "", match.group(1))
    match = re.search(r"当前(?:已选)?经营模式\s*[:：]\s*([^\n]{2,60})", " ".join(lines))
    if match:
        value = match.group(1).strip()
        value = re.split(r"\s+(?:必填项|已认证信息|基本信息|生产能力)", value)[0].strip()
        if value:
            out["biz_type"] = value
    website = str(out.get("website") or "").strip()
    if not website or "http" not in website or "以http" in website or "点击" in website:
        out.pop("website", None)
    return out


def parse_store_inputs(inputs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """从表单控件取值（bizType / mainCategory / 更多的经营产品 / 员工数 等）。"""
    out: Dict[str, Any] = {}
    more_products: List[str] = []
    for item in inputs or []:
        name = str(item.get("name") or "")
        value = str(item.get("value") or "").strip()
        if not value:
            continue
        if name.startswith("provideProducts"):
            if value.lower() not in {"true", "false", "on", "off"}:
                more_products.append(value)
        elif name == "employeesCount":
            out["employees"] = value
        elif name == "factorySize":
            out["factory_size"] = value
        elif name in {"website", "companyWebsite", "webSite"} or name.lower().endswith("website"):
            if "http" in value:
                out["website"] = value
    if more_products:
        out["more_products"] = sorted(set(more_products))
    return out


# ------------------------------------------------------------------ 抓取

_PRODUCT_ROW_JS = """() => {
  const rows = Array.from(document.querySelectorAll('div.list-item, div[class*="list-item"]'));
  const out = [];
  for (const el of rows) {
    const text = (el.innerText || '').replace(/\\s+/g, ' ').trim();
    if (!text || text.indexOf('ID:') < 0) continue;
    const img = el.querySelector('img[src*="http"]');
    const detail = el.querySelector('a[href*="product-detail"]');
    const edit = el.querySelector('a[href*="publish.htm"]');
    out.push({
      text,
      image_url: img ? img.getAttribute('src') : '',
      detail_url: detail ? detail.getAttribute('href') : '',
      edit_url: edit ? edit.getAttribute('href') : ''
    });
  }
  return out;
}"""

_FIRST_ID_JS = """() => {
  const el = document.querySelector('div.list-item, div[class*="list-item"]');
  if (!el) return '';
  const m = (el.innerText || '').match(/ID\\s*[:：]\\s*(\\d{6,})/);
  return m ? m[1] : '';
}"""

_NEXT_BUTTON_SELECTORS = (
    'button[class*="next"]:not([disabled])',
    '[class*="pagination"] [class*="next"]',
    'li[class*="next"] a',
    '[aria-label*="next" i]',
    'button:has-text("下一页")',
    'a:has-text("下一页")',
)


async def _extract_products_page(page: Any) -> List[Dict[str, Any]]:
    try:
        return await page.evaluate(_PRODUCT_ROW_JS)
    except Exception as exc:
        logger.warning("[ALI-STORE] extract product rows failed: %s", exc)
        return []


async def _first_product_id(page: Any) -> str:
    try:
        return str(await page.evaluate(_FIRST_ID_JS) or "")
    except Exception:
        return ""


async def _click_next_page(page: Any) -> bool:
    for selector in _NEXT_BUTTON_SELECTORS:
        try:
            locator = page.locator(selector).last
            if await locator.count() and await locator.is_visible():
                await locator.click(timeout=4000)
                return True
        except Exception:
            continue
    return False


def _api_url_from_performance(page_entries: List[str]) -> str:
    """页面 resource entries 里带 token 的列表接口 URL。"""
    for url in page_entries or []:
        if "asyQueryProductsList.do" in url:
            return url
    return ""


_API_KEEP_PARAMS = ("ctoken", "_tb_token_", "_csrf_token_", "lang")
_API_FIXED_PARAMS = {
    "status": "approved",          # 只看线上（审核通过）
    "repositoryType": "all",
    "imageType": "all",
    "statisticsType": "month",
}


def clean_product_api_url(url: str, *, page: int = 1, size: int = 50) -> str:
    """只保留 token/语言 + 固定筛选，丢掉页面上可能残留的其它筛选条件。

    2026-09-21 事故：客户端那次同步只入库 50 条（页面当时带着筛选/分页状态，被原样复用），
    所以这里强制重建查询串：只看线上（status=approved）+ 全仓 + 指定 page/size。
    """
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    parts = urlsplit(str(url or ""))
    keep: Dict[str, str] = {}
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key in _API_KEEP_PARAMS and value:
            keep[key] = value
    query = dict(_API_FIXED_PARAMS)
    query.update(keep)
    query["page"] = str(page)
    query["size"] = str(size)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def _with_param(url: str, key: str, value: Any) -> str:
    pattern = re.compile(r"([?&])" + re.escape(key) + r"=[^&]*")
    if pattern.search(url):
        return pattern.sub(lambda m: "%s%s=%s" % (m.group(1), key, value), url, count=1)
    joiner = "&" if "?" in url else "?"
    return "%s%s%s=%s" % (url, joiner, key, value)


def api_item_to_fields(item: Dict[str, Any]) -> Dict[str, Any]:
    """把 asyQueryProductsList.do 的产品对象映射成库表字段。"""
    def _num(*keys: str) -> Optional[float]:
        for key in keys:
            value = item.get(key)
            if value in (None, "", []):
                continue
            try:
                return float(str(value).replace(",", ""))
            except (TypeError, ValueError):
                continue
        return None

    def _int(*keys: str) -> Optional[int]:
        value = _num(*keys)
        return int(value) if value is not None else None

    status = str(item.get("status") or "").strip()
    audit = {"approved": "审核通过", "draft": "草稿", "auditing": "审核中",
             "rejected": "审核不通过", "not_approved": "审核不通过"}.get(status, status)
    display = str(item.get("displayStatus") or item.get("isDisplay") or "").strip().lower()
    shelf = "已上架" if display in {"y", "yes", "true"} else ("已下架" if display else "")
    tags = []
    for tag in item.get("productTagList") or []:
        if isinstance(tag, dict):
            name = tag.get("descMcmsKey") or tag.get("name") or ""
        else:
            name = str(tag)
        label = {"SERVICE_ABILITY_PRODUCT": "服务力"}.get(str(name), str(name))
        if label:
            tags.append(label)
    option = item.get("option") if isinstance(item.get("option"), dict) else {}
    edit_url = str(option.get("editUrl") or "")
    if edit_url.startswith("//"):
        edit_url = "https:" + edit_url
    return {
        "product_id": str(item.get("id") or ""),
        "subject": item.get("subject"),
        "model_no": item.get("redModel"),
        "group_name": item.get("groupName1") or item.get("groupName2") or "",
        "product_type": "商机品" if str(item.get("tradeType") or "") == "sourcingProduct" else (item.get("tradeType") or ""),
        "price_text": item.get("fobPrice") or "",
        "price_min": _num("skuMinPrice"),
        "price_max": _num("skuMaxPrice"),
        "price_unit": item.get("priceUnit"),
        "stock_text": str(item.get("showNum") or ""),
        "audit_status": audit,
        "shelf_status": shelf,
        "monthly_exposure": _int("clickNum"),
        "tags": tags,
        "note": "国别管控中" if item.get("countryRisk") else None,
        "image_url": item.get("absImageUrl") or item.get("absSummImageUrl") or "",
        "detail_url": item.get("detailUrl") or item.get("productDetailUrl") or "",
        "edit_url": edit_url,
        "keyword_text": item.get("keywords") or "",
        "score": _num("ggsNewScore", "finalScore", "qualityScore"),
        "click_num": _int("clickNum"),
        "visitor_cnt": _int("visitorCnt"),
        "fb_num": _int("fbNum"),
        "owner": item.get("ownerMemberName") or "",
        "moq": str(item.get("minOrderQuantity") or ""),
        "second_order_quantity": _int("secondOrderQuantity"),
        "currency": item.get("currencyCode") or "",
        "category_id": _int("categoryId"),
        "country_risk": bool(item.get("countryRisk")),
        "gmt_modified": str(item.get("gmtModified") or ""),
        "raw": {"api_item": item},
    }


async def sync_products_via_api(page: Any, *, size: int = 50, max_pages: int = 40) -> Dict[str, Any]:
    """优先走内部接口（asyQueryProductsList.do：status=approved 即线上），拿全字段。"""
    try:
        entries = await page.evaluate(
            "() => performance.getEntriesByType('resource').map(e => e.name)"
        )
    except Exception:
        entries = []
    url = _api_url_from_performance(entries if isinstance(entries, list) else [])
    if not url:
        return {"items": [], "pages_scanned": 0, "source": "api_url_missing"}
    sizes = [size]
    for fallback in (20, 10):
        if fallback not in sizes:
            sizes.append(fallback)
    items: List[Dict[str, Any]] = []
    seen: set = set()
    pages = 0
    total = None
    diagnostics: List[Dict[str, Any]] = []
    used_size = sizes[0]
    for page_no in range(1, max(1, min(200, max_pages)) + 1):
        data = None
        page_error = ""
        for candidate_size in sizes:
            target = clean_product_api_url(url, page=page_no, size=candidate_size)
            try:
                raw = await page.evaluate(
                    "(u) => fetch(u, {credentials: 'include'}).then(r => r.text())", target
                )
                data = json.loads(str(raw).strip())
                used_size = candidate_size
                break
            except Exception as exc:
                page_error = str(exc)[:200]
                logger.warning("[ALI-STORE] api page %s size %s failed: %s", page_no, candidate_size, exc)
                data = None
        if data is None:
            diagnostics.append({"page": page_no, "error": page_error or "fetch failed"})
            break
        products = data.get("products") if isinstance(data, dict) else None
        if not isinstance(products, list) or not products:
            diagnostics.append({"page": page_no, "len": 0, "done": True})
            break
        pages += 1
        try:
            total = int(data.get("count"))
        except (TypeError, ValueError):
            total = total
        new_on_page = 0
        for item in products:
            fields = api_item_to_fields(item if isinstance(item, dict) else {})
            pid = str(fields.get("product_id") or "")
            if not pid or pid in seen:
                continue
            seen.add(pid)
            new_on_page += 1
            items.append(fields)
        diagnostics.append({
            "page": page_no,
            "size": used_size,
            "len": len(products),
            "new": new_on_page,
            "total": total,
            "first_id": str((products[0] or {}).get("id") or "") if isinstance(products[0], dict) else "",
        })
        if new_on_page == 0:
            # 这一页全是重复：可能 page/size 没生效，换更小的 size 再试一次
            if used_size != sizes[-1]:
                continue
            break
        if total is not None and len(items) >= total:
            break
    return {
        "items": items,
        "pages_scanned": pages,
        "source": "api",
        "total": total,
        "page_size": used_size,
        "pages": diagnostics[:60],
    }


async def sync_products(page: Any, *, max_pages: int = 40, page_wait: float = 2.6) -> Dict[str, Any]:
    """抓线上产品：优先内部接口，失败时回退到列表 DOM 逐页抓。"""
    await _goto(page, PRODUCT_URL)
    await asyncio.sleep(6.0)
    api_result = await sync_products_via_api(page, max_pages=max_pages)
    if api_result.get("items"):
        return api_result
    items: List[Dict[str, Any]] = []
    seen_ids: set = set()
    pages_scanned = 0
    for _ in range(max(1, min(200, max_pages))):
        rows = await _extract_products_page(page)
        pages_scanned += 1
        new_on_page = 0
        for row in rows:
            parsed = parse_product_row(row.get("text") or "")
            pid = str(parsed.get("product_id") or "")
            if not pid or pid in seen_ids:
                continue
            seen_ids.add(pid)
            new_on_page += 1
            parsed.update({
                "product_id": pid,
                "image_url": row.get("image_url") or "",
                "detail_url": row.get("detail_url") or "",
                "edit_url": row.get("edit_url") or "",
                "raw": {"row_text": (row.get("text") or "")[:800]},
            })
            items.append(parsed)
        if new_on_page == 0:
            break
        before = await _first_product_id(page)
        if not await _click_next_page(page):
            break
        await asyncio.sleep(page_wait)
        after = await _first_product_id(page)
        if before and after and before == after:
            break
    return {"items": items, "pages_scanned": pages_scanned}


async def sync_store_profile(page: Any) -> Dict[str, Any]:
    """抓「管理公司信息」：文本字段 + 表单控件值 + 线上店铺链接。"""
    await _goto(page, STORE_URL)
    await asyncio.sleep(4.0)
    payload = await page.evaluate("""() => {
      const text = (document.body.innerText || '');
      const inputs = Array.from(document.querySelectorAll('input, select, textarea')).map(el => ({
        name: el.getAttribute('name') || el.id || '',
        value: (el.value || el.getAttribute('value') || '').toString().slice(0, 200)
      }));
      const links = Array.from(document.querySelectorAll('a[href]'))
        .map(a => ({t: (a.innerText || '').trim(), h: a.getAttribute('href') || ''}))
        .filter(x => x.t && x.h).slice(0, 200);
      return {text, inputs, links};
    }""")
    profile = parse_store_text(payload.get("text") or "")
    profile.update(parse_store_inputs(payload.get("inputs") or []))
    storefront = ""
    for link in payload.get("links") or []:
        href = str(link.get("h") or "")
        if "en.alibaba.com" in href or "/company_profile" in href:
            storefront = href
            break
    if storefront:
        profile["storefront_url"] = storefront
    # 公司资料页是分页签的，抓一份关键页签的纯文本，方便后续核对
    sections = {"基本信息": re.sub(r"\s+", " ", (payload.get("text") or ""))[:4000]}
    profile["sections"] = sections
    profile["raw"] = {"inputs": (payload.get("inputs") or [])[:80], "links": (payload.get("links") or [])[:60]}
    return profile


# ------------------------------------------------------------------ 落库

def _upsert_products(db: Session, user_id: int, account_id: int, items: List[Dict[str, Any]]) -> Dict[str, int]:
    created = updated = 0
    for item in items:
        pid = str(item.get("product_id") or "").strip()
        if not pid:
            continue
        row = (
            db.query(AlibabaProduct)
            .filter(AlibabaProduct.account_id == account_id, AlibabaProduct.product_id == pid)
            .first()
        )
        fields = {
            "subject": item.get("subject"),
            "model_no": item.get("model_no"),
            "group_name": item.get("group_name"),
            "product_type": item.get("product_type"),
            "price_text": item.get("price_text"),
            "price_min": item.get("price_min"),
            "price_max": item.get("price_max"),
            "price_unit": item.get("price_unit"),
            "stock_text": item.get("stock_text"),
            "audit_status": item.get("audit_status"),
            "shelf_status": item.get("shelf_status"),
            "monthly_exposure": item.get("monthly_exposure"),
            "tags": item.get("tags") or [],
            "note": item.get("note"),
            "image_url": item.get("image_url"),
            "detail_url": item.get("detail_url"),
            "edit_url": item.get("edit_url"),
            "keyword_text": item.get("keyword_text"),
            "score": item.get("score"),
            "click_num": item.get("click_num"),
            "visitor_cnt": item.get("visitor_cnt"),
            "fb_num": item.get("fb_num"),
            "owner": item.get("owner"),
            "moq": item.get("moq"),
            "second_order_quantity": item.get("second_order_quantity"),
            "currency": item.get("currency"),
            "category_id": item.get("category_id"),
            "country_risk": item.get("country_risk"),
            "gmt_modified": item.get("gmt_modified"),
            "raw": item.get("raw") or {},
            "synced_at": _now(),
        }
        if row:
            for key, value in fields.items():
                setattr(row, key, value)
            updated += 1
        else:
            db.add(AlibabaProduct(user_id=user_id, account_id=account_id, product_id=pid, **fields))
            created += 1
    db.commit()
    return {"created": created, "updated": updated}


def _upsert_store_profile(db: Session, user_id: int, account_id: int, profile: Dict[str, Any]) -> None:
    row = (
        db.query(AlibabaStoreProfile)
        .filter(AlibabaStoreProfile.account_id == account_id)
        .first()
    )
    fields = {
        "company_name": profile.get("company_name"),
        "registered_place": profile.get("registered_place"),
        "street_address": profile.get("street_address"),
        "city": profile.get("city"),
        "province": profile.get("province"),
        "country": profile.get("country"),
        "biz_type": profile.get("biz_type"),
        "main_category": profile.get("main_category"),
        "main_business": profile.get("main_business"),
        "more_products": profile.get("more_products") or [],
        "register_year": profile.get("register_year"),
        "employees": profile.get("employees"),
        "factory_size": profile.get("factory_size"),
        "website": profile.get("website"),
        "completeness": profile.get("completeness"),
        "storefront_url": profile.get("storefront_url"),
        "sections": profile.get("sections") or {},
        "raw": profile.get("raw") or {},
        "synced_at": _now(),
    }
    if row:
        for key, value in fields.items():
            setattr(row, key, value)
    else:
        db.add(AlibabaStoreProfile(user_id=user_id, account_id=account_id, **fields))
    db.commit()


# ------------------------------------------------------------------ 序列化

def serialize_store(row: Optional[AlibabaStoreProfile]) -> Optional[Dict[str, Any]]:
    if not row:
        return None
    return {
        "company_name": row.company_name,
        "registered_place": row.registered_place,
        "street_address": row.street_address,
        "city": row.city,
        "province": row.province,
        "country": row.country,
        "biz_type": row.biz_type,
        "main_category": row.main_category,
        "main_business": row.main_business,
        "more_products": row.more_products if isinstance(row.more_products, list) else [],
        "register_year": row.register_year,
        "employees": row.employees,
        "factory_size": row.factory_size,
        "website": row.website,
        "completeness": row.completeness,
        "storefront_url": row.storefront_url,
        "sections": row.sections if isinstance(row.sections, dict) else {},
        "synced_at": row.synced_at.isoformat() if row.synced_at else None,
    }


def serialize_product(row: AlibabaProduct) -> Dict[str, Any]:
    return {
        "product_id": row.product_id,
        "subject": row.subject,
        "model_no": row.model_no,
        "group_name": row.group_name,
        "product_type": row.product_type,
        "price_text": row.price_text,
        "price_min": row.price_min,
        "price_max": row.price_max,
        "price_unit": row.price_unit,
        "stock_text": row.stock_text,
        "audit_status": row.audit_status,
        "shelf_status": row.shelf_status,
        "monthly_exposure": row.monthly_exposure,
        "tags": row.tags if isinstance(row.tags, list) else [],
        "note": row.note,
        "image_url": row.image_url,
        "detail_url": row.detail_url,
        "edit_url": row.edit_url,
        "keywords": row.keyword_text,
        "score": row.score,
        "click_num": row.click_num,
        "visitor_cnt": row.visitor_cnt,
        "fb_num": row.fb_num,
        "owner": row.owner,
        "moq": row.moq,
        "second_order_quantity": row.second_order_quantity,
        "currency": row.currency,
        "category_id": row.category_id,
        "country_risk": row.country_risk,
        "gmt_modified": row.gmt_modified,
        "synced_at": row.synced_at.isoformat() if row.synced_at else None,
    }


# ------------------------------------------------------------------ 接口

class StoreSyncBody(BaseModel):
    products: bool = True
    store: bool = True
    max_pages: int = Field(default=40, ge=1, le=200)


@router.get("/api/alibaba-inquiries/accounts/{account_id}/store", summary="店铺资料与产品统计")
def get_store(
    account_id: int,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    _account_or_404(db, current_user.id, account_id)
    row = (
        db.query(AlibabaStoreProfile)
        .filter(AlibabaStoreProfile.user_id == current_user.id, AlibabaStoreProfile.account_id == account_id)
        .first()
    )
    total = (
        db.query(func.count(AlibabaProduct.id))
        .filter(AlibabaProduct.user_id == current_user.id, AlibabaProduct.account_id == account_id)
        .scalar()
        or 0
    )
    online = (
        db.query(func.count(AlibabaProduct.id))
        .filter(
            AlibabaProduct.user_id == current_user.id,
            AlibabaProduct.account_id == account_id,
            or_(AlibabaProduct.shelf_status == "已上架", AlibabaProduct.audit_status == "审核通过"),
        )
        .scalar()
        or 0
    )
    groups = [
        {"name": name, "count": count}
        for name, count in (
            db.query(AlibabaProduct.group_name, func.count(AlibabaProduct.id))
            .filter(AlibabaProduct.account_id == account_id)
            .group_by(AlibabaProduct.group_name)
            .order_by(func.count(AlibabaProduct.id).desc())
            .limit(12)
            .all()
        )
    ]
    return {"ok": True, "store": serialize_store(row), "stats": {"products": int(total), "online": int(online)}, "groups": groups}


@router.get("/api/alibaba-inquiries/accounts/{account_id}/products", summary="线上产品清单")
def list_products(
    account_id: int,
    q: str = "",
    audit_status: str = "",
    shelf_status: str = "",
    group_name: str = "",
    limit: int = 20,
    offset: int = 0,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    _account_or_404(db, current_user.id, account_id)
    query = db.query(AlibabaProduct).filter(
        AlibabaProduct.user_id == current_user.id, AlibabaProduct.account_id == account_id
    )
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                AlibabaProduct.subject.ilike(like),
                AlibabaProduct.model_no.ilike(like),
                AlibabaProduct.group_name.ilike(like),
            )
        )
    if audit_status:
        query = query.filter(AlibabaProduct.audit_status == audit_status)
    if shelf_status:
        query = query.filter(AlibabaProduct.shelf_status == shelf_status)
    if group_name:
        query = query.filter(AlibabaProduct.group_name == group_name)
    total = query.with_entities(func.count(AlibabaProduct.id)).scalar() or 0
    rows = (
        query.order_by(AlibabaProduct.monthly_exposure.desc().nullslast(), AlibabaProduct.id.desc())
        .offset(max(0, offset))
        .limit(max(1, min(200, limit)))
        .all()
    )
    return {"ok": True, "products": [serialize_product(r) for r in rows], "total": int(total)}


@router.post("/api/alibaba-inquiries/accounts/{account_id}/store/sync", summary="同步店铺资料与线上产品")
async def sync_store(
    account_id: int,
    body: StoreSyncBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    acct = _account_or_404(db, current_user.id, account_id)
    lock = await _account_lock(account_id, "sync")
    result: Dict[str, Any] = {"ok": True, "store": None, "products": None}
    async with lock:
        page = await _get_account_page(acct, visible=False)
        if body.store:
            try:
                profile = await sync_store_profile(page)
                _upsert_store_profile(db, current_user.id, account_id, profile)
                result["store"] = {k: v for k, v in profile.items() if k not in {"raw", "sections"}}
            except Exception as exc:
                logger.warning("[ALI-STORE] store sync failed: %s", exc)
                result["store_error"] = str(exc)[:300]
        if body.products:
            try:
                harvested = await sync_products(page, max_pages=body.max_pages)
                counts = _upsert_products(db, current_user.id, account_id, harvested["items"])
                result["products"] = {
                    "found": len(harvested["items"]),
                    "pages_scanned": harvested["pages_scanned"],
                    "source": harvested.get("source", "dom"),
                    "reported_total": harvested.get("total"),
                    "page_size": harvested.get("page_size"),
                    "pages": harvested.get("pages") or [],
                    "created": counts["created"],
                    "updated": counts["updated"],
                    "samples": harvested["items"][:3],
                }
            except Exception as exc:
                logger.warning("[ALI-STORE] product sync failed: %s", exc)
                result["products_error"] = str(exc)[:300]
    return result
