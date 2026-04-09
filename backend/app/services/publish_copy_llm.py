"""发布前：用与会话一致的模型生成标题、正文话术与话题（在线版走服务器速推聚合 completions）。"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, Optional, Tuple

import httpx
from fastapi import HTTPException

from ..core.config import settings

logger = logging.getLogger(__name__)


class PublishCopyLLMError(Exception):
    """无法生成发布文案（未配置模型或模型输出不可用）。"""


def _extract_json_object(text: str) -> dict:
    raw = (text or "").strip()
    if not raw:
        raise PublishCopyLLMError("模型返回为空")
    m = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", raw)
    if m:
        blob = m.group(1)
    else:
        i = raw.find("{")
        j = raw.rfind("}")
        if i < 0 or j <= i:
            raise PublishCopyLLMError("模型返回中未找到 JSON 对象")
        blob = raw[i : j + 1]
    try:
        out = json.loads(blob)
    except json.JSONDecodeError as e:
        raise PublishCopyLLMError(f"模型返回非合法 JSON: {e}") from e
    if not isinstance(out, dict):
        raise PublishCopyLLMError("模型 JSON 根须为对象")
    return out


def _platform_copy_limits(platform: str, media_type: str) -> str:
    p = (platform or "").strip().lower()
    mt = (media_type or "").strip().lower()
    lines = []
    if p == "xiaohongshu":
        lines.append("标题：最多 20 个字（含标点）。")
        lines.append("正文/描述：最多约 1000 字，可分多段。")
    elif p == "douyin":
        if mt == "image":
            lines.append("标题：最多 20 个字。")
        else:
            lines.append("标题：最多 30 个字。")
        lines.append("描述：宜控制在 400 字以内（平台描述与话题合并后总长约 ≤500 字）。")
    elif p == "toutiao":
        lines.append("标题：最多 30 个字。")
        lines.append("简介/正文：宜充实、口语化，最多约 5000 字以内。")
    else:
        lines.append("标题：宜简短有力，不超过 30 字。")
        lines.append("描述：清晰说明内容亮点，避免空洞套话。")
    return "\n".join(lines)


def _sutui_route(
    asb: str, raw_tok: str, inner_model: str
) -> Tuple[Dict[str, Any], str, Dict[str, str]]:
    inner = (inner_model or "").strip()
    if not inner:
        raise PublishCopyLLMError("速推模型 ID 为空")
    cfg: Dict[str, Any] = {
        "base_url": "",
        "api_key": "",
        "model_name": inner,
        "provider": "sutui",
    }
    url = f"{asb.rstrip('/')}/api/sutui-chat/completions"
    hdrs = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {raw_tok}",
    }
    return cfg, url, hdrs


async def generate_publish_copy(
    *,
    platform: str,
    media_type: str,
    asset_prompt: str,
    filename: str,
    hint_title: str = "",
    hint_desc: str = "",
    hint_tags: str = "",
    raw_token: Optional[str] = None,
    chat_model: Optional[str] = None,
) -> Tuple[str, str, str]:
    """
    返回 (title, description, tags)。
    tags 为逗号分隔短话题词，勿含 #；发布驱动会按需转成 #话题。

    在线版：优先用请求头传入的 chat_model（与会话所选 sutui/xxx 或直连 provider/model 一致）；
    未传时请求认证中心 /auth/me 的 preferred_model；仍无法解析时再尝试本机 API Key（_pick_default_model）。
    """
    from ..api.chat import _chat_openai, _pick_default_model, _resolve_config

    edition = (getattr(settings, "lobster_edition", None) or "online").strip().lower()
    asb = (getattr(settings, "auth_server_base", None) or "").strip().rstrip("/")
    raw_tok = (raw_token or "").strip()
    req_model = (chat_model or "").strip()

    cfg: Optional[Dict[str, Any]] = None
    override_url: Optional[str] = None
    override_headers: Optional[Dict[str, str]] = None

    if req_model.startswith("sutui/"):
        inner = req_model.split("/", 1)[1].strip()
        if edition == "online" and asb and raw_tok and inner:
            cfg, override_url, override_headers = _sutui_route(asb, raw_tok, inner)
        elif not inner:
            pass
        else:
            raise PublishCopyLLMError("未配置 AUTH_SERVER_BASE 或缺少登录 Token，无法使用速推生成发布文案")
    elif req_model and "/" in req_model:
        cfg = _resolve_config(req_model)

    if cfg is None and edition == "online" and asb and raw_tok:
        try:
            async with httpx.AsyncClient(timeout=15.0, trust_env=False) as c:
                r = await c.get(
                    f"{asb}/auth/me",
                    headers={"Authorization": f"Bearer {raw_tok}"},
                )
            if r.status_code == 200:
                data = r.json() if r.content else {}
                pm = (data.get("preferred_model") or "").strip()
                if pm.startswith("sutui/"):
                    inner = pm.split("/", 1)[1].strip()
                    if inner:
                        cfg, override_url, override_headers = _sutui_route(asb, raw_tok, inner)
                elif pm and "/" in pm:
                    cfg = _resolve_config(pm)
        except PublishCopyLLMError:
            raise
        except Exception as e:
            logger.warning("[PUBLISH-COPY-LLM] 读取 auth/me preferred_model 失败: %s", e)

    if cfg is None:
        try:
            model = _pick_default_model()
            cfg = _resolve_config(model)
        except HTTPException as e:
            d = e.detail
            msg = d if isinstance(d, str) else str(d)
            raise PublishCopyLLMError(msg or "未配置对话模型") from e

    if cfg is None:
        raise PublishCopyLLMError("未配置任何可用对话模型")

    if (cfg.get("provider") or "").strip() == "sutui":
        if not override_url or not override_headers:
            if asb and raw_tok:
                cfg, override_url, override_headers = _sutui_route(
                    asb, raw_tok, str(cfg.get("model_name") or "")
                )
            else:
                raise PublishCopyLLMError("未配置 AUTH_SERVER_BASE 或缺少登录 Token，无法使用速推生成发布文案")

    limits = _platform_copy_limits(platform, media_type)
    plat_cn = {
        "douyin": "抖音",
        "xiaohongshu": "小红书",
        "toutiao": "今日头条/头条号",
        "bilibili": "B站",
        "kuaishou": "快手",
    }.get((platform or "").strip().lower(), platform or "该平台")

    sys = (
        "你是短视频与图文「发布文案」助手，只根据给定素材信息写可上架的标题与话术。\n"
        "【硬性规则】\n"
        "1. 只输出一个 JSON 对象，不要 Markdown 说明、不要代码块外的任何文字。\n"
        "2. JSON 键固定为：title（字符串）、description（字符串）、tags（字符串）。\n"
        "3. tags 为逗号分隔的短话题词（2～8 个为宜），不要写 # 号；不要出现 auto、task.get_result、"
        "invoke_capability 等技术/内部词汇；不要英文能力名。\n"
        "4. 内容须与素材主题一致，口语自然，避免虚假宣传；不确定的细节不要编造具体数据。\n"
        "5. 严格遵守用户给出的字数/平台约束。\n"
    )
    user_parts = [
        f"发布平台：{plat_cn}。素材类型：{media_type or 'unknown'}。",
        limits,
        f"素材文件名：{(filename or '')[:200]}",
        "【生成/脚本参考】\n" + ((asset_prompt or "").strip()[:3500] or "（无正文提示，请根据文件名与类型自拟主题）"),
    ]
    if (hint_title or "").strip():
        user_parts.append("【用户希望的标题方向（可改写）】\n" + hint_title.strip()[:500])
    if (hint_desc or "").strip():
        user_parts.append("【用户希望的正文要点（可改写合并）】\n" + hint_desc.strip()[:2000])
    if (hint_tags or "").strip():
        user_parts.append("【用户希望的话题参考（过滤技术词后采用）】\n" + hint_tags.strip()[:500])
    user = "\n\n".join(user_parts)
    user += (
        "\n\n请输出 JSON：{\"title\":\"...\",\"description\":\"...\",\"tags\":\"话题1,话题2\"} "
        "（tags 可 \"\"）"
    )

    messages = [
        {"role": "system", "content": sys},
        {"role": "user", "content": user},
    ]
    reply = await _chat_openai(
        messages,
        cfg,
        [],
        raw_tok,
        sutui_token=None,
        override_url=override_url,
        override_headers=override_headers,
    )
    data = _extract_json_object(reply)
    title = str(data.get("title") or "").strip()
    desc = str(data.get("description") or "").strip()
    tags = str(data.get("tags") or "").strip()
    if not title and not desc:
        raise PublishCopyLLMError("模型未返回可用的 title/description")
    if not title:
        title = desc.split("\n", 1)[0].strip()[:120] or "作品分享"
    if not desc:
        desc = title
    logger.info(
        "[PUBLISH-COPY-LLM] platform=%s media=%s title_len=%d desc_len=%d tags_len=%d",
        platform,
        media_type,
        len(title),
        len(desc),
        len(tags),
    )
    return title, desc, tags
