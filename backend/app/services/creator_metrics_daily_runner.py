"""发布数据（播放量）每日错峰同步并上报云端：抖音 + 视频号。

链路：本机浏览器登录态（只读）→ 采集作品计数 → 写本地采样表（按北京自然日唯一）
→ 批量上报云端 `POST {AUTH_SERVER_BASE}/api/publish/metrics:batch`（失败指数退避重试，
仍未成功的行保留 pending，下一天继续带上去，不丢数据）。

时间：每天 02:00–06:00（北京时间）窗口内**错峰**执行——每台机器按 installation_id 稳定
散列到窗口内的某一分钟，再叠加每日 0~5 分钟微抖，避免所有客户端挤在同一分钟打服务器。
窗口可用 LOBSTER_PUBLISH_METRICS_WINDOW="02:00-06:00" 覆盖，LOBSTER_PUBLISH_METRICS_DAILY=0 关闭。

朋友圈（微信朋友圈视频）本轮明确不做。
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence

from sqlalchemy.orm import Session

from ..core.config import settings
from ..db import SessionLocal
from ..models import CreatorMetricSample, PublishAccount
from .creator_metrics_collect import (
    METRIC_PLATFORMS,
    PLATFORM_LABEL,
    SAMPLE_BATCH_SIZE,
    beijing_day,
    build_batch_payload,
    build_metric_rows,
    chunk_rows,
    sample_key,
)

logger = logging.getLogger(__name__)

BEIJING_TZ = timezone(timedelta(hours=8))

# 错峰窗口（北京时间分钟数）：默认 02:00–06:00。所有客户端都固定 02:00 会在凌晨同一分钟
# 打爆服务器，因此改成「窗口内按安装 ID 稳定散列到某一分钟 + 每日 0~5 分钟微抖」。
DAILY_WINDOW_START_MINUTE = 2 * 60
DAILY_WINDOW_END_MINUTE = 6 * 60
DAILY_SLOT_JITTER_MINUTES = 5

DEFAULT_UPLOAD_PATH = "/api/publish/metrics:batch"
UPLOAD_MAX_ATTEMPTS = 3
UPLOAD_BACKOFF_SECONDS: Sequence[float] = (5.0, 30.0)
UPLOAD_ROW_LIMIT = 500

PostFn = Callable[[str, Dict[str, Any], Dict[str, str]], Awaitable[Any]]
SleepFn = Callable[[float], Awaitable[None]]


def daily_sync_enabled() -> bool:
    raw = os.environ.get("LOBSTER_PUBLISH_METRICS_DAILY", "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def upload_path() -> str:
    raw = (os.environ.get("LOBSTER_PUBLISH_METRICS_PATH") or "").strip()
    return raw or DEFAULT_UPLOAD_PATH


def beijing_now() -> datetime:
    return datetime.now(timezone.utc).astimezone(BEIJING_TZ)


def _day_jitter_minutes(seed: str, span: int) -> int:
    """按「日期 + 机器」稳定微抖，避免同一台机器每天都在同一秒发起（同日恒定，便于复现）。"""
    if span <= 0:
        return 0
    digest = hashlib.sha256(str(seed or "").encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % (span + 1)


def _parse_window_spec(raw: str) -> Optional[tuple[int, int]]:
    """解析 "2-6" / "02:00-06:00" 形式的窗口（返回北京时间分钟数区间）。"""
    text = str(raw or "").strip()
    if "-" not in text:
        return None
    left, right = text.split("-", 1)

    def _to_minutes(value: str) -> int:
        item = value.strip()
        if ":" in item:
            hours, minutes = item.split(":", 1)
            return int(hours) * 60 + int(minutes)
        return int(float(item)) * 60

    try:
        start, end = _to_minutes(left), _to_minutes(right)
    except (TypeError, ValueError):
        return None
    if not (0 <= start < end <= 24 * 60):
        return None
    return start, end


def daily_window() -> tuple[int, int]:
    """错峰窗口（北京时间分钟数）；LOBSTER_PUBLISH_METRICS_WINDOW 可覆盖，如 "02:00-06:00"。"""
    parsed = _parse_window_spec(os.environ.get("LOBSTER_PUBLISH_METRICS_WINDOW", ""))
    return parsed or (DAILY_WINDOW_START_MINUTE, DAILY_WINDOW_END_MINUTE)


def stable_slot_minute(
    seed: str,
    *,
    window: Optional[tuple[int, int]] = None,
) -> int:
    """把同一台机器稳定映射到窗口内的某一分钟（打散，避免所有客户端同一时刻上报）。"""
    start, end = window or daily_window()
    span = max(1, int(end) - int(start))
    digest = hashlib.sha256(str(seed or "default").encode("utf-8")).hexdigest()
    return int(start) + int(digest[:8], 16) % span


def daily_slot_seed() -> str:
    """本机错峰种子：安装 ID 优先，其次登录用户，最后环境变量兜底。"""
    jwt_token, installation_id = read_login_context()
    if installation_id:
        return f"install:{installation_id}"
    sub = _decode_jwt_sub(jwt_token)
    if sub:
        return f"user:{sub}"
    return f"env:{os.environ.get('LOBSTER_INSTALLATION_ID', 'default')}"


def next_daily_run_at(
    now: Optional[datetime] = None,
    *,
    seed: str = "",
    window: Optional[tuple[int, int]] = None,
    jitter_minutes: int = DAILY_SLOT_JITTER_MINUTES,
    jitter_value: Optional[int] = None,
) -> datetime:
    """下一个「北京时间 窗口内本机固定分钟 + 每日微抖」时刻（带时区）。"""
    current = (now or beijing_now()).astimezone(BEIJING_TZ)
    start, end = window or daily_window()
    slot = stable_slot_minute(seed, window=(start, end))
    offset = (
        _day_jitter_minutes(f"{current.strftime('%Y-%m-%d')}|{seed}", jitter_minutes)
        if jitter_value is None
        else max(0, int(jitter_value))
    )
    target = current.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(
        minutes=slot + offset
    )
    if target <= current:
        day_next = (current + timedelta(days=1)).strftime("%Y-%m-%d")
        offset_next = (
            _day_jitter_minutes(f"{day_next}|{seed}", jitter_minutes)
            if jitter_value is None
            else max(0, int(jitter_value))
        )
        target = target + timedelta(days=1) - timedelta(minutes=offset) + timedelta(
            minutes=offset_next
        )
    return target


def _decode_jwt_sub(token: str) -> str:
    parts = (token or "").split(".")
    if len(parts) < 2:
        return ""
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")).decode("utf-8"))
    except Exception:
        return ""
    return str(data.get("sub") or "").strip()


def read_login_context() -> tuple[str, str]:
    """后台任务用的云端凭证：(jwt, installation_id)；读不到返回空串。"""
    jwt_token = ""
    installation_id = ""
    try:
        from .openclaw_channel_auth_store import read_channel_fallback

        jwt_token, installation_id = read_channel_fallback()
    except Exception as exc:
        logger.warning("[PUBLISH-METRICS] 读取本机登录凭证失败: %s", exc)
    jwt_token = (jwt_token or getattr(settings, "openclaw_sutui_fallback_jwt", "") or "").strip()
    installation_id = (
        (installation_id or "").strip()
        or (getattr(settings, "openclaw_sutui_fallback_installation_id", "") or "").strip()
        or (os.environ.get("LOBSTER_INSTALLATION_ID") or "").strip()
    )
    return jwt_token, installation_id


def resolve_local_user_id(db: Session, *, installation_id: str = "") -> int:
    """优先用云端 token 的 sub；否则退回本机 users 表里的首个用户。"""
    jwt_token, _ = read_login_context()
    sub = _decode_jwt_sub(jwt_token)
    if sub.isdigit() and int(sub) > 0:
        return int(sub)
    try:
        from ..models import User

        row = db.query(User).order_by(User.id.asc()).first()
        if row is not None:
            return int(row.id)
        if installation_id:
            row = db.query(User).filter(User.client_installation_id == installation_id).first()
            if row is not None:
                return int(row.id)
    except Exception as exc:
        logger.warning("[PUBLISH-METRICS] 解析本机用户失败: %s", exc)
    return 0


def metric_accounts(db: Session, user_id: int) -> List[PublishAccount]:
    """参与每日同步的账号：抖音 + 视频号（朋友圈不做）。"""
    return (
        db.query(PublishAccount)
        .filter(PublishAccount.user_id == user_id, PublishAccount.platform.in_(METRIC_PLATFORMS))
        .order_by(PublishAccount.id.asc())
        .all()
    )


def build_sample_rows(
    *,
    user_id: int,
    account: Any,
    platform: str,
    items: Sequence[Dict[str, Any]],
    installation_id: str,
    sampled_at: Optional[datetime] = None,
    source: str = "daily_0200",
) -> List[Dict[str, Any]]:
    """作品条目 → 本地采样行（含幂等键与北京采样日）。"""
    stamp = sampled_at or datetime.utcnow()
    day = beijing_day(stamp)
    account_id = int(getattr(account, "id", 0) or 0)
    nickname = str(getattr(account, "nickname", "") or "")
    rows: List[Dict[str, Any]] = []
    for row in build_metric_rows(items, platform=platform):
        enriched = dict(row)
        enriched.update(
            {
                "user_id": int(user_id),
                "account_id": account_id,
                "account_nickname": nickname,
                "sampled_at": stamp,
                "sampled_day": day,
                "source": source,
            }
        )
        enriched["sample_key"] = sample_key(
            installation_id=installation_id,
            platform=platform,
            item_id=str(enriched.get("item_id") or ""),
            sampled_day=day,
        )
        rows.append(enriched)
    return rows


_COUNTER_FIELDS = ("views", "likes", "comments", "shares", "favorites", "impressions")


def upsert_metric_samples(db: Session, rows: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    """按 sample_key 幂等写入：同一天重复采集只更新数值（并重新排队上报）。"""
    created = 0
    updated = 0
    for row in rows:
        key = str(row.get("sample_key") or "")
        if not key:
            continue
        existing = (
            db.query(CreatorMetricSample).filter(CreatorMetricSample.sample_key == key).first()
        )
        if existing is None:
            db.add(
                CreatorMetricSample(
                    user_id=int(row.get("user_id") or 0),
                    account_id=int(row.get("account_id") or 0) or None,
                    account_nickname=row.get("account_nickname") or None,
                    platform=str(row.get("platform") or ""),
                    item_id=str(row.get("item_id") or ""),
                    item_url=row.get("item_url") or None,
                    title=row.get("title") or None,
                    published_at=row.get("published_at"),
                    views=int(row.get("views") or 0),
                    likes=int(row.get("likes") or 0),
                    comments=int(row.get("comments") or 0),
                    shares=int(row.get("shares") or 0),
                    favorites=int(row.get("favorites") or 0),
                    impressions=int(row.get("impressions") or 0),
                    sampled_at=row.get("sampled_at") or datetime.utcnow(),
                    sampled_day=str(row.get("sampled_day") or ""),
                    sample_key=key,
                    source=str(row.get("source") or "daily_0200"),
                    uploaded_at=None,
                    upload_attempts=0,
                    last_upload_error=None,
                )
            )
            created += 1
            continue
        changed = False
        for field in _COUNTER_FIELDS:
            value = int(row.get(field) or 0)
            if getattr(existing, field) != value:
                setattr(existing, field, value)
                changed = True
        if not existing.item_url and row.get("item_url"):
            existing.item_url = row.get("item_url")
            changed = True
        if row.get("title") and existing.title != row.get("title"):
            existing.title = row.get("title")
            changed = True
        if row.get("published_at") and existing.published_at != row.get("published_at"):
            existing.published_at = row.get("published_at")
            changed = True
        if changed:
            existing.sampled_at = row.get("sampled_at") or datetime.utcnow()
            existing.uploaded_at = None
            existing.last_upload_error = None
            updated += 1
    db.commit()
    return {"created": created, "updated": updated}


def pending_metric_samples(db: Session, *, limit: int = UPLOAD_ROW_LIMIT) -> List[CreatorMetricSample]:
    return (
        db.query(CreatorMetricSample)
        .filter(CreatorMetricSample.uploaded_at.is_(None))
        .order_by(CreatorMetricSample.id.asc())
        .limit(max(1, int(limit)))
        .all()
    )


def count_pending_metric_samples(db: Session) -> int:
    return int(
        db.query(CreatorMetricSample)
        .filter(CreatorMetricSample.uploaded_at.is_(None))
        .count()
    )


def _row_to_payload_dict(row: CreatorMetricSample) -> Dict[str, Any]:
    return {
        "sample_key": row.sample_key,
        "platform": row.platform,
        "item_id": row.item_id,
        "item_url": row.item_url,
        "title": row.title,
        "account_id": row.account_id,
        "account_nickname": row.account_nickname,
        "views": int(row.views or 0),
        "likes": int(row.likes or 0),
        "comments": int(row.comments or 0),
        "shares": int(row.shares or 0),
        "favorites": int(row.favorites or 0),
        "impressions": int(row.impressions or 0),
        "sampled_at": row.sampled_at,
        "sampled_day": row.sampled_day,
        "source": row.source,
        "published_at": row.published_at,
    }


async def _httpx_post(url: str, payload: Dict[str, Any], headers: Dict[str, str]) -> Any:
    import httpx

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True, trust_env=False) as client:
        return await client.post(url, json=payload, headers=headers)


async def upload_pending_metric_samples(
    db: Session,
    *,
    installation_id: str = "",
    user_id: int = 0,
    limit: int = UPLOAD_ROW_LIMIT,
    post_fn: Optional[PostFn] = None,
    sleep_fn: Optional[SleepFn] = None,
    max_attempts: int = UPLOAD_MAX_ATTEMPTS,
) -> Dict[str, Any]:
    """把未上报的采样批量推到云端；失败保留 pending 供下次重试。"""
    base = (getattr(settings, "auth_server_base", "") or "").strip().rstrip("/")
    jwt_token, token_installation_id = read_login_context()
    effective_installation = (installation_id or token_installation_id or "").strip()
    rows = pending_metric_samples(db, limit=limit)
    if not rows:
        return {"ok": True, "uploaded": 0, "failed": 0, "pending": 0, "skipped": "no_pending_samples"}
    if not base or not jwt_token:
        logger.info(
            "[PUBLISH-METRICS] 暂不上报：base=%s 登录凭证=%s 待上报=%s",
            bool(base),
            bool(jwt_token),
            len(rows),
        )
        return {
            "ok": False,
            "uploaded": 0,
            "failed": 0,
            "pending": len(rows),
            "skipped": "未登录或未配置 AUTH_SERVER_BASE",
        }

    poster = post_fn or _httpx_post
    sleeper = sleep_fn or asyncio.sleep
    url = f"{base}{upload_path()}"
    headers = {"Authorization": f"Bearer {jwt_token}", "Content-Type": "application/json"}
    if effective_installation:
        headers["X-Installation-Id"] = effective_installation

    uploaded = 0
    failed = 0
    last_error = ""
    pairs = [(row, _row_to_payload_dict(row)) for row in rows]
    for pair_chunk in chunk_rows(pairs, SAMPLE_BATCH_SIZE):
        chunk_rows_db = [item[0] for item in pair_chunk]
        payload = build_batch_payload(
            installation_id=effective_installation,
            user_id=user_id,
            rows=[item[1] for item in pair_chunk],
        )
        ok = False
        attempts_used = 0
        for attempt in range(1, max(1, int(max_attempts)) + 1):
            attempts_used = attempt
            try:
                resp = await poster(url, payload, headers)
                status = int(getattr(resp, "status_code", 0) or 0)
                if 200 <= status < 300:
                    ok = True
                    break
                body = ""
                try:
                    body = str(getattr(resp, "text", "") or "")[:300]
                except Exception:
                    body = ""
                last_error = f"HTTP {status} {body}".strip()
            except Exception as exc:
                last_error = str(exc)
            backoff = UPLOAD_BACKOFF_SECONDS[min(attempt - 1, len(UPLOAD_BACKOFF_SECONDS) - 1)]
            logger.warning(
                "[PUBLISH-METRICS] 上报失败 attempt=%s/%s err=%s",
                attempt,
                max_attempts,
                last_error,
            )
            if attempt < max_attempts:
                await sleeper(backoff)
        now = datetime.utcnow()
        for row in chunk_rows_db:
            row.upload_attempts = int(row.upload_attempts or 0) + max(1, attempts_used)
            if ok:
                row.uploaded_at = now
                row.last_upload_error = None
                uploaded += 1
            else:
                row.last_upload_error = last_error[:1000]
                failed += 1
        db.commit()

    pending_left = count_pending_metric_samples(db)
    return {
        "ok": failed == 0,
        "uploaded": uploaded,
        "failed": failed,
        "pending": pending_left,
        "error": last_error or None,
        "url": url,
    }


async def collect_metrics_for_account(
    db: Session,
    *,
    user_id: int,
    account: PublishAccount,
    installation_id: str = "",
    sync_fn: Optional[Callable[..., Awaitable[Dict[str, Any]]]] = None,
    sampled_at: Optional[datetime] = None,
    source: str = "daily_0200",
) -> Dict[str, Any]:
    """采集单个账号并落库（不触发上报）。"""
    if sync_fn is None:
        from ..api.creator_content import perform_creator_content_sync

        sync_fn = perform_creator_content_sync
    try:
        result = await sync_fn(db, user_id=user_id, account_id=int(account.id))
    except Exception as exc:
        logger.exception("[PUBLISH-METRICS] 账号采集异常 account_id=%s", account.id)
        return {
            "account_id": int(account.id),
            "nickname": account.nickname,
            "platform": account.platform,
            "ok": False,
            "item_count": 0,
            "error": str(exc),
        }
    items = result.get("items") or []
    rows = build_sample_rows(
        user_id=user_id,
        account=account,
        platform=account.platform,
        items=items,
        installation_id=installation_id,
        sampled_at=sampled_at,
        source=source,
    )
    counts = upsert_metric_samples(db, rows)
    return {
        "account_id": int(account.id),
        "nickname": account.nickname,
        "platform": account.platform,
        "platform_label": PLATFORM_LABEL.get(account.platform, account.platform),
        "ok": bool(result.get("ok")),
        "item_count": len(items),
        "samples_created": counts["created"],
        "samples_updated": counts["updated"],
        "error": result.get("error"),
        "need_relogin": bool((result.get("meta") or {}).get("need_relogin")),
    }


async def run_daily_metrics_cycle(
    *,
    db: Optional[Session] = None,
    user_id: Optional[int] = None,
    installation_id: str = "",
    sync_fn: Optional[Callable[..., Awaitable[Dict[str, Any]]]] = None,
    post_fn: Optional[PostFn] = None,
    sleep_fn: Optional[SleepFn] = None,
    sampled_at: Optional[datetime] = None,
    source: str = "daily_0200",
) -> Dict[str, Any]:
    """每日一轮：抖音 + 视频号采集落库 → 上报云端。"""
    owns_session = db is None
    session = db or SessionLocal()
    try:
        jwt_token, token_installation_id = read_login_context()
        effective_installation = (installation_id or token_installation_id or "").strip()
        uid = int(user_id or 0) or resolve_local_user_id(session, installation_id=effective_installation)
        if not uid:
            return {"ok": False, "error": "无法确定本机用户（未登录且本地无用户）", "accounts": []}
        accounts = metric_accounts(session, uid)
        results: List[Dict[str, Any]] = []
        for account in accounts:
            results.append(
                await collect_metrics_for_account(
                    session,
                    user_id=uid,
                    account=account,
                    installation_id=effective_installation,
                    sync_fn=sync_fn,
                    sampled_at=sampled_at,
                    source=source,
                )
            )
        upload = await upload_pending_metric_samples(
            session,
            installation_id=effective_installation,
            user_id=uid,
            post_fn=post_fn,
            sleep_fn=sleep_fn,
        )
        summary = {
            "ok": any(r.get("ok") for r in results) or not results,
            "platforms": list(METRIC_PLATFORMS),
            "user_id": uid,
            "installation_id": effective_installation,
            "account_count": len(accounts),
            "accounts": results,
            "samples_created": sum(int(r.get("samples_created") or 0) for r in results),
            "samples_updated": sum(int(r.get("samples_updated") or 0) for r in results),
            "upload": upload,
        }
        logger.info(
            "[PUBLISH-METRICS] 每日同步完成 accounts=%s created=%s updated=%s uploaded=%s failed=%s",
            len(accounts),
            summary["samples_created"],
            summary["samples_updated"],
            upload.get("uploaded"),
            upload.get("failed"),
        )
        return summary
    finally:
        if owns_session:
            session.close()


async def creator_metrics_daily_background_loop() -> None:
    """常驻循环：每天在 02:00–06:00 窗口内、本机固定那一分钟（+0~5 分钟微抖）跑一轮。"""
    if not daily_sync_enabled():
        logger.info("[PUBLISH-METRICS] 每日同步已关闭（LOBSTER_PUBLISH_METRICS_DAILY=0）")
        return
    start, end = daily_window()
    seed = daily_slot_seed()
    slot = stable_slot_minute(seed, window=(start, end))
    logger.info(
        "[PUBLISH-METRICS] 发布数据每日同步已启动：每天 %02d:%02d-%02d:%02d（北京时间）错峰执行，"
        "本机固定时段约 %02d:%02d（每日再抖 0-%s 分钟），平台=%s",
        start // 60,
        start % 60,
        end // 60,
        end % 60,
        slot // 60,
        slot % 60,
        DAILY_SLOT_JITTER_MINUTES,
        "/".join(PLATFORM_LABEL.get(p, p) for p in METRIC_PLATFORMS),
    )
    while True:
        try:
            now = beijing_now()
            target = next_daily_run_at(now, seed=daily_slot_seed())
            wait = max(5.0, min((target - now).total_seconds(), 1800.0))
            await asyncio.sleep(wait)
            if beijing_now() < target:
                continue
            await run_daily_metrics_cycle()
            await asyncio.sleep(90.0)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("[PUBLISH-METRICS] 每日同步异常: %s", exc, exc_info=True)
            await asyncio.sleep(300.0)
