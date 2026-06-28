import asyncio
import hashlib
import logging
import re
import time
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Union
from urllib.parse import quote

import bcrypt
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ..core.config import settings
from ..captcha_util import create_captcha, verify_captcha
from ..db import get_db
from ..models import User
from ..services.openclaw_channel_auth_store import (
    persist_channel_fallback_for_login,
    persist_weixin_openclaw_peer_for_user,
)

router = APIRouter()
logger = logging.getLogger(__name__)
ONLINE_USER_EMAIL = "online@sutui.lobster.local"
REGISTER_INITIAL_CREDITS = 100

PHONE_EMAIL_SUFFIX = "@sms.lobster.local"
_CN_MOBILE_RE = re.compile(r"^1[3-9]\d{9}$")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7 days

# 认证中心 GET /auth/me：并发发布等多路同时校验时，远端偶发 ConnectTimeout/502，对同一请求做有限次重试（非换路径兜底）。
_AUTH_ME_MAX_ATTEMPTS = 3
_AUTH_ME_TRANSIENT_HTTP = frozenset({429, 502, 503, 504})

# Bearer(+安装 id) -> (monotonic 过期时间, 用户 id)；仅缓存远端 200 结果
_AUTH_ME_CACHE_LOCK = asyncio.Lock()
_AUTH_ME_CACHE: Dict[str, tuple[float, int]] = {}
_SKILL_STORE_ADMIN_CACHE_LOCK = asyncio.Lock()
_SKILL_STORE_ADMIN_CACHE: Dict[str, tuple[float, bool]] = {}
_SKILL_STORE_ADMIN_CACHE_TTL_SEC = 120.0


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, protected_namespaces=())
    id: int
    email: str
    preferred_model: str
    credits: Optional[float] = None
    brand_mark: Optional[str] = None
    is_overseas_user: bool = False
    wecom_userid: Optional[str] = None
    is_agent: bool = False
    features: Dict[str, bool] = Field(default_factory=dict)


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


_BRAND_MARK_RE = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")


def _normalize_brand_mark(raw: Optional[str]) -> Optional[str]:
    """注册请求中的品牌标记；空则不入库。须为小写 slug（与 brands.json 的 marks 键一致）。"""
    if raw is None:
        return None
    s = (raw or "").strip().lower()
    if not s:
        return None
    if not _BRAND_MARK_RE.match(s):
        raise HTTPException(status_code=400, detail="品牌标记格式无效")
    return s


class RegisterBody(BaseModel):
    account: str  # 字母开头，2～64 位，仅允许字母数字._-
    password: str
    captcha_id: str = ""
    captcha_answer: str = ""
    brand_mark: Optional[str] = None


class SmsSendBody(BaseModel):
    phone: str
    captcha_id: str = ""
    captcha_answer: str = ""


class RegisterPhoneBody(BaseModel):
    phone: str
    code: str
    password: Optional[str] = None
    brand_mark: Optional[str] = None
    is_overseas_user: bool = False
    parent_account: Optional[str] = None


class PhonePasswordLoginBody(BaseModel):
    phone: Optional[str] = None
    account: Optional[str] = None
    password: str


class SetPasswordBody(BaseModel):
    password: str


def _normalize_cn_mobile(raw: str) -> str:
    d = re.sub(r"\D", "", (raw or "").strip())
    if not _CN_MOBILE_RE.match(d):
        raise HTTPException(status_code=400, detail="手机号格式无效")
    return d


def _phone_account_email(mobile: str) -> str:
    return f"{mobile}{PHONE_EMAIL_SUFFIX}"


def _normalize_new_password(raw: str) -> str:
    password = raw or ""
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="密码至少 6 位")
    if len(password) > 128:
        raise HTTPException(status_code=400, detail="密码不能超过 128 位")
    return password


