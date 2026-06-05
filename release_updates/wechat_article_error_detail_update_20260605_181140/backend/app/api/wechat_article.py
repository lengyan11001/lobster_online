"""WeChat official-account article drafts.

Minimal WeWrite-style flow for Lobster:
markdown -> WeChat-compatible inline HTML -> WeChat draft box.
"""
from __future__ import annotations

import base64
import hashlib
import html
import json
import logging
import mimetypes
import os
import re
import tempfile
import time
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from jose import JWTError, jwt
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .auth import _ServerUser, get_current_user_media_edit
from .assets import build_asset_file_url, get_asset_public_url
from ..core.config import settings
from ..db import get_db
from ..models import Asset, User

router = APIRouter()
logger = logging.getLogger(__name__)

_BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
_DATA_DIR = _BASE_DIR / "data"
_DATA_DIR.mkdir(exist_ok=True)
_ASSETS_DIR = _BASE_DIR / "assets"
_CONFIG_PATH = _DATA_DIR / "wechat_article_config.json"
_API_TIMEOUT = 30.0
_LOBSTER_SERVER_PUBLIC = "https://bhzn.top"
_IMAGE_GENERATION_RETRIES = 3
_IMAGE_GENERATION_CONCURRENCY = 3

_THEMES: Dict[str, Dict[str, str]] = {
    "professional-clean": {
        "accent": "#2563eb",
        "accent_soft": "#eef4ff",
        "text": "#1f2937",
        "muted": "#667085",
        "border": "#e5e7eb",
        "quote_bg": "#f7f8fb",
    },
    "minimal-gold": {
        "accent": "#b8842f",
        "accent_soft": "#fff7e8",
        "text": "#24201a",
        "muted": "#766b5a",
        "border": "#eadfc9",
        "quote_bg": "#fbf6ed",
    },
    "warm-editorial": {
        "accent": "#c65a3a",
        "accent_soft": "#fff2ed",
        "text": "#2b2521",
        "muted": "#74665d",
        "border": "#eadbd2",
        "quote_bg": "#fff7f3",
    },
}


class WechatArticleConfigIn(BaseModel):
    appid: str = ""
    secret: str = ""
    author: str = ""
    theme: str = "professional-clean"


class WechatArticlePreviewIn(BaseModel):
    title: str = ""
    markdown: str = Field("", description="Article markdown")
    theme: str = "professional-clean"


class WechatArticleGenerateIn(BaseModel):
    idea: str = Field("", description="用户输入的公众号主题、想法或素材")
    style: str = "专业、有观点、适合公众号阅读"
    audience: str = ""
    theme: str = "professional-clean"
    include_images: bool = False
    image_model: str = "gpt-image-2"
    image_aspect_ratio: str = "3:2"
    image_count: int = 3
    selected_image_urls: List[str] = Field(default_factory=list)
    selected_asset_ids: List[str] = Field(default_factory=list)


class WechatArticleDraftIn(BaseModel):
    title: str = ""
    markdown: str = Field("", description="Article markdown")
    theme: str = "professional-clean"
    author: str = ""
    digest: str = ""
    cover_asset_id: str = ""
    cover_image_url: str = ""
    upload_article_images: bool = True


class WechatArticlePipelineIn(BaseModel):
    idea: str = Field("", description="用户输入的公众号主题、想法或素材")
    topic: str = ""
    style: str = "专业、有观点、适合公众号阅读"
    audience: str = ""
    theme: str = "professional-clean"
    include_images: bool = True
    image_model: str = "gpt-image-2"
    image_aspect_ratio: str = "16:9"
    image_count: int = 3
    selected_image_urls: List[str] = Field(default_factory=list)
    selected_asset_ids: List[str] = Field(default_factory=list)
    upload_article_images: bool = True


def _now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _config_doc_path(user_id: int) -> Path:
    return _DATA_DIR / f"wechat_article_{user_id}.json"


def _load_all_config() -> Dict[str, Any]:
    if not _CONFIG_PATH.exists():
        return {}
    try:
        data = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_all_config(data: Dict[str, Any]) -> None:
    _CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_config(user_id: int) -> Dict[str, Any]:
    env_cfg = {
        "appid": os.environ.get("WEWRITE_WECHAT_APPID") or os.environ.get("WECHAT_ARTICLE_APPID") or "",
        "secret": os.environ.get("WEWRITE_WECHAT_SECRET") or os.environ.get("WECHAT_ARTICLE_SECRET") or "",
        "author": os.environ.get("WEWRITE_WECHAT_AUTHOR") or os.environ.get("WECHAT_ARTICLE_AUTHOR") or "",
        "theme": os.environ.get("WEWRITE_THEME") or os.environ.get("WECHAT_ARTICLE_THEME") or "professional-clean",
    }
    data = _load_all_config()
    user_cfg = data.get(str(user_id)) if isinstance(data.get(str(user_id)), dict) else {}
    out = dict(env_cfg)
    out.update({k: v for k, v in user_cfg.items() if v is not None})
    return out


def _save_config(user_id: int, patch: Dict[str, Any]) -> Dict[str, Any]:
    data = _load_all_config()
    current = data.get(str(user_id)) if isinstance(data.get(str(user_id)), dict) else {}
    current = dict(current)
    for key in ("appid", "secret", "author", "theme"):
        if key in patch:
            value = str(patch.get(key) or "").strip()
            if key == "secret" and not value:
                continue
            current[key] = value
    if current.get("theme") not in _THEMES:
        current["theme"] = "professional-clean"
    data[str(user_id)] = current
    _save_all_config(data)
    return _load_config(user_id)


def _draft_doc_path(user_id: int) -> Path:
    return _config_doc_path(user_id)


def _load_doc(user_id: int) -> Dict[str, Any]:
    path = _draft_doc_path(user_id)
    if not path.exists():
        return {"drafts": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"drafts": []}
    if not isinstance(data, dict):
        return {"drafts": []}
    if not isinstance(data.get("drafts"), list):
        data["drafts"] = []
    return data


def _save_doc(user_id: int, data: Dict[str, Any]) -> None:
    _draft_doc_path(user_id).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _mask_secret(value: str) -> str:
    s = str(value or "").strip()
    if not s:
        return ""
    if len(s) <= 10:
        return s[:3] + "..."
    return s[:6] + "..." + s[-4:]


