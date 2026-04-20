"""电商商品发布 API：列出店铺账号、打开商品发布页面并自动填充。"""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Asset, EcommerceDetailJob, PublishAccount
from .auth import _ServerUser, get_current_user_for_local
from .publish import BROWSER_DATA_DIR, browser_options_from_publish_meta

logger = logging.getLogger(__name__)
router = APIRouter()

ECOMMERCE_PLATFORMS = {
    "douyin_shop", "xiaohongshu_shop", "alibaba1688", "taobao", "pinduoduo"
}

ECOMMERCE_PLATFORM_NAMES = {
    "douyin_shop": "抖店",
    "xiaohongshu_shop": "小红书店铺",
    "alibaba1688": "1688",
    "taobao": "淘宝",
    "pinduoduo": "拼多多",
}

_BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
_ASSETS_DIR = _BASE_DIR / "assets"


class OpenProductFormBody(BaseModel):
    platform: str = Field(..., description="电商平台 ID")
    account_nickname: Optional[str] = Field(None, description="店铺账号昵称，不传则用该平台第一个账号")
    title: Optional[str] = Field(None, description="商品标题（宝贝标题，淘宝 60 字内）")
    price: Optional[str] = Field(None, description="商品价格")
    category: Optional[str] = Field(None, description="商品类目（中文，旧字段，淘宝优先用 cat_id）")
    main_image_asset_ids: List[str] = Field(default_factory=list, description="主图素材 ID 列表（淘宝 1440x1440 主图）")
    detail_image_asset_ids: List[str] = Field(default_factory=list, description="详情图素材 ID 列表")
    # ── Phase T2：淘宝完整 listing payload ──
    cat_id: Optional[int] = Field(None, description="淘宝类目 ID（淘宝商品创建页 catId 必传）")
    guide_title: Optional[str] = Field(None, description="导购标题（淘宝 30 字内，品牌+品类+利益点）")
    brand: Optional[str] = Field(None, description="品牌；不传且 no_brand=True 时选'无品牌/无注册商标'")
    no_brand: bool = Field(False, description="True 时点选'无品牌/无注册商标'")
    specs: Optional[Dict[str, str]] = Field(None, description="商品属性 {label: value}")
    stock: Optional[int] = Field(None, description="总库存")
    delivery_time: Optional[str] = Field(None, description="发货时间，如 '48小时内发货'")
    delivery_location: Optional[str] = Field(None, description="发货地，如 '浙江/金华'")
    portrait_image_asset_ids: List[str] = Field(default_factory=list, description="竖图（1440x1920）asset ids，淘宝主图区第二组")
    # ── Phase T3：电商详情图全资源（白底 / SKU / 卖点 / hero_claim） ──
    main_square_image_asset_ids: List[str] = Field(default_factory=list, description="1:1 主图（1440x1440）asset ids；不传则用 main_image_asset_ids 兜底")
    white_bg_image_asset_ids: List[str] = Field(default_factory=list, description="白底图 asset ids（淘宝白底图区）")
    sku_image_asset_ids: List[str] = Field(default_factory=list, description="SKU 规格图 asset ids（销售规格区，需先建规格）")
    selling_points: Optional[List[str]] = Field(None, description="5 条卖点文案，会拼到「卖点」/「购买须知」textarea")
    hero_claim: Optional[str] = Field(None, description="主推标语，优先填到「卖点」textarea")
    # ── PDF 完整流程：商机发现入口 ──
    opportunity_id: Optional[int] = Field(None, description="千牛商机发现 opportunityId；传了用 ai/category.htm 入口（PDF 标准）")
    opportunity_type: int = Field(2, description="opportunityType: 2=平台缺货(默认), 14=市场趋势")