def _login_account_key(username: str) -> str:
    u_raw = (username or "").strip()
    if not u_raw:
        return ""
    if "@" in u_raw:
        return u_raw.lower()
    digits_only = re.sub(r"\D", "", u_raw)
    if _CN_MOBILE_RE.match(digits_only):
        return _phone_account_email(digits_only)
    return u_raw.lower()


def _auth_server_base_or_none() -> str:
    return (getattr(settings, "auth_server_base", None) or "").strip().rstrip("/")


def _auth_server_base_required() -> str:
    base = _auth_server_base_or_none()
    if not base:
        raise HTTPException(status_code=503, detail="未配置认证中心（AUTH_SERVER_BASE）")
    return base


def _client_error_detail(resp: httpx.Response) -> Any:
    try:
        data = resp.json()
    except Exception:
        text = (resp.text or "").strip()
        return text or f"HTTP {resp.status_code}"
    if isinstance(data, dict) and "detail" in data:
        return data.get("detail")
    return data


def _raise_auth_server_response(resp: httpx.Response) -> None:
    if 400 <= resp.status_code < 500:
        raise HTTPException(status_code=resp.status_code, detail=_client_error_detail(resp))
    raise HTTPException(status_code=502, detail="认证中心暂时不可用")


def _forward_headers_to_auth_server(request: Request, *, include_auth: bool = False) -> Dict[str, str]:
    headers: Dict[str, str] = {}
    xi = (request.headers.get("X-Installation-Id") or request.headers.get("x-installation-id") or "").strip()
    if xi:
        headers["X-Installation-Id"] = xi
    if include_auth:
        auth = (request.headers.get("Authorization") or "").strip()
        if auth:
            headers["Authorization"] = auth
    return headers


def _persist_remote_token_for_openclaw(token: str, request: Request, user_id: Optional[int] = None) -> None:
    persist_channel_fallback_for_login(
        jwt_token=token,
        request=request,
        user_id=user_id,
        db=None,
    )


async def _proxy_auth_form(path: str, form: Dict[str, str], request: Request) -> Dict[str, Any]:
    base = _auth_server_base_required()
    headers = _forward_headers_to_auth_server(request)
    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            resp = await client.post(f"{base}{path}", data=form, headers=headers)
        except httpx.RequestError as e:
            logger.warning("[auth-proxy] %s request failed: %s", path, e)
            raise HTTPException(status_code=503, detail="认证中心不可达") from e
    if resp.status_code >= 400:
        _raise_auth_server_response(resp)
    return resp.json()


async def _proxy_auth_json(path: str, payload: Dict[str, Any], request: Request, *, include_auth: bool = False) -> Dict[str, Any]:
    base = _auth_server_base_required()
    headers = _forward_headers_to_auth_server(request, include_auth=include_auth)
    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            resp = await client.post(f"{base}{path}", json=payload, headers=headers)
        except httpx.RequestError as e:
            logger.warning("[auth-proxy] %s request failed: %s", path, e)
            raise HTTPException(status_code=503, detail="认证中心不可达") from e
    if resp.status_code >= 400:
        _raise_auth_server_response(resp)
    return resp.json()


@router.get("/captcha", summary="获取图片验证码（登录/注册前调用）")
async def get_captcha():
    base = _auth_server_base_or_none()
    if base:
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                resp = await client.get(f"{base}/auth/captcha")
            except httpx.RequestError as e:
                logger.warning("[auth-proxy] captcha request failed: %s", e)
                raise HTTPException(status_code=503, detail="认证中心不可达") from e
        if resp.status_code >= 400:
            _raise_auth_server_response(resp)
        data = resp.json()
        try:
            uid = data.get("id") if isinstance(data, dict) else None
            if uid is not None:
                persist_channel_fallback_for_login(
                    jwt_token=token,
                    request=request,
                    user_id=int(uid),
                    db=None,
                )
        except Exception as exc:
            logger.debug("[auth-proxy] persist channel fallback from /auth/me skipped: %s", exc)
        return data
    captcha_id, image_data_uri = create_captcha()
    return {"captcha_id": captcha_id, "image": image_data_uri}