def _public_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "appid": cfg.get("appid") or "",
        "secret_masked": _mask_secret(cfg.get("secret") or ""),
        "has_secret": bool(str(cfg.get("secret") or "").strip()),
        "author": cfg.get("author") or "",
        "theme": cfg.get("theme") if cfg.get("theme") in _THEMES else "professional-clean",
        "themes": [
            {"id": k, "name": k}
            for k in ("professional-clean", "minimal-gold", "warm-editorial")
        ],
    }


def _theme(name: str) -> Dict[str, str]:
    return _THEMES.get((name or "").strip()) or _THEMES["professional-clean"]


def _escape(text: Any) -> str:
    return html.escape(str(text or ""), quote=False)


def _inline(text: str) -> str:
    s = _escape(text)
    s = re.sub(r"`([^`]+)`", r'<code style="background:#f4f4f5;border-radius:4px;padding:1px 4px;font-size:90%;">\1</code>', s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", s)
    return s


def _extract_title(markdown: str, fallback: str = "") -> str:
    if fallback.strip():
        return fallback.strip()
    for line in (markdown or "").splitlines():
        m = re.match(r"^\s*#\s+(.+?)\s*$", line)
        if m:
            return re.sub(r"[*_`#]+", "", m.group(1)).strip()
    for line in (markdown or "").splitlines():
        stripped = line.strip()
        if stripped:
            return re.sub(r"[*_`#]+", "", stripped).strip()[:64]
    return "公众号文章"


def _digest(markdown: str, explicit: str = "") -> str:
    if explicit.strip():
        return explicit.strip()[:120]
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", markdown or "")
    text = re.sub(r"`{3}[\s\S]*?`{3}", "", text)
    text = re.sub(r"^[#>\-\*\d\.\s]+", "", text, flags=re.MULTILINE)
    text = re.sub(r"[*_`>\[\]()]|https?://\S+", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:120]


def _raw_token_from_request(request: Request) -> str:
    auth = (request.headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def _server_proxy_base() -> str:
    return (settings.auth_server_base or "").strip().rstrip("/") or _LOBSTER_SERVER_PUBLIC


def _installation_id_from_request(request: Request, user_id: int) -> str:
    xi = (request.headers.get("X-Installation-Id") or request.headers.get("x-installation-id") or "").strip()
    return xi or f"lobster-internal-{int(user_id)}"


def _optional_local_user(request: Request, db: Session) -> Optional[_ServerUser]:
    auth = (request.headers.get("Authorization") or "").strip()
    if not auth.lower().startswith("bearer "):
        return None
    token = auth[7:].strip()
    if not token:
        return None
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
        uid = payload.get("sub")
        user_id = int(uid)
    except (JWTError, ValueError, TypeError):
        return None
    row = db.query(User.id).filter(User.id == user_id).first()
    if not row:
        return None
    return _ServerUser(id=user_id)


def _extract_json_object(text: str) -> Dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        return {}
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.IGNORECASE | re.DOTALL).strip()
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            obj = json.loads(raw[start:end + 1])
            return obj if isinstance(obj, dict) else {}
        except Exception:
            return {}
    return {}


def _normalize_generated_article(obj: Dict[str, Any], idea: str, include_images: bool) -> Dict[str, Any]:
    if not isinstance(obj, dict):
        obj = {}
    title = str(obj.get("title") or "").strip()[:80]
    if not title:
        title = _extract_title("", idea[:64] or "公众号文章")
    digest = str(obj.get("digest") or "").strip()[:120]
    markdown = str(obj.get("markdown") or "").strip()
    image_prompt = str(obj.get("image_prompt") or "").strip()
    if not markdown:
        markdown = _fallback_article_markdown(idea, title, include_images)
    if not digest:
        digest = _digest(markdown)
    if include_images and not image_prompt:
        image_prompt = f"公众号文章配图，主题：{title}。白色现代编辑设计，干净高级，适合微信推文头图，无文字。"
    return {
        "title": title,
        "digest": digest,
        "markdown": markdown,
        "image_prompt": image_prompt,
    }


def _fallback_article_markdown(idea: str, title: str = "", include_images: bool = False) -> str:
    topic = (idea or "").strip() or "一个值得展开的公众号选题"
    title = (title or topic[:50] or "公众号文章").strip()
    parts = [
        f"# {title}",
        "",
        "很多时候，一个好选题真正需要的不是堆砌信息，而是把读者正在关心的问题讲清楚。",
        "",
    ]
    if include_images:
        parts += ["> 配图建议：生成一张干净、现代、留白充足的公众号头图，用来承接文章主题。", ""]
    parts += [
        "## 为什么这个话题值得写",
        "",
        f"围绕“{topic}”，文章可以先抓住读者最直接的痛点：他们为什么会在意这件事，它和当前的工作、生活或决策有什么关系。",
        "",
        "## 可以怎么展开",
        "",
        "- 先给出一个清晰判断，避免开头过散。",
        "- 再用 2 到 3 个角度拆开讲，让观点有层次。",
        "- 最后落到行动建议，让读者看完能带走一个明确结论。",
        "",
        "## 结尾",
        "",
        "好的公众号文章不只是把内容写完，而是让读者感觉自己更理解这个问题，也更知道下一步该怎么做。",
    ]
    return "\n".join(parts)


async def _call_article_writer(body: WechatArticleGenerateIn, token: str, installation_id: str) -> Dict[str, Any]:
    asb = _server_proxy_base()
    model = (
        os.environ.get("WEWRITE_ARTICLE_MODEL")
        or os.environ.get("LOBSTER_WEWRITE_ARTICLE_MODEL")
        or getattr(settings, "lobster_orchestration_sutui_chat_model", "")
        or "deepseek-chat"
    )
    system_prompt = (
        "你是资深微信公众号主编和排版策划。请根据用户输入的主题/想法，直接生成可发布的公众号文章。"
        "必须返回严格 JSON，不要 Markdown 代码块。字段：title、digest、markdown、image_prompt。"
        "markdown 需包含一级标题、自然段、二级标题、列表或引用，语言中文，适合微信阅读。"
        "如果用户要求自动配图，image_prompt 写一条适合 gpt-image-2 的配图提示词；否则 image_prompt 可为空。"
    )
    user_prompt = (
        f"用户主题/想法：\n{body.idea.strip()}\n\n"
        f"目标读者：{body.audience.strip() or '普通公众号读者'}\n"
        f"写作风格：{body.style.strip() or '专业、有观点、适合公众号阅读'}\n"
        f"是否自动配图：{'是' if body.include_images else '否'}\n\n"
        "请生成一篇完整公众号文章，不要让用户再补标题、摘要或正文。"
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "temperature": 0.72,
    }
    if token:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "X-Installation-Id": installation_id,
        }
        url = f"{asb}/api/sutui-chat/completions"
    else:
        local = _local_openai_chat_config()
        if not local:
            raise RuntimeError(
                "未找到登录 Bearer，无法使用服务器 GPT 中转；也未配置本机 OpenAI 兼容文本模型接口。"
            )
        url = local["url"]
        headers = local["headers"]
        payload["model"] = local["model"]
    async with httpx.AsyncClient(timeout=120.0, trust_env=False) as client:
        resp = await client.post(url, json=payload, headers=headers)
    if resp.status_code >= 400:
        raise RuntimeError(f"sutui-chat HTTP {resp.status_code}: {(resp.text or '')[:600]}")
    data = resp.json() if resp.content else {}
    try:
        content = data["choices"][0]["message"]["content"]
    except Exception:
        content = json.dumps(data, ensure_ascii=False)
    return _normalize_generated_article(_extract_json_object(content), body.idea, body.include_images)