def _resolve_asset_to_local_path(db: Session, user_id: int, asset_id: str) -> Optional[str]:
    """将 asset_id 解析为本地文件路径；无本地文件则下载 source_url 到临时文件。"""
    asset = (
        db.query(Asset)
        .filter(Asset.asset_id == asset_id.strip(), Asset.user_id == user_id)
        .first()
    )
    if not asset:
        logger.warning("[ecommerce_publish] asset not found: %s", asset_id)
        return None

    local = _ASSETS_DIR / asset.filename
    if local.exists():
        return str(local)

    url = (asset.source_url or "").strip()
    if not url.startswith(("http://", "https://")):
        logger.warning("[ecommerce_publish] asset %s has no local file and no source_url", asset_id)
        return None

    try:
        ext = Path(asset.filename or "").suffix or ".png"
        with httpx.Client(timeout=60.0, follow_redirects=True) as client:
            r = client.get(url)
            r.raise_for_status()
        fd, tmp_path = tempfile.mkstemp(suffix=ext, prefix="ecom_img_")
        try:
            os.write(fd, r.content)
        finally:
            os.close(fd)
        logger.info("[ecommerce_publish] downloaded asset %s to %s", asset_id, tmp_path)
        return tmp_path
    except Exception as e:
        logger.warning("[ecommerce_publish] download asset %s failed: %s", asset_id, e)
        return None


def _resolve_asset_ids(db: Session, user_id: int, asset_ids: List[str]) -> List[str]:
    """批量解析 asset_id 列表为本地文件路径列表（跳过解析失败的）。"""
    paths = []
    for aid in asset_ids:
        p = _resolve_asset_to_local_path(db, user_id, aid)
        if p:
            paths.append(p)
    return paths


@router.get("/api/ecommerce-publish/platforms", summary="列出支持的电商平台")
def list_ecommerce_platforms():
    return {
        "platforms": [
            {"id": pid, "name": ECOMMERCE_PLATFORM_NAMES.get(pid, pid)}
            for pid in sorted(ECOMMERCE_PLATFORMS)
        ]
    }


@router.get("/api/ecommerce-publish/accounts", summary="列出电商店铺账号")
def list_shop_accounts(
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(PublishAccount)
        .filter(
            PublishAccount.user_id == current_user.id,
            PublishAccount.platform.in_(ECOMMERCE_PLATFORMS),
        )
        .order_by(PublishAccount.platform, PublishAccount.id)
        .all()
    )
    return {
        "accounts": [
            {
                "id": a.id,
                "platform": a.platform,
                "platform_name": ECOMMERCE_PLATFORM_NAMES.get(a.platform, a.platform),
                "nickname": a.nickname,
                "status": a.status,
                "last_login": a.last_login.isoformat() if a.last_login else None,
            }
            for a in rows
        ],
        "platforms": [
            {"id": pid, "name": ECOMMERCE_PLATFORM_NAMES.get(pid, pid)}
            for pid in sorted(ECOMMERCE_PLATFORMS)
        ],
    }