def _password_to_bcrypt_input(password: str) -> bytes:
    raw = password.encode("utf-8")
    if len(raw) <= 72:
        return raw
    return hashlib.sha256(raw).hexdigest().encode("ascii")


def get_password_hash(password: str) -> str:
    data = _password_to_bcrypt_input(password)
    return bcrypt.hashpw(data, bcrypt.gensalt()).decode("ascii")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    data = _password_to_bcrypt_input(plain_password)
    return bcrypt.checkpw(data, hashed_password.encode("ascii"))


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.secret_key, algorithm=ALGORITHM)


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="无法验证凭证",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
        user_id: int = int(payload.get("sub"))
        if user_id is None:
            raise credentials_exception
    except (JWTError, ValueError):
        raise credentials_exception
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise credentials_exception
    return user


class _ServerUser:
    """仅含 id，用于发布/素材等本地接口：凭 server 的 token 识别用户。"""
    def __init__(self, id: int):
        self.id = id


_INTERNAL_INSTALL_ID_RE = re.compile(r"^lobster-internal-(\d+)$", re.IGNORECASE)


def _server_user_from_internal_lobster_jwt(request: Request, token: str) -> Optional[_ServerUser]:
    """
    本机代用户调用 POST /chat 等：create_access_token（本机 secret）+ X-Installation-Id lobster-internal-{uid}。
    认证中心不识别该 JWT；在 auth/me 返回 401/403 时用本机 HS256 校验 sub 与 Installation-Id 一致。
    """
    xi = (request.headers.get("X-Installation-Id") or "").strip()
    m = _INTERNAL_INSTALL_ID_RE.match(xi)
    if not m:
        return None
    expected_uid = int(m.group(1))
    from ..core.config import get_settings

    s = get_settings()
    try:
        payload = jwt.decode(token, s.secret_key, algorithms=[ALGORITHM])
        sub = payload.get("sub")
        if sub is None:
            return None
        uid = int(sub)
    except (JWTError, ValueError, TypeError):
        return None
    if uid != expected_uid:
        return None
    return _ServerUser(id=uid)