def _local_openai_chat_config() -> Optional[Dict[str, Any]]:
    key = (
        os.environ.get("WEWRITE_OPENAI_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("HAODUOMI_API_KEY")
        or os.environ.get("COMFLY_API_KEY")
        or ""
    ).strip()
    if not key:
        return None
    base = (
        os.environ.get("WEWRITE_OPENAI_BASE_URL")
        or os.environ.get("OPENAI_BASE_URL")
        or os.environ.get("HAODUOMI_OPENAI_BASE_URL")
        or os.environ.get("COMFLY_API_BASE")
        or ""
    ).strip().rstrip("/")
    if not base:
        base = "https://api.openai.com/v1"
    if base.endswith("/chat/completions"):
        url = base
    elif base.endswith("/v1"):
        url = base + "/chat/completions"
    else:
        url = base + "/v1/chat/completions"
    model = (
        os.environ.get("WEWRITE_ARTICLE_MODEL")
        or os.environ.get("OPENAI_MODEL")
        or os.environ.get("HAODUOMI_MODEL")
        or "gpt-4o-mini"
    ).strip() or "gpt-4o-mini"
    return {
        "url": url,
        "model": model,
        "headers": {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
    }


def _extract_image_url(payload: Any) -> str:
    if isinstance(payload, dict):
        b64_json = payload.get("b64_json")
        if isinstance(b64_json, str) and b64_json.strip():
            raw = b64_json.strip()
            if raw.startswith("data:image/"):
                return raw
            return f"data:image/png;base64,{raw}"
        for key in ("url", "source_url", "preview_url", "open_url", "generated_image_url", "data_url"):
            val = payload.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        for key in ("images", "data", "saved_assets"):
            val = payload.get(key)
            if isinstance(val, list):
                for item in val:
                    found = _extract_image_url(item)
                    if found:
                        return found
        for key in ("result", "payload"):
            val = payload.get(key)
            found = _extract_image_url(val)
            if found:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = _extract_image_url(item)
            if found:
                return found
    return ""


def _image_size_for_aspect_ratio(aspect_ratio: str) -> str:
    sizes = {
        "1:1": "1024x1024",
        "3:2": "1536x1024",
        "16:9": "1536x1024",
        "2:3": "1024x1536",
        "9:16": "1024x1536",
    }
    return sizes.get((aspect_ratio or "").strip(), "1536x1024")


def _image_ratio_instruction(aspect_ratio: str) -> str:
    ratio = (aspect_ratio or "").strip()
    labels = {
        "3:2": "横屏 3:2 公众号头图构图",
        "16:9": "宽横屏 16:9 封面构图",
        "1:1": "方形 1:1 构图",
        "2:3": "竖屏 2:3 构图",
        "9:16": "竖屏 9:16 构图",
    }
    return labels.get(ratio, labels["3:2"])


def _clamp_image_count(value: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 3
    return max(1, min(n, 5))


def _response_error_detail(resp: httpx.Response) -> str:
    try:
        payload = resp.json()
    except Exception:
        payload = None
    if isinstance(payload, dict):
        detail = payload.get("detail") or payload.get("error") or payload.get("message")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()
        if isinstance(detail, dict):
            msg = detail.get("message") or detail.get("detail") or detail.get("error")
            if isinstance(msg, str) and msg.strip():
                return msg.strip()
        try:
            return json.dumps(payload, ensure_ascii=False)[:800]
        except Exception:
            pass
    text = (resp.text or "").strip()
    return text[:800] if text else f"HTTP {resp.status_code}"


async def _generate_article_image(
    request: Request,
    current_user: _ServerUser,
    db: Session,
    prompt: str,
    model: str,
    aspect_ratio: str = "3:2",
) -> tuple[str, str]:
    clean_prompt = (prompt or "").strip()
    if not clean_prompt:
        return "", ""
    try:
        token = _raw_token_from_request(request)
        if not token:
            return "", "自动配图需要登录 Bearer，用于调用服务器图片中转。"
        base = _server_proxy_base()
        final_prompt = (
            f"{clean_prompt}\n\n"
            f"画面比例要求：{_image_ratio_instruction(aspect_ratio)}。"
            "适合微信公众号正文头图或文章配图，主体完整，留白干净，不要文字、不要水印、不要按钮。"
        )
        payload = {
            "prompt": final_prompt,
            "model": (model or "gpt-image-2").strip() or "gpt-image-2",
            "n": 1,
            "quality": "high",
            "size": _image_size_for_aspect_ratio(aspect_ratio),
            "response_format": "url",
        }
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "X-Installation-Id": _installation_id_from_request(request, current_user.id),
        }
        async with httpx.AsyncClient(timeout=180.0, trust_env=False) as client:
            resp = await client.post(f"{base}/api/comfly-proxy/v1/images/generations", json=payload, headers=headers)
        if resp.status_code >= 400:
            detail = _response_error_detail(resp)
            logger.warning(
                "[wechat-article] image proxy failed user_id=%s status=%s detail=%s",
                current_user.id,
                resp.status_code,
                detail[:500],
            )
            return "", f"服务器图片中转失败 HTTP {resp.status_code}: {detail[:500]}"
        data = resp.json() if resp.content else {}
        image_url = _extract_image_url(data)
        if not image_url:
            detail = _response_error_detail(resp)
            logger.warning(
                "[wechat-article] image proxy returned no image user_id=%s detail=%s",
                current_user.id,
                detail[:500],
            )
            return "", f"服务器图片中转未返回图片地址或 b64_json：{detail[:500]}"
        return image_url, ""
    except Exception as exc:
        logger.warning("[wechat-article] image generation skipped/failed: %s", exc)
        return "", str(exc)


async def _generate_article_images(
    request: Request,
    current_user: _ServerUser,
    db: Session,
    prompt: str,
    model: str,
    aspect_ratio: str,
    count: int,
) -> tuple[List[str], List[str]]:
    count = _clamp_image_count(count)
    base_prompt = (prompt or "").strip()
    if not base_prompt:
        return [], []
    roles = [
        "文章头图：概括全文主题，适合放在标题下方。",
        "正文章节配图：承接文章中段观点，画面更偏场景和细节。",
        "正文案例配图：表现读者痛点、工作场景或方法落地。",
        "数据洞察配图：抽象表现趋势、增长、运营分析，不要出现文字。",
        "结尾配图：收束文章情绪，干净、有行动感、适合结尾前。",
    ]
    semaphore = asyncio.Semaphore(min(_IMAGE_GENERATION_CONCURRENCY, count))

    async def generate_one(idx: int) -> tuple[int, str, str, int]:
        role = roles[idx] if idx < len(roles) else f"文章配图 {idx + 1}"
        prompt_i = f"{base_prompt}\n\n用途：{role}"
        last_error = ""
        async with semaphore:
            for attempt in range(1, _IMAGE_GENERATION_RETRIES + 1):
                url, err = await _generate_article_image(
                    request=request,
                    current_user=current_user,
                    db=db,
                    prompt=prompt_i,
                    model=model,
                    aspect_ratio=aspect_ratio,
                )
                if url:
                    return idx, url, "", attempt
                last_error = err or "图片生成未返回结果"
                if attempt < _IMAGE_GENERATION_RETRIES:
                    await asyncio.sleep(0.8 * attempt)
        return idx, "", last_error, _IMAGE_GENERATION_RETRIES

    results = await asyncio.gather(*(generate_one(idx) for idx in range(count)))
    results.sort(key=lambda item: item[0])
    urls: List[str] = []
    errors: List[str] = []
    for idx, url, err, attempts in results:
        if url:
            urls.append(url)
        elif err:
            errors.append(f"第 {idx + 1} 张（已重试 {attempts} 次）：{err}")
    return urls, errors


def _insert_article_images(markdown: str, image_urls: List[str]) -> str:
    urls = [u for u in image_urls if isinstance(u, str) and u.strip()]
    if not urls:
        return markdown
    lines = (markdown or "").splitlines()
    if not lines:
        return "\n\n".join(f"![文章配图 {idx + 1}]({url})" for idx, url in enumerate(urls))

    heading_indexes = [idx for idx, line in enumerate(lines) if re.match(r"^\s{0,3}#{1,3}\s+", line)]
    insert_after = []
    if heading_indexes:
        insert_after.append(heading_indexes[0])
        insert_after.extend(heading_indexes[1:])
    if not insert_after:
        insert_after = [0]

    mapping: Dict[int, List[str]] = {}
    for idx, url in enumerate(urls):
        anchor = insert_after[min(idx, len(insert_after) - 1)]
        mapping.setdefault(anchor, []).append(url)

    out: List[str] = []
    for idx, line in enumerate(lines):
        out.append(line)
        for url in mapping.get(idx, []):
            out.extend(["", f"![文章配图]({url})"])
    if not out:
        out = [f"![文章配图]({urls[0]})"]
    return "\n".join(out)


def _insert_article_image(markdown: str, image_url: str, image_prompt: str) -> str:
    if image_url:
        return _insert_article_images(markdown, [image_url])
    return markdown


def _dedupe_urls(urls: List[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for raw in urls:
        url = str(raw or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        out.append(url)
    return out


def _selected_asset_urls(
    asset_ids: List[str],
    request: Request,
    db: Session,
    user_id: int,
) -> List[str]:
    urls: List[str] = []
    for raw in asset_ids or []:
        aid = str(raw or "").strip()
        if not aid:
            continue
        row = db.query(Asset).filter(Asset.asset_id == aid, Asset.user_id == user_id).first()
        if not row or (row.media_type or "").lower() != "image":
            continue
        public_url = get_asset_public_url(aid, user_id, request, db)
        local_url = build_asset_file_url(request, aid, expiry_sec=86400)
        urls.append(public_url or local_url or "")
    return _dedupe_urls(urls)


def _render_markdown_to_wechat_html(markdown: str, theme_name: str = "professional-clean") -> str:
    colors = _theme(theme_name)
    lines = (markdown or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: List[str] = []
    paragraph: List[str] = []
    list_items: List[str] = []
    code_lines: List[str] = []
    in_code = False

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            text = "<br>".join(_inline(x.strip()) for x in paragraph if x.strip())
            blocks.append(
                f'<p style="margin:0 0 16px;color:{colors["text"]};font-size:16px;line-height:1.85;">{text}</p>'
            )
            paragraph = []

    def flush_list() -> None:
        nonlocal list_items
        if list_items:
            inner = "".join(
                f'<li style="margin:0 0 8px;color:{colors["text"]};font-size:16px;line-height:1.75;">{_inline(x)}</li>'
                for x in list_items
            )
            blocks.append(f'<ul style="margin:0 0 18px;padding-left:1.2em;">{inner}</ul>')
            list_items = []

    def flush_code() -> None:
        nonlocal code_lines
        if code_lines:
            code = _escape("\n".join(code_lines))
            blocks.append(
                '<pre style="margin:0 0 18px;padding:12px 14px;white-space:pre-wrap;'
                f'background:#f6f7f9;border:1px solid {colors["border"]};border-radius:8px;'
                f'color:{colors["text"]};font-size:13px;line-height:1.65;">{code}</pre>'
            )
            code_lines = []

    for raw in lines:
        line = raw.rstrip()
        if line.strip().startswith("```"):
            if in_code:
                flush_code()
                in_code = False
            else:
                flush_paragraph()
                flush_list()
                in_code = True
            continue
        if in_code:
            code_lines.append(line)
            continue

        if not line.strip():
            flush_paragraph()
            flush_list()
            continue

        img = re.match(r"^\s*!\[([^\]]*)\]\(([^)]+)\)\s*$", line)
        if img:
            flush_paragraph()
            flush_list()
            alt = _escape(img.group(1))
            src = _escape(img.group(2).strip())
            blocks.append(
                f'<p style="margin:20px 0;text-align:center;"><img src="{src}" alt="{alt}" '
                'style="max-width:100%;height:auto;border-radius:8px;display:inline-block;"></p>'
            )
            continue

        heading = re.match(r"^\s{0,3}(#{1,3})\s+(.+?)\s*$", line)
        if heading:
            flush_paragraph()
            flush_list()
            level = len(heading.group(1))
            text = _inline(heading.group(2))
            if level == 1:
                blocks.append(
                    f'<h1 style="margin:0 0 20px;color:{colors["text"]};font-size:24px;line-height:1.35;font-weight:800;">{text}</h1>'
                )
            elif level == 2:
                blocks.append(
                    f'<h2 style="margin:28px 0 14px;padding-left:10px;border-left:4px solid {colors["accent"]};'
                    f'color:{colors["text"]};font-size:19px;line-height:1.45;font-weight:800;">{text}</h2>'
                )
            else:
                blocks.append(
                    f'<h3 style="margin:22px 0 12px;color:{colors["accent"]};font-size:17px;line-height:1.45;font-weight:800;">{text}</h3>'
                )
            continue

        quote = re.match(r"^\s*>\s?(.*)$", line)
        if quote:
            flush_paragraph()
            flush_list()
            blocks.append(
                f'<blockquote style="margin:0 0 18px;padding:12px 14px;background:{colors["quote_bg"]};'
                f'border-left:4px solid {colors["accent"]};border-radius:8px;color:{colors["muted"]};'
                f'font-size:15px;line-height:1.75;">{_inline(quote.group(1))}</blockquote>'
            )
            continue

        li = re.match(r"^\s*(?:[-*+]|\d+[.)])\s+(.+?)\s*$", line)
        if li:
            flush_paragraph()
            list_items.append(li.group(1))
            continue

        paragraph.append(line)

    flush_code()
    flush_paragraph()
    flush_list()
    return (
        f'<section style="box-sizing:border-box;padding:4px 0;color:{colors["text"]};'
        'font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;">'
        + "".join(blocks)
        + "</section>"
    )


def _replace_image_src(html_text: str, mapping: Dict[str, str]) -> str:
    out = html_text
    for old, new in mapping.items():
        if old and new and old != new:
            out = out.replace(f'src="{html.escape(old, quote=True)}"', f'src="{html.escape(new, quote=True)}"')
            out = out.replace(f"src='{html.escape(old, quote=True)}'", f"src='{html.escape(new, quote=True)}'")
            out = out.replace(old, new)
    return out


def _wechat_error_detail(action: str, data: Any, http_status: int = 0) -> Dict[str, Any]:
    payload = data if isinstance(data, dict) else {}
    errcode = payload.get("errcode")
    errmsg = str(payload.get("errmsg") or data or "").strip()
    message = f"微信公众号{action}失败"
    if errmsg:
        message += f": {errmsg}"
    hint = ""
    lower_msg = errmsg.lower()
    if (
        "not in whitelist" in lower_msg
        or "invalid ip" in lower_msg
        or "ip whitelist" in lower_msg
        or "白名单" in errmsg
    ):
        hint = "请将微信返回的 IP 加入微信公众号后台的接口 IP 白名单。"
    detail: Dict[str, Any] = {
        "message": message,
        "errcode": errcode,
        "errmsg": errmsg,
        "raw": data,
    }
    if http_status:
        detail["http_status"] = http_status
    if hint:
        detail["hint"] = hint
    return detail


def _raise_wechat_error(action: str, data: Any, http_status: int = 0) -> None:
    raise HTTPException(status_code=400, detail=_wechat_error_detail(action, data, http_status))


async def _wechat_get_access_token(appid: str, secret: str) -> str:
    async with httpx.AsyncClient(timeout=_API_TIMEOUT) as client:
        r = await client.get(
            "https://api.weixin.qq.com/cgi-bin/token",
            params={"grant_type": "client_credential", "appid": appid, "secret": secret},
        )
    data = r.json() if r.content else {}
    token = data.get("access_token")
    if not token:
        _raise_wechat_error("access_token 获取", data, r.status_code)
    return str(token)


async def _wechat_upload_bytes(access_token: str, data: bytes, filename: str, permanent: bool) -> str:
    content_type = mimetypes.guess_type(filename)[0] or "image/png"
    files = {"media": (filename, data, content_type)}
    if permanent:
        url = "https://api.weixin.qq.com/cgi-bin/material/add_material"
        params = {"access_token": access_token, "type": "image"}
    else:
        url = "https://api.weixin.qq.com/cgi-bin/media/uploadimg"
        params = {"access_token": access_token}
    async with httpx.AsyncClient(timeout=_API_TIMEOUT) as client:
        r = await client.post(url, params=params, files=files)
    resp = r.json() if r.content else {}
    if permanent:
        media_id = resp.get("media_id")
        if not media_id:
            _raise_wechat_error("封面上传", resp, r.status_code)
        return str(media_id)
    wx_url = resp.get("url")
    if not wx_url:
        _raise_wechat_error("正文图片上传", resp, r.status_code)
    return str(wx_url)


async def _download_url(url: str) -> tuple[bytes, str]:
    async with httpx.AsyncClient(timeout=_API_TIMEOUT, follow_redirects=True) as client:
        r = await client.get(url)
    if r.status_code >= 400:
        raise HTTPException(status_code=400, detail=f"下载图片失败 HTTP {r.status_code}: {url[:120]}")
    suffix = ".png"
    ctype = r.headers.get("content-type") or ""
    guessed = mimetypes.guess_extension(ctype.split(";")[0].strip())
    if guessed:
        suffix = guessed
    return r.content, "wechat-image" + suffix


def _asset_image_bytes(asset: Asset) -> tuple[bytes, str]:
    filename = asset.filename or f"{asset.asset_id}.png"
    path = _ASSETS_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"素材本地文件不存在: {asset.asset_id}")
    return path.read_bytes(), filename


def _find_asset(db: Session, user_id: int, asset_id: str) -> Asset:
    row = db.query(Asset).filter(Asset.user_id == user_id, Asset.asset_id == asset_id).first()
    if not row:
        raise HTTPException(status_code=404, detail=f"素材不存在: {asset_id}")
    if (row.media_type or "").lower() != "image":
        raise HTTPException(status_code=400, detail="公众号封面/正文图片只支持图片素材")
    return row


async def _image_source_to_bytes(src: str, db: Session, user_id: int) -> Optional[tuple[bytes, str]]:
    raw = (src or "").strip()
    if not raw:
        return None
    if raw.startswith("data:image/"):
        m = re.match(r"^data:image/([a-zA-Z0-9.+-]+);base64,(.+)$", raw, re.DOTALL)
        if not m:
            return None
        suffix = "." + (m.group(1).lower().replace("jpeg", "jpg"))
        return base64.b64decode(m.group(2)), "wechat-inline" + suffix
    if raw.startswith("asset:"):
        asset = _find_asset(db, user_id, raw.split(":", 1)[1].strip())
        return _asset_image_bytes(asset)
    if re.match(r"^[a-f0-9]{8,32}$", raw, re.IGNORECASE):
        asset = db.query(Asset).filter(Asset.user_id == user_id, Asset.asset_id == raw).first()
        if asset and (asset.media_type or "").lower() == "image":
            return _asset_image_bytes(asset)
    if raw.startswith("http://") or raw.startswith("https://"):
        return await _download_url(raw)
    p = Path(raw)
    if not p.is_absolute():
        p = _BASE_DIR / raw
    if p.exists() and p.is_file():
        return p.read_bytes(), p.name
    return None


async def _upload_article_images(html_text: str, access_token: str, db: Session, user_id: int) -> tuple[str, Dict[str, str]]:
    srcs = []
    for m in re.finditer(r'<img[^>]+src=["\']([^"\']+)["\']', html_text, re.IGNORECASE):
        src = html.unescape(m.group(1)).strip()
        if src and src not in srcs:
            srcs.append(src)
    mapping: Dict[str, str] = {}
    for src in srcs:
        try:
            resolved = await _image_source_to_bytes(src, db, user_id)
            if not resolved:
                continue
            data, filename = resolved
            wx_url = await _wechat_upload_bytes(access_token, data, filename, permanent=False)
            mapping[src] = wx_url
        except Exception as exc:
            logger.warning("[wechat-article] upload article image failed src=%s err=%s", src[:120], exc)
    return _replace_image_src(html_text, mapping), mapping


async def _create_wechat_draft(
    access_token: str,
    title: str,
    article_html: str,
    digest: str,
    author: str,
    thumb_media_id: str = "",
) -> str:
    article: Dict[str, Any] = {
        "title": title,
        "author": author or "",
        "digest": digest or "",
        "content": article_html,
        "show_cover_pic": 0,
    }
    if thumb_media_id:
        article["thumb_media_id"] = thumb_media_id
    async with httpx.AsyncClient(timeout=_API_TIMEOUT) as client:
        r = await client.post(
            "https://api.weixin.qq.com/cgi-bin/draft/add",
            params={"access_token": access_token},
            content=json.dumps({"articles": [article]}, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
    data = r.json() if r.content else {}
    media_id = data.get("media_id")
    if not media_id:
        _raise_wechat_error("草稿创建", data, r.status_code)
    return str(media_id)


async def _default_thumb_media_id(access_token: str) -> str:
    # 1x1 white PNG. WeChat draft/add normally requires thumb_media_id for articles.
    raw = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
    )
    return await _wechat_upload_bytes(
        access_token,
        base64.b64decode(raw),
        "wechat-default-cover.png",
        permanent=True,
    )


def _record_draft(user_id: int, item: Dict[str, Any]) -> Dict[str, Any]:
    doc = _load_doc(user_id)
    drafts = [x for x in doc.get("drafts", []) if isinstance(x, dict)]
    drafts.insert(0, item)
    doc["drafts"] = drafts[:200]
    _save_doc(user_id, doc)
    return item


def _draft_id(media_id: str, title: str) -> str:
    return hashlib.sha1(f"{media_id}:{title}:{time.time()}".encode("utf-8")).hexdigest()[:16]


def _exc_detail(exc: Exception) -> str:
    if isinstance(exc, HTTPException):
        detail = exc.detail
        if isinstance(detail, dict):
            parts = []
            message = str(detail.get("message") or "").strip()
            errmsg = str(detail.get("errmsg") or "").strip()
            hint = str(detail.get("hint") or "").strip()
            errcode = detail.get("errcode")
            if message:
                parts.append(message)
            if errcode not in (None, ""):
                parts.append(f"错误码：{errcode}")
            if errmsg and errmsg not in message:
                parts.append(f"微信返回：{errmsg}")
            if hint:
                parts.append(hint)
            if parts:
                return "\n".join(parts).strip()
        return str(detail or "").strip()
    return str(exc or "").strip()


def _record_local_article(
    user_id: int,
    *,
    title: str,
    digest: str,
    author: str,
    theme_name: str,
    cover_asset_id: str = "",
    cover_image_url: str = "",
    markdown: str,
    article_html: str,
    image_uploads: int = 0,
    push_error: str = "",
) -> Dict[str, Any]:
    item: Dict[str, Any] = {
        "id": _draft_id("local", title),
        "media_id": "",
        "status": "local_saved",
        "push_status": "local_saved",
        "title": title,
        "digest": digest,
        "author": author,
        "theme": theme_name,
        "cover_asset_id": cover_asset_id,
        "cover_image_url": cover_image_url,
        "has_cover": bool(cover_asset_id or cover_image_url),
        "image_uploads": image_uploads,
        "markdown": markdown,
        "html": article_html,
        "created_at": _now_iso(),
        "local_saved_at": _now_iso(),
    }
    if push_error:
        item["push_error"] = push_error[:500]
    return _record_draft(user_id, item)


@router.get("/api/wechat-article/config")
def get_wechat_article_config(current_user: _ServerUser = Depends(get_current_user_media_edit)):
    return _public_config(_load_config(current_user.id))


@router.put("/api/wechat-article/config")
def put_wechat_article_config(
    body: WechatArticleConfigIn,
    current_user: _ServerUser = Depends(get_current_user_media_edit),
):
    cfg = _save_config(current_user.id, body.model_dump())
    return _public_config(cfg)


@router.post("/api/wechat-article/preview")
def preview_wechat_article(
    body: WechatArticlePreviewIn,
):
    title = _extract_title(body.markdown, body.title)
    article_html = _render_markdown_to_wechat_html(body.markdown, body.theme)
    return {
        "ok": True,
        "title": title,
        "digest": _digest(body.markdown),
        "html": article_html,
        "theme": body.theme if body.theme in _THEMES else "professional-clean",
    }


@router.post("/api/wechat-article/generate")
async def generate_wechat_article(
    body: WechatArticleGenerateIn,
    request: Request,
    db: Session = Depends(get_db),
):
    idea = (body.idea or "").strip()
    if not idea:
        raise HTTPException(status_code=400, detail="请输入文章主题或想法")
    theme_name = body.theme if body.theme in _THEMES else "professional-clean"
    token = _raw_token_from_request(request)
    current_user = _optional_local_user(request, db)
    installation_id = _installation_id_from_request(request, current_user.id if current_user else 0)
    warnings: List[str] = []
    try:
        article = await _call_article_writer(body, token, installation_id)
    except Exception as exc:
        logger.warning("[wechat-article] AI article writer fallback user_id=%s err=%s", current_user.id if current_user else 0, exc)
        warnings.append("AI 成稿服务暂不可用，已使用本地结构化草稿兜底。")
        article = _normalize_generated_article({}, idea, body.include_images)

    image_url = ""
    image_urls: List[str] = []
    image_error = ""
    image_errors: List[str] = []
    selected_urls = _dedupe_urls(list(body.selected_image_urls or []))
    if body.selected_asset_ids:
        selected_urls.extend(
            _selected_asset_urls(
                body.selected_asset_ids,
                request=request,
                db=db,
                user_id=current_user.id if current_user else 0,
            )
        )
        selected_urls = _dedupe_urls(selected_urls)
    if body.include_images:
        image_urls, image_errors = await _generate_article_images(
            request=request,
            current_user=current_user or _ServerUser(id=0),
            db=db,
            prompt=article.get("image_prompt") or "",
            model=body.image_model,
            aspect_ratio=body.image_aspect_ratio,
            count=body.image_count,
        )
        image_url = image_urls[0] if image_urls else ""
        image_error = "；".join(image_errors[:3])
        if image_error:
            logger.warning(
                "[wechat-article] image generation partial user_id=%s requested=%s generated=%s error=%s",
                current_user.id if current_user else 0,
                _clamp_image_count(body.image_count),
                len(image_urls),
                image_error[:800],
            )
            if image_urls:
                warnings.append(f"部分自动配图生成失败：{image_error}")
            else:
                warnings.append(f"自动配图失败：{image_error}")

    all_image_urls = _dedupe_urls(selected_urls + image_urls)
    image_url = all_image_urls[0] if all_image_urls else image_url
    markdown = _insert_article_images(article["markdown"], all_image_urls)
    title = _extract_title(markdown, article["title"])
    digest = _digest(markdown, article["digest"])
    article_html = _render_markdown_to_wechat_html(markdown, theme_name)
    return {
        "ok": True,
        "title": title,
        "digest": digest,
        "markdown": markdown,
        "html": article_html,
        "theme": theme_name,
        "image": {
            "enabled": bool(body.include_images),
            "url": image_url,
            "urls": all_image_urls,
            "count": _clamp_image_count(body.image_count),
            "generated_count": len(image_urls),
            "selected_count": len(selected_urls),
            "prompt": article.get("image_prompt") or "",
            "aspect_ratio": body.image_aspect_ratio or "3:2",
            "error": image_error,
            "errors": image_errors,
        },
        "warnings": warnings,
    }


@router.post("/api/wechat-article/drafts")
async def create_wechat_article_draft(
    body: WechatArticleDraftIn,
    request: Request,
    current_user: _ServerUser = Depends(get_current_user_media_edit),
    db: Session = Depends(get_db),
):
    markdown = (body.markdown or "").strip()
    if not markdown:
        raise HTTPException(status_code=400, detail="请提供公众号文章 markdown")
    cfg = _load_config(current_user.id)
    appid = (cfg.get("appid") or "").strip()
    secret = (cfg.get("secret") or "").strip()

    title = _extract_title(markdown, body.title)
    digest = _digest(markdown, body.digest)
    theme_name = body.theme if body.theme in _THEMES else (cfg.get("theme") or "professional-clean")
    author = (body.author or cfg.get("author") or "").strip()
    article_html = _render_markdown_to_wechat_html(markdown, theme_name)
    cover_asset_id = (body.cover_asset_id or "").strip()
    cover_image_url = (body.cover_image_url or "").strip()

    if not appid or not secret:
        item = _record_local_article(
            current_user.id,
            title=title,
            digest=digest,
            author=author,
            theme_name=theme_name,
            cover_asset_id=cover_asset_id,
            cover_image_url=cover_image_url,
            markdown=markdown,
            article_html=article_html,
            push_error="公众号 AppID 或 AppSecret 未配置",
        )
        return {
            "ok": True,
            "pushed": False,
            "push_status": "local_saved",
            "draft": item,
            "message": "文章已保存到公众号文章页面，配置公众号后可再次推送。",
        }

    image_mapping: Dict[str, str] = {}
    try:
        token = await _wechat_get_access_token(appid, secret)
        if body.upload_article_images:
            article_html, image_mapping = await _upload_article_images(article_html, token, db, current_user.id)

        thumb_media_id = ""
        if cover_asset_id:
            asset = _find_asset(db, current_user.id, cover_asset_id)
            data, filename = _asset_image_bytes(asset)
            thumb_media_id = await _wechat_upload_bytes(token, data, filename, permanent=True)
        elif cover_image_url:
            data, filename = await _download_url(cover_image_url)
            thumb_media_id = await _wechat_upload_bytes(token, data, filename, permanent=True)
        else:
            thumb_media_id = await _default_thumb_media_id(token)

        media_id = await _create_wechat_draft(
            access_token=token,
            title=title,
            article_html=article_html,
            digest=digest,
            author=author,
            thumb_media_id=thumb_media_id,
        )
    except Exception as exc:
        detail = _exc_detail(exc)
        logger.warning(
            "[wechat-article] draft push failed; saved locally user_id=%s title=%s err=%s",
            current_user.id,
            title[:80],
            detail[:500],
        )
        item = _record_local_article(
            current_user.id,
            title=title,
            digest=digest,
            author=author,
            theme_name=theme_name,
            cover_asset_id=cover_asset_id,
            cover_image_url=cover_image_url,
            markdown=markdown,
            article_html=article_html,
            image_uploads=len(image_mapping),
            push_error=detail,
        )
        return {
            "ok": True,
            "pushed": False,
            "push_status": "local_saved",
            "draft": item,
            "message": "文章已保存到公众号文章页面，稍后可重新推送到草稿箱。",
        }

    item = _record_draft(
        current_user.id,
        {
            "id": _draft_id(media_id, title),
            "media_id": media_id,
            "status": "pushed",
            "push_status": "pushed",
            "title": title,
            "digest": digest,
            "author": author,
            "theme": theme_name,
            "cover_asset_id": cover_asset_id,
            "has_cover": bool(thumb_media_id),
            "image_uploads": len(image_mapping),
            "markdown": markdown,
            "html": article_html,
            "created_at": _now_iso(),
        },
    )
    logger.info(
        "[wechat-article] draft created user_id=%s media_id=%s title=%s images=%s",
        current_user.id,
        media_id,
        title[:80],
        len(image_mapping),
    )
    return {"ok": True, "pushed": True, "push_status": "pushed", "draft": item}


@router.post("/api/wechat-article/pipeline")
async def run_wechat_article_pipeline(
    body: WechatArticlePipelineIn,
    request: Request,
    current_user: _ServerUser = Depends(get_current_user_media_edit),
    db: Session = Depends(get_db),
):
    idea = (body.idea or body.topic or "").strip()
    if not idea:
        raise HTTPException(status_code=400, detail="请输入公众号文章主题或想法")
    theme_name = body.theme if body.theme in _THEMES else "professional-clean"
    generated = await generate_wechat_article(
        WechatArticleGenerateIn(
            idea=idea,
            style=body.style,
            audience=body.audience,
            theme=theme_name,
            include_images=body.include_images,
            image_model=body.image_model or "gpt-image-2",
            image_aspect_ratio=body.image_aspect_ratio or "16:9",
            image_count=body.image_count or 3,
            selected_image_urls=body.selected_image_urls,
            selected_asset_ids=body.selected_asset_ids,
        ),
        request=request,
        db=db,
    )
    image_info = generated.get("image") if isinstance(generated.get("image"), dict) else {}
    image_urls = image_info.get("urls") if isinstance(image_info, dict) else []
    cover_image_url = ""
    if isinstance(image_urls, list) and image_urls:
        cover_image_url = str(image_urls[0] or "").strip()
    draft_result = await create_wechat_article_draft(
        WechatArticleDraftIn(
            title=str(generated.get("title") or ""),
            markdown=str(generated.get("markdown") or ""),
            theme=theme_name,
            digest=str(generated.get("digest") or ""),
            cover_image_url=cover_image_url,
            upload_article_images=body.upload_article_images,
        ),
        request=request,
        current_user=current_user,
        db=db,
    )
    draft = draft_result.get("draft") if isinstance(draft_result, dict) else {}
    pushed = bool(draft_result.get("pushed")) if isinstance(draft_result, dict) else bool(draft.get("media_id"))
    push_status = str(draft_result.get("push_status") or draft.get("push_status") or "").strip() if isinstance(draft_result, dict) else ""
    logger.info(
        "[wechat-article] pipeline completed user_id=%s title=%s media_id=%s images=%s",
        current_user.id,
        str(generated.get("title") or "")[:80],
        draft.get("media_id") if isinstance(draft, dict) else "",
        len(image_urls) if isinstance(image_urls, list) else 0,
    )
    return {
        "ok": True,
        "title": generated.get("title"),
        "digest": generated.get("digest"),
        "markdown": generated.get("markdown"),
        "html": generated.get("html"),
        "image": generated.get("image"),
        "draft": draft,
        "pushed": pushed,
        "push_status": push_status or ("pushed" if pushed else "local_saved"),
        "warnings": (generated.get("warnings") or []) + ([] if pushed else ["公众号推送未完成，文章已保存到本地。"]),
    }


@router.get("/api/wechat-article/drafts")
def list_wechat_article_drafts(
    current_user: _ServerUser = Depends(get_current_user_media_edit),
):
    doc = _load_doc(current_user.id)
    drafts = []
    for item in doc.get("drafts", []):
        if not isinstance(item, dict):
            continue
        x = dict(item)
        # Keep list payload light; detail endpoint can return body content later if needed.
        x.pop("html", None)
        x.pop("markdown", None)
        drafts.append(x)
    return {"drafts": drafts}


@router.get("/api/wechat-article/drafts/{draft_id}")
def get_wechat_article_draft(
    draft_id: str,
    current_user: _ServerUser = Depends(get_current_user_media_edit),
):
    for item in _load_doc(current_user.id).get("drafts", []):
        if isinstance(item, dict) and item.get("id") == draft_id:
            return item
    raise HTTPException(status_code=404, detail="草稿记录不存在")


@router.delete("/api/wechat-article/drafts/{draft_id}")
def delete_wechat_article_draft(
    draft_id: str,
    current_user: _ServerUser = Depends(get_current_user_media_edit),
):
    doc = _load_doc(current_user.id)
    before = len(doc.get("drafts", []))
    doc["drafts"] = [
        item for item in doc.get("drafts", [])
        if not (isinstance(item, dict) and item.get("id") == draft_id)
    ]
    _save_doc(current_user.id, doc)
    return {"ok": True, "deleted": before - len(doc["drafts"])}