@router.post("/api/ecommerce-publish/open-product-form", summary="打开商品发布页面并自动填充（不提交）")
async def open_product_form(
    body: OpenProductFormBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    if body.platform not in ECOMMERCE_PLATFORMS:
        raise HTTPException(400, detail=f"不支持的电商平台: {body.platform}")

    query = db.query(PublishAccount).filter(
        PublishAccount.user_id == current_user.id,
        PublishAccount.platform == body.platform,
    )
    if body.account_nickname:
        acct = query.filter(PublishAccount.nickname == body.account_nickname.strip()).first()
    else:
        acct = query.first()

    if not acct:
        platform_name = ECOMMERCE_PLATFORM_NAMES.get(body.platform, body.platform)
        raise HTTPException(
            404,
            detail=f"未找到{platform_name}店铺账号，请先在技能商店「商品发布」中添加并登录",
        )

    profile_dir = acct.browser_profile or str(BROWSER_DATA_DIR / f"{acct.platform}_{acct.nickname}")

    main_image_paths = _resolve_asset_ids(db, current_user.id, body.main_image_asset_ids)
    detail_image_paths = _resolve_asset_ids(db, current_user.id, body.detail_image_asset_ids)
    portrait_image_paths = _resolve_asset_ids(db, current_user.id, body.portrait_image_asset_ids)
    main_square_image_paths = _resolve_asset_ids(db, current_user.id, body.main_square_image_asset_ids)
    white_bg_image_paths = _resolve_asset_ids(db, current_user.id, body.white_bg_image_asset_ids)
    sku_image_paths = _resolve_asset_ids(db, current_user.id, body.sku_image_asset_ids)

    temp_files: List[str] = []

    try:
        from publisher.browser_pool import (
            _acquire_context,
            _ensure_visible_interactive_context,
            _get_page_with_reacquire,
            _setup_auto_close,
        )
        from publisher.drivers import DRIVERS

        driver_cls = DRIVERS.get(body.platform)
        if not driver_cls:
            raise HTTPException(400, detail=f"不支持的电商平台驱动: {body.platform}")

        bopts = browser_options_from_publish_meta(acct.meta)
        await _ensure_visible_interactive_context(profile_dir, browser_options=bopts)
        ctx, created_new = await _acquire_context(
            profile_dir, new_headless=False, browser_options=bopts
        )

        page, ctx = await _get_page_with_reacquire(profile_dir, ctx, browser_options=bopts)

        driver = driver_cls()
        login_ok = await driver.check_login(page, navigate=True)
        if not login_ok:
            login_url = driver.login_url()
            try:
                await page.goto(login_url, wait_until="domcontentloaded", timeout=30000)
            except Exception:
                pass
            _setup_auto_close(ctx, profile_dir, page, browser_options=bopts)
            platform_name = ECOMMERCE_PLATFORM_NAMES.get(body.platform, body.platform)
            return {
                "ok": False,
                "need_login": True,
                "message": f"未登录{platform_name}，已打开登录页面，请扫码登录后重试",
                "platform": body.platform,
                "platform_name": platform_name,
                "account_nickname": acct.nickname,
            }

        # 淘宝 driver 接受 Phase T2 全套参数；其他平台 driver 当前只看老参数（向前兼容传不报错）
        driver_kwargs: Dict[str, Any] = dict(
            title=body.title,
            price=body.price,
            category=body.category,
            main_image_paths=main_image_paths or None,
            detail_image_paths=detail_image_paths or None,
        )
        if body.platform == "taobao":
            driver_kwargs.update(
                cat_id=body.cat_id,
                opportunity_id=body.opportunity_id,
                opportunity_type=body.opportunity_type,
                guide_title=body.guide_title,
                brand=body.brand,
                no_brand=body.no_brand,
                specs=body.specs,
                stock=body.stock,
                delivery_time=body.delivery_time,
                delivery_location=body.delivery_location,
                portrait_image_paths=portrait_image_paths or None,
                main_square_image_paths=main_square_image_paths or None,
                white_bg_image_paths=white_bg_image_paths or None,
                sku_image_paths=sku_image_paths or None,
                selling_points=body.selling_points,
                hero_claim=body.hero_claim,
                account_nickname=acct.nickname,
            )
        result = await driver.open_product_form(page, **driver_kwargs)

        _setup_auto_close(ctx, profile_dir, page, browser_options=bopts)

        platform_name = ECOMMERCE_PLATFORM_NAMES.get(body.platform, body.platform)
        return {
            "ok": result.get("ok", False),
            "message": result.get("message", ""),
            "platform": body.platform,
            "platform_name": platform_name,
            "account_nickname": acct.nickname,
            "auto_filled": result.get("auto_filled", []),
            "url": result.get("url", ""),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[ecommerce_publish] open_product_form failed platform=%s", body.platform)
        raise HTTPException(500, detail=f"打开商品发布页失败: {e}")
    finally:
        for tf in temp_files:
            try:
                os.unlink(tf)
            except Exception:
                pass


class PublishFromJobBody(BaseModel):
    job_id: str = Field(..., description="电商详情图 pipeline job_id")
    platform: str = Field("douyin_shop", description="电商平台 ID")
    account_nickname: Optional[str] = Field(None, description="店铺账号昵称")
    title: Optional[str] = Field(None, description="商品标题（不传则用 title_payload.listing_title 或 job.product_name）")
    # ── Phase T2：淘宝完整 listing 字段（不传则从 job.title_payload + saved_assets.analysis 提取）──
    cat_id: Optional[int] = Field(None, description="淘宝类目 ID（不传则用 job.taobao_cate_id.cate_id）")
    price: Optional[str] = Field(None, description="商品价格")
    stock: Optional[int] = Field(None, description="总库存")
    brand: Optional[str] = Field(
        None,
        description="显式品牌；不传则用 job.saved_assets.result.config.brand。两者都为空时不主动选'无品牌'，留给用户审核",
    )
    no_brand: bool = Field(
        False,
        description="True 时由 driver 点选'无品牌/无注册商标'。默认 False=不猜，让用户审核时自己选",
    )
    delivery_time: Optional[str] = Field("48小时内发货", description="发货时间，PDF 默认 48 小时")
    delivery_location: Optional[str] = Field("浙江/金华", description="发货地，PDF 默认 浙江/金华")
    extra_specs: Optional[Dict[str, str]] = Field(None, description="覆盖/补充 specs，会与 job 的 specs 合并")
    # ── PDF 完整流程开关 ──
    use_opportunity: bool = Field(
        True,
        description="True=按 PDF 走商机发现入口；先调 discover-opportunities 拿 opportunityId，再走 ai/category.htm。"
                    "False=直接 v2/publish.htm?catId=xxx（绕过商机发现）",
    )
    opportunity_id: Optional[int] = Field(
        None,
        description="显式传 opportunityId，省去爬虫这一步。优先级 > use_opportunity 触发的爬虫",
    )
    opportunity_type: int = Field(2, description="2=平台缺货(默认), 14=市场趋势")
    opportunity_seed_keyword: Optional[str] = Field(
        None,
        description="商机搜索词；不传则从 job.product_name 自动取",
    )
    opportunity_tab: str = Field("平台缺货", description="千牛 tab")


def _extract_asset_ids_from_suite(saved_assets: Dict[str, Any], category: str) -> List[str]:
    bundle = saved_assets.get("suite_bundle") if isinstance(saved_assets.get("suite_bundle"), dict) else {}
    items = bundle.get(category, [])
    if not isinstance(items, list):
        return []
    return [str(it.get("asset_id")) for it in items if isinstance(it, dict) and it.get("asset_id")]


def _split_main_images_by_dimension(
    db: Session, user_id: int, asset_ids: List[str]
) -> Dict[str, List[str]]:
    """根据 asset.prompt / asset.meta.relative_path 中的尺寸标记
    （'1440X1440' = 1:1 / '1440X1920' = 3:4），把 main_images 拆为方图与竖图。
    若无法判断，整批归到 square 兜底。
    """
    if not asset_ids:
        return {"square": [], "portrait": []}
    square: List[str] = []
    portrait: List[str] = []
    unknown: List[str] = []
    for aid in asset_ids:
        asset = (
            db.query(Asset)
            .filter(Asset.asset_id == aid.strip(), Asset.user_id == user_id)
            .first()
        )
        if not asset:
            unknown.append(aid)
            continue
        prompt = (asset.prompt or "").upper()
        meta_path = ""
        if isinstance(asset.meta, dict):
            meta_path = str(asset.meta.get("relative_path") or "").upper()
        joined = f"{prompt} {meta_path}"
        if "1440X1920" in joined or "1440*1920" in joined:
            portrait.append(aid)
        elif "1440X1440" in joined or "1440*1440" in joined:
            square.append(aid)
        else:
            unknown.append(aid)
    if not square and not portrait:
        square = unknown
    elif unknown:
        # 未知归到方图（淘宝主图区上限 5，会自动截断）
        square.extend(unknown)
    return {"square": square, "portrait": portrait}


def _extract_selling_points(analysis: Dict[str, Any]) -> List[str]:
    """analysis.selling_points 可能是 [{"text": "..."}] 或 ["..."]。"""
    raw = analysis.get("selling_points") if isinstance(analysis, dict) else None
    if not isinstance(raw, list):
        return []
    out: List[str] = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
        elif isinstance(item, dict):
            t = item.get("text") or item.get("title") or item.get("content")
            if isinstance(t, str) and t.strip():
                out.append(t.strip())
    return out


@router.post("/api/ecommerce-publish/from-job", summary="从电商详情图 job 一键打开商品发布页")
async def publish_from_job(
    body: PublishFromJobBody,
    current_user: _ServerUser = Depends(get_current_user_for_local),
    db: Session = Depends(get_db),
):
    from ..services.comfly_ecommerce_detail_job_store import get_job as get_mem_job

    job_id = (body.job_id or "").strip().lower()
    if not job_id:
        raise HTTPException(400, detail="job_id 不能为空")

    saved_assets: Optional[Dict[str, Any]] = None
    product_name: Optional[str] = None
    listing_category: Optional[str] = None

    mem_job = get_mem_job(job_id)
    if mem_job and mem_job.get("status") == "completed":
        saved_assets = mem_job.get("saved_assets")
        result = mem_job.get("result") or {}
        analysis = result.get("analysis") if isinstance(result.get("analysis"), dict) else {}
        product_name = str(analysis.get("product_name") or "").strip()
        listing_category = str(analysis.get("listing_category") or "").strip() or str((result.get("config") or {}).get("listing_category") or "").strip()

    # 收集 Phase T2 需要的扩展数据：title_payload + analysis.specs + taobao_cate_id
    title_payload: Dict[str, Any] = {}
    analysis: Dict[str, Any] = {}
    db_job: Optional[EcommerceDetailJob] = None
    if saved_assets is None:
        db_job = db.query(EcommerceDetailJob).filter(EcommerceDetailJob.job_id == job_id).first()
        if not db_job:
            raise HTTPException(404, detail=f"未找到 job_id={job_id}")
        if db_job.status != "completed":
            raise HTTPException(400, detail=f"Job 尚未完成（状态: {db_job.status}）")
        saved_assets = db_job.saved_assets or {}
        product_name = product_name or db_job.product_name or ""
    else:
        # mem_job 命中时也补一下 db_job 取 title_payload / taobao_cate_id
        db_job = db.query(EcommerceDetailJob).filter(EcommerceDetailJob.job_id == job_id).first()

    # 从 saved_assets.result.analysis 拿 specs / brand
    if isinstance(saved_assets, dict):
        result = saved_assets.get("result") if isinstance(saved_assets.get("result"), dict) else {}
        analysis = result.get("analysis") if isinstance(result.get("analysis"), dict) else {}
        config = result.get("config") if isinstance(result.get("config"), dict) else {}
    else:
        config = {}

    if db_job is not None:
        title_payload = db_job.title_payload if isinstance(db_job.title_payload, dict) else {}

    meta = saved_assets.get("meta") if isinstance(saved_assets, dict) and isinstance(saved_assets.get("meta"), dict) else {}
    listing_category = listing_category or str(meta.get("listing_category") or "").strip() or None

    # ── 图片：完整电商详情图资源分流 ─────
    # main_images 实际是 1:1+3:4 混在一起（按 1440X1440 / 1440X1920 命名拆开）
    raw_main_ids = _extract_asset_ids_from_suite(saved_assets, "main_images")
    split = _split_main_images_by_dimension(db, current_user.id, raw_main_ids)
    main_square_ids = split["square"][:5]  # 淘宝 1:1 主图区上限 5
    portrait_ids_from_main = split["portrait"][:5]

    # 兼容老 skill：如果有显式 main_portrait_images / portrait_images
    explicit_portrait = (
        _extract_asset_ids_from_suite(saved_assets, "main_portrait_images")
        or _extract_asset_ids_from_suite(saved_assets, "portrait_images")
    )
    portrait_ids = (explicit_portrait or portrait_ids_from_main)[:5]

    # 老接口字段保留：main_ids 给非淘宝平台兜底（旧 driver 只看 main_image_paths）
    main_ids = main_square_ids or raw_main_ids[:5]

    # 白底图
    white_bg_ids = _extract_asset_ids_from_suite(saved_assets, "transparent_white_bg")
    if not white_bg_ids:
        white_bg_ids = _extract_asset_ids_from_suite(saved_assets, "white_bg_images")

    # SKU 规格图
    sku_ids = _extract_asset_ids_from_suite(saved_assets, "sku_images")

    # 详情图：detail_images + showcase_images + material_images（合并，所有"详情页"图）
    detail_ids = _extract_asset_ids_from_suite(saved_assets, "detail_images")
    showcase_ids = _extract_asset_ids_from_suite(saved_assets, "showcase_images")
    material_ids = _extract_asset_ids_from_suite(saved_assets, "material_images")
    if not detail_ids:
        pages = saved_assets.get("pages", [])
        if isinstance(pages, list):
            detail_ids = [str(p.get("asset_id")) for p in pages if isinstance(p, dict) and p.get("asset_id")]
    # 合并去重，保持顺序
    seen = set()
    full_detail_ids: List[str] = []
    for lst in (detail_ids, showcase_ids, material_ids):
        for aid in lst:
            if aid and aid not in seen:
                seen.add(aid)
                full_detail_ids.append(aid)
    detail_ids = full_detail_ids

    # 卖点 / hero_claim
    selling_points = _extract_selling_points(analysis)
    hero_claim = ""
    if isinstance(analysis, dict):
        hero_claim = str(analysis.get("hero_claim") or "").strip()

    # ── 标题：优先 title_payload.listing_title > body.title > product_name ──
    title = (
        body.title
        or (title_payload.get("listing_title") if isinstance(title_payload, dict) else None)
        or product_name
        or None
    )
    guide_title = (title_payload.get("guide_title") if isinstance(title_payload, dict) else None) or None

    # ── 类目 ID：body.cat_id > job.taobao_cate_id ──
    cat_id = body.cat_id
    if not cat_id and db_job is not None and isinstance(db_job.taobao_cate_id, dict):
        cat_id = db_job.taobao_cate_id.get("cate_id")

    # ── PDF 完整流程：商机发现 → opportunityId（仅淘宝）──
    opportunity_id: Optional[int] = body.opportunity_id
    opportunity_type: int = body.opportunity_type
    opportunity_meta: Dict[str, Any] = {}
    if body.platform == "taobao" and body.use_opportunity and not opportunity_id:
        from ..services.taobao_opportunity_crawler import discover_opportunities
        # profile_dir：复用同一个 tb 账号
        opp_profile = None
        opp_acct = (
            db.query(PublishAccount)
            .filter(PublishAccount.user_id == current_user.id, PublishAccount.platform == "taobao")
            .order_by(PublishAccount.id.asc())
            .first()
        )
        if opp_acct:
            opp_profile = opp_acct.browser_profile or str(BROWSER_DATA_DIR / f"{opp_acct.platform}_{opp_acct.nickname}")
        seed = (body.opportunity_seed_keyword or product_name or "").strip()
        if opp_profile and seed:
            try:
                opp_result = await discover_opportunities(
                    profile_dir=opp_profile,
                    seed_keyword=seed,
                    tab=body.opportunity_tab,
                    max_rows=10,
                )
                opportunity_meta = {
                    "ok": opp_result.get("ok"),
                    "error": opp_result.get("error"),
                    "tab_switched": opp_result.get("tab_switched"),
                    "search_applied": opp_result.get("search_applied"),
                    "seed_keyword": seed,
                    "found": len(opp_result.get("opportunities") or []),
                }
                opps = opp_result.get("opportunities") or []
                if opps:
                    chosen = opps[0]
                    opportunity_id = chosen.get("opportunity_id")
                    opportunity_type = chosen.get("opportunity_type") or 2
                    opportunity_meta["chosen"] = chosen
                    logger.info(
                        "[publish_from_job] PDF flow: seed=%r → opportunityId=%s type=%s",
                        seed, opportunity_id, opportunity_type,
                    )
            except Exception as e:
                logger.exception("[publish_from_job] discover_opportunities failed")
                opportunity_meta = {"ok": False, "error": str(e)}

    # ── 品牌：body.brand > config.brand；都空时 no_brand 由用户显式控制（默认 False=审核自选） ──
    brand = (body.brand or "").strip() or str(config.get("brand") or "").strip() or None
    no_brand = body.no_brand

    # ── 商品属性 specs：analysis.specs + body.extra_specs ──
    specs: Dict[str, str] = {}
    if isinstance(analysis.get("specs"), dict):
        for k, v in analysis["specs"].items():
            if isinstance(v, str) and v.strip():
                specs[str(k).strip()] = v.strip()
            elif isinstance(v, list) and v:
                specs[str(k).strip()] = ",".join(str(x).strip() for x in v if x)
    if body.extra_specs:
        specs.update({str(k).strip(): str(v).strip() for k, v in body.extra_specs.items() if k and v})

    form_body = OpenProductFormBody(
        platform=body.platform,
        account_nickname=body.account_nickname,
        title=title,
        price=body.price,
        category=listing_category,
        main_image_asset_ids=main_ids,
        portrait_image_asset_ids=portrait_ids,
        detail_image_asset_ids=detail_ids,
        main_square_image_asset_ids=main_square_ids,
        white_bg_image_asset_ids=white_bg_ids,
        sku_image_asset_ids=sku_ids,
        selling_points=selling_points or None,
        hero_claim=hero_claim or None,
        cat_id=cat_id,
        guide_title=guide_title,
        brand=brand,
        no_brand=no_brand,
        specs=specs or None,
        stock=body.stock,
        delivery_time=body.delivery_time,
        delivery_location=body.delivery_location,
        opportunity_id=opportunity_id,
        opportunity_type=opportunity_type,
    )

    result = await open_product_form(form_body, current_user=current_user, db=db)
    if opportunity_meta:
        if isinstance(result, dict):
            result["opportunity_meta"] = opportunity_meta
    return result