async def get_current_user_for_local(
    request: Request,
    token: str = Depends(oauth2_scheme),
) -> _ServerUser:
    """发布/素材等：认证中心 GET {AUTH_SERVER_BASE}/auth/me。未配置 AUTH_SERVER_BASE → 503。"""
    from ..core.config import get_settings

    s = get_settings()
    base = (s.auth_server_base or "").strip().rstrip("/")
    if not base:
        logger.error(
            "[auth-local] 503 原因=未配置_AUTH_SERVER_BASE 接口=Depends(get_current_user_for_local) "
            "说明=须在 .env 设置 AUTH_SERVER_BASE（与登录所用远端一致）"
        )
        raise HTTPException(
            status_code=503,
            detail="未配置认证中心（AUTH_SERVER_BASE），无法校验登录态",
        )
    headers: Dict[str, str] = {"Authorization": f"Bearer {token}"}
    xi = (request.headers.get("X-Installation-Id") or "").strip()
    if xi:
        headers["X-Installation-Id"] = xi

    ttl_s = max(0, int(getattr(s, "auth_me_cache_ttl_seconds", 0) or 0))
    cache_key: Optional[str] = None
    if ttl_s > 0:
        cache_key = hashlib.sha256(f"{token}\0{xi}".encode("utf-8")).hexdigest()
        now_m = time.monotonic()
        async with _AUTH_ME_CACHE_LOCK:
            hit = _AUTH_ME_CACHE.get(cache_key)
            if hit and hit[0] > now_m:
                return _ServerUser(id=hit[1])

    last_request_error: Optional[Exception] = None
    for attempt in range(1, _AUTH_ME_MAX_ATTEMPTS + 1):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(
                    f"{base}/auth/me",
                    headers=headers,
                )
            if r.status_code == 200:
                data = r.json()
                uid = data.get("id")
                if uid is None:
                    raise HTTPException(status_code=401, detail="无法验证凭证")
                uid_int = int(uid)
                persist_channel_fallback_for_login(
                    jwt_token=token,
                    request=request,
                    user_id=uid_int,
                    db=None,
                )
                if cache_key is not None and ttl_s > 0:
                    exp = time.monotonic() + float(ttl_s)
                    async with _AUTH_ME_CACHE_LOCK:
                        _AUTH_ME_CACHE[cache_key] = (exp, uid_int)
                        if len(_AUTH_ME_CACHE) > 2000:
                            t = time.monotonic()
                            for k in list(_AUTH_ME_CACHE.keys()):
                                if _AUTH_ME_CACHE[k][0] <= t:
                                    del _AUTH_ME_CACHE[k]
                return _ServerUser(id=uid_int)
            if r.status_code in (401, 403):
                su = _server_user_from_internal_lobster_jwt(request, token)
                if su is not None:
                    return su
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="无法验证凭证",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            if r.status_code in _AUTH_ME_TRANSIENT_HTTP:
                if attempt < _AUTH_ME_MAX_ATTEMPTS:
                    logger.warning(
                        "[auth-local] auth/me HTTP %s，重试 %s/%s",
                        r.status_code,
                        attempt,
                        _AUTH_ME_MAX_ATTEMPTS,
                    )
                    await asyncio.sleep(0.25 * (2 ** (attempt - 1)))
                    continue
                raise HTTPException(status_code=503, detail="认证中心暂时不可用")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="无法验证凭证",
                headers={"WWW-Authenticate": "Bearer"},
            )
        except HTTPException:
            raise
        except httpx.RequestError as e:
            last_request_error = e
            if attempt < _AUTH_ME_MAX_ATTEMPTS:
                logger.warning(
                    "[auth-local] auth/me %s，重试 %s/%s: %s",
                    type(e).__name__,
                    attempt,
                    _AUTH_ME_MAX_ATTEMPTS,
                    e,
                )
                await asyncio.sleep(0.25 * (2 ** (attempt - 1)))
                continue
            logger.error(
                "[auth-local] 503 原因=认证中心不可达 url=%s/auth/me err_type=%s err=%s",
                base,
                type(e).__name__,
                e,
            )
            raise HTTPException(status_code=503, detail="认证中心不可达") from last_request_error


async def require_skill_store_admin(request: Request) -> None:
    """与认证中心 GET /skills/skill-store-admin 一致；未配置 AUTH_SERVER_BASE 时不拦截（与 MCP 一致）。"""
    from ..core.config import get_settings

    s = get_settings()
    base = (s.auth_server_base or "").strip().rstrip("/")
    if not base:
        return
    auth = (request.headers.get("Authorization") or "").strip()
    if not auth:
        raise HTTPException(status_code=403, detail="需要技能商店管理员权限")
    hdr = auth if auth.lower().startswith("bearer ") else f"Bearer {auth}"
    cache_key = hashlib.sha256(hdr.encode("utf-8")).hexdigest()
    now = time.monotonic()
    async with _SKILL_STORE_ADMIN_CACHE_LOCK:
        cached = _SKILL_STORE_ADMIN_CACHE.get(cache_key)
        if cached and cached[0] > now:
            if cached[1]:
                return
            raise HTTPException(status_code=403, detail="需要技能商店管理员权限")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{base}/skills/skill-store-admin", headers={"Authorization": hdr})
        if r.status_code != 200:
            logger.warning("[require_skill_store_admin] skill-store-admin HTTP %s", r.status_code)
            raise HTTPException(status_code=403, detail="需要技能商店管理员权限")
        data = r.json()
        is_admin = bool(data.get("is_skill_store_admin"))
        async with _SKILL_STORE_ADMIN_CACHE_LOCK:
            _SKILL_STORE_ADMIN_CACHE[cache_key] = (
                time.monotonic() + _SKILL_STORE_ADMIN_CACHE_TTL_SEC,
                is_admin,
            )
        if not is_admin:
            raise HTTPException(status_code=403, detail="需要技能商店管理员权限")
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("[require_skill_store_admin] err=%s", e)
        raise HTTPException(status_code=503, detail="无法校验管理员权限")


async def get_current_user_for_chat(
    request: Request,
    token: str = Depends(oauth2_scheme),
) -> Union[User, _ServerUser]:
    """智能对话：仅远端 /auth/me，与素材/发布一致（不走路由本机 User 表的 JWT）。"""
    return await get_current_user_for_local(request, token=token)


async def get_current_user_media_edit(
    request: Request,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> _ServerUser:
    """素材剪辑 API：在线版用认证中心 JWT（与素材库一致）；单机版用本机 SECRET_KEY 签发的 JWT。"""
    from ..core.config import get_settings

    s = get_settings()
    base = (s.auth_server_base or "").strip().rstrip("/")
    if base:
        try:
            u = await get_current_user_for_local(request, token=token)
        except HTTPException as e:
            logger.warning(
                "[media_edit_auth] reject mode=auth_server status=%s detail=%s",
                e.status_code,
                e.detail,
            )
            raise
        logger.info("[media_edit_auth] ok mode=auth_server user_id=%s", u.id)
        return u
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="无法验证凭证",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, s.secret_key, algorithms=[ALGORITHM])
        uid = payload.get("sub")
        if uid is None:
            raise credentials_exception
        user_id = int(uid)
    except (JWTError, ValueError, TypeError) as e:
        logger.warning("[media_edit_auth] reject mode=local_jwt err=%s", type(e).__name__)
        raise credentials_exception
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        logger.warning("[media_edit_auth] reject mode=local_db_no_user user_id=%s", user_id)
        raise credentials_exception
    logger.info("[media_edit_auth] ok mode=local_db user_id=%s", user.id)
    return _ServerUser(id=user.id)


@router.post("/login", response_model=Token, summary="登录（表单含验证码）")
async def login(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    username = (form.get("username") or "").strip()
    password = form.get("password") or ""
    captcha_id = (form.get("captcha_id") or "").strip()
    captcha_answer = (form.get("captcha_answer") or "").strip()
    if _auth_server_base_or_none():
        data = await _proxy_auth_form(
            "/auth/login",
            {
                "username": username,
                "password": password,
                "captcha_id": captcha_id,
                "captcha_answer": captcha_answer,
            },
            request,
        )
        access_token = str(data.get("access_token") or "").strip()
        if access_token:
            _persist_remote_token_for_openclaw(access_token, request)
        return data
    if not username or not password:
        raise HTTPException(status_code=400, detail="请输入账号和密码")
    if not verify_captcha(captcha_id, captcha_answer):
        raise HTTPException(status_code=400, detail="验证码错误或已过期，请刷新后重试")
    account_key = _login_account_key(username)
    user = db.query(User).filter(User.email == account_key).first()
    if not user or not verify_password(password, user.hashed_password):
        raise HTTPException(status_code=400, detail="账号或密码错误")
    access_token = create_access_token(data={"sub": str(user.id)})
    persist_channel_fallback_for_login(
        jwt_token=access_token, request=request, user_id=user.id, db=db
    )
    return Token(access_token=access_token)


@router.post("/login-phone-password", response_model=Token, summary="账号/手机号密码登录")
async def login_phone_password(request: Request, body: PhonePasswordLoginBody, db: Session = Depends(get_db)):
    if _auth_server_base_or_none():
        data = await _proxy_auth_json("/auth/login-phone-password", body.model_dump(), request)
        access_token = str(data.get("access_token") or "").strip()
        if access_token:
            _persist_remote_token_for_openclaw(access_token, request)
        return data
    account = (body.account or body.phone or "").strip()
    password = body.password or ""
    account_key = _login_account_key(account)
    if not account_key:
        raise HTTPException(status_code=400, detail="请输入账号或手机号")
    if not password:
        raise HTTPException(status_code=400, detail="请输入密码")
    user = db.query(User).filter(User.email == account_key).first()
    if not user or not verify_password(password, user.hashed_password):
        raise HTTPException(status_code=400, detail="账号或密码错误")
    access_token = create_access_token(data={"sub": str(user.id)})
    persist_channel_fallback_for_login(
        jwt_token=access_token, request=request, user_id=user.id, db=db
    )
    return Token(access_token=access_token)


@router.post("/set-password", summary="当前登录用户设置手机号密码")
async def set_password(
    body: SetPasswordBody,
    request: Request,
    db: Session = Depends(get_db),
    token: str = Depends(oauth2_scheme),
):
    if _auth_server_base_or_none():
        return await _proxy_auth_json("/auth/set-password", body.model_dump(), request, include_auth=True)
    current_user = await get_current_user(token=token, db=db)
    password = _normalize_new_password(body.password)
    current_user.hashed_password = get_password_hash(password)
    db.add(current_user)
    db.commit()
    return {"ok": True}


@router.post("/register", response_model=Token, summary="（已关闭）原字母账号注册，请用 /auth/register-phone")
def register(body: RegisterBody, db: Session = Depends(get_db)):
    from ..core.config import settings
    edition = (getattr(settings, "lobster_edition", None) or "online").strip().lower()
    use_independent = getattr(settings, "lobster_independent_auth", True)
    if edition != "online" or not use_independent:
        raise HTTPException(status_code=400, detail="当前版本不支持自主注册")
    raise HTTPException(status_code=400, detail="已关闭账号密码注册，请使用手机号验证码登录/注册")


@router.post("/sms/send", summary="发送手机登录/注册短信验证码（需先通过图形验证码）")
async def send_register_sms(body: SmsSendBody, request: Request):
    from ..core.config import settings

    edition = (getattr(settings, "lobster_edition", None) or "online").strip().lower()
    use_independent = getattr(settings, "lobster_independent_auth", True)
    if edition != "online" or not use_independent:
        raise HTTPException(status_code=400, detail="当前版本不支持")
    if not _auth_server_base_or_none():
        raise HTTPException(status_code=503, detail="未配置认证中心（AUTH_SERVER_BASE），无法发送短信")
    return await _proxy_auth_json("/auth/sms/send", body.model_dump(), request)


@router.post("/register-phone", response_model=Token, summary="手机号验证码注册和登录")
async def register_phone(request: Request, body: RegisterPhoneBody, db: Session = Depends(get_db)):
    from ..core.config import settings

    edition = (getattr(settings, "lobster_edition", None) or "online").strip().lower()
    use_independent = getattr(settings, "lobster_independent_auth", True)
    if edition != "online" or not use_independent:
        raise HTTPException(status_code=400, detail="当前版本不支持自主注册")
    if not _auth_server_base_or_none():
        raise HTTPException(status_code=503, detail="未配置认证中心（AUTH_SERVER_BASE），无法注册")
    data = await _proxy_auth_json("/auth/register-phone", body.model_dump(), request)
    access_token = str(data.get("access_token") or "").strip()
    if access_token:
        _persist_remote_token_for_openclaw(access_token, request)
    return data


@router.post("/persist-openclaw-channel-fallback", summary="浏览器 OAuth 落 token 后同步 OpenClaw 微信渠道凭证")
async def persist_openclaw_channel_fallback_endpoint(
    request: Request,
    db: Session = Depends(get_db),
    current_user: Union[User, _ServerUser] = Depends(get_current_user_for_chat),
):
    """与登录接口写入的 openclaw/.channel_fallback.json 相同；供 ?token= 跳转等未走后端登录的场景。"""
    auth = (request.headers.get("Authorization") or "").strip()
    if not auth.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="需要 Bearer token")
    raw = auth.split(" ", 1)[-1].strip()
    if not raw:
        raise HTTPException(status_code=401, detail="token 为空")
    persist_channel_fallback_for_login(
        jwt_token=raw,
        request=request,
        user_id=current_user.id,
        db=db,
    )
    return {"ok": True}


class WeixinOpenclawPeerBody(BaseModel):
    """与 OpenClaw 微信助手私聊中的「好友 ID」一致（可发 /myid 查看），绑定后走 mcp-gateway 将按该本站账号扣费。"""
    weixin_user_id: str


@router.post("/persist-weixin-openclaw-peer", summary="绑定微信助手好友 ID 与当前登录账号（OpenClaw MCP 按人扣费）")
async def persist_weixin_openclaw_peer_endpoint(
    body: WeixinOpenclawPeerBody,
    request: Request,
    db: Session = Depends(get_db),
    current_user: Union[User, _ServerUser] = Depends(get_current_user_for_chat),
):
    auth = (request.headers.get("Authorization") or "").strip()
    if not auth.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="需要 Bearer token")
    raw = auth.split(" ", 1)[-1].strip()
    if not raw:
        raise HTTPException(status_code=401, detail="token 为空")
    wid = (body.weixin_user_id or "").strip()
    if not wid:
        raise HTTPException(status_code=400, detail="weixin_user_id 不能为空")
    if isinstance(current_user, User):
        uid = current_user.id
    else:
        uid = current_user.id
    persist_weixin_openclaw_peer_for_user(
        weixin_user_id=wid,
        jwt_token=raw,
        request=request,
        user_id=uid,
        db=db,
    )
    return {"ok": True, "weixin_user_id": wid}


@router.get("/me", response_model=UserOut, summary="当前用户信息")
async def get_me(
    request: Request,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
):
    base = _auth_server_base_or_none()
    if base:
        headers = _forward_headers_to_auth_server(request, include_auth=True)
        if "Authorization" not in headers:
            headers["Authorization"] = f"Bearer {token}"
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                resp = await client.get(f"{base}/auth/me", headers=headers)
            except httpx.RequestError as e:
                logger.warning("[auth-proxy] me request failed: %s", e)
                raise HTTPException(status_code=503, detail="认证中心不可达") from e
        if resp.status_code >= 400:
            _raise_auth_server_response(resp)
        return resp.json()
    current_user = await get_current_user(token=token, db=db)
    edition = (getattr(settings, "lobster_edition", None) or "online").strip().lower()
    preferred = "sutui" if edition == "online" else (getattr(current_user, "preferred_model", "openclaw") or "openclaw")
    return UserOut(
        id=current_user.id,
        email=current_user.email,
        preferred_model=preferred,
        credits=getattr(current_user, "credits", None),
        brand_mark=getattr(current_user, "brand_mark", None),
        is_overseas_user=bool(getattr(current_user, "is_overseas_user", False)),
        features={},
    )


# 服务号网页授权登录（全部走服务器：redirect_uri 与 code 换 token 均在服务器完成）
def _wechat_oa_base_url(request: Request) -> str:
    base = (getattr(settings, "wechat_oa_base_url", None) or "").strip().rstrip("/")
    if base:
        return base
    return str(request.base_url).rstrip("/")


@router.get("/wechat-login-url", summary="在线版：获取服务号网页授权登录 URL")
def get_wechat_login_url(request: Request):
    """返回微信服务号网页授权链接，用户在该链接内授权后由微信回调到服务器 /auth/wechat-callback。"""
    edition = (getattr(settings, "lobster_edition", None) or "online").strip().lower()
    if edition != "online":
        logger.warning("[wechat-login-url] edition=%s 非 online，返回 400", edition)
        raise HTTPException(status_code=400, detail="当前为单机版")
    app_id = (getattr(settings, "wechat_oa_app_id", None) or "").strip()
    if not app_id:
        logger.warning("[wechat-login-url] 未配置 WECHAT_OA_APP_ID，返回 503")
        raise HTTPException(status_code=503, detail="未配置服务号 AppID（请在 .env 中设置 WECHAT_OA_APP_ID）")
    base = _wechat_oa_base_url(request)
    redirect_uri = f"{base}/auth/wechat-callback"
    url = (
        "https://open.weixin.qq.com/connect/oauth2/authorize"
        f"?appid={quote(app_id, safe='')}"
        f"&redirect_uri={quote(redirect_uri, safe='')}"
        "&response_type=code"
        "&scope=snsapi_userinfo"
        "&state=login"
        "#wechat_redirect"
    )
    if not (url and url.strip()):
        raise HTTPException(status_code=503, detail="生成登录链接失败")
    logger.info("[wechat-login-url] 返回 login_url 成功 base=%s", base)
    return {"login_url": url}


@router.get("/wechat-callback", summary="服务号网页授权回调（仅服务器可访问）")
def wechat_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """微信带 code 回调；用 code 换 openid，创建/绑定用户并下发 token，重定向到前端带 ?token=。"""
    edition = (getattr(settings, "lobster_edition", None) or "online").strip().lower()
    if edition != "online":
        raise HTTPException(status_code=400, detail="当前为单机版")
    if not code or not code.strip():
        raise HTTPException(status_code=400, detail="缺少 code 参数")
    app_id = (getattr(settings, "wechat_oa_app_id", None) or "").strip()
    secret = (getattr(settings, "wechat_oa_secret", None) or "").strip()
    if not app_id or not secret:
        raise HTTPException(status_code=503, detail="未配置服务号 AppID/AppSecret")
    try:
        with httpx.Client(timeout=15.0) as client:
            r = client.get(
                "https://api.weixin.qq.com/sns/oauth2/access_token",
                params={
                    "appid": app_id,
                    "secret": secret,
                    "code": code.strip(),
                    "grant_type": "authorization_code",
                },
            )
        data = r.json()
    except Exception as e:
        logger.exception("wechat_callback 请求微信失败: %s", e)
        raise HTTPException(status_code=502, detail="微信授权验证失败，请重试")
    err = data.get("errcode") or data.get("errmsg")
    if err:
        logger.warning("wechat_callback 微信返回错误: %s", data)
        raise HTTPException(status_code=400, detail=data.get("errmsg") or str(err))
    openid = (data.get("openid") or "").strip()
    if not openid:
        raise HTTPException(status_code=400, detail="未获取到 openid")
    user = db.query(User).filter(User.wechat_openid == openid).first()
    if not user:
        email = f"{openid}@wechat.lobster.local"
        existing = db.query(User).filter(User.email == email).first()
        if existing:
            existing.wechat_openid = openid
            db.commit()
            db.refresh(existing)
            user = existing
        else:
            _install_mark = (getattr(settings, "lobster_brand_mark", None) or "").strip().lower() or "bihuo"
            user = User(
                email=email,
                hashed_password=get_password_hash(f"wechat-{openid}-no-pwd"),
                credits=REGISTER_INITIAL_CREDITS,
                role="user",
                preferred_model="sutui",
                wechat_openid=openid,
                brand_mark=_normalize_brand_mark(_install_mark),
            )
            db.add(user)
            db.commit()
            db.refresh(user)
    access_token = create_access_token(data={"sub": str(user.id)})
    persist_channel_fallback_for_login(
        jwt_token=access_token, request=request, user_id=user.id, db=db
    )
    base = _wechat_oa_base_url(request)
    front = base.rstrip("/") + "/"
    return RedirectResponse(url=f"{front}?token={access_token}", status_code=302)
