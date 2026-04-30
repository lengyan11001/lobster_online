"""OpenClaw Gateway chat route helpers.

Keep this module separate from chat.py so the direct LLM + MCP orchestration
stays focused on the direct path. chat.py should only decide when to call this
route, not carry the Gateway implementation details.
"""

from __future__ import annotations

import json
import logging
import re
import base64
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import Request

from ..core.config import settings

try:
    from ..services.chat_route_mode import CHAT_ROUTE_MODE_OPENCLAW, get_chat_route_mode
except ImportError:
    CHAT_ROUTE_MODE_OPENCLAW = "openclaw"

    def get_chat_route_mode() -> str:
        return "direct"

try:
    from ..services.openclaw_tool_scope import classify_openclaw_tool_scope
except ImportError:
    class _OpenClawFallbackScope:
        intent = "legacy"
        allowed_tools = set()
        allowed_capabilities = None

        def headers(self) -> Dict[str, str]:
            return {}

        def system_hint(self) -> str:
            return ""

    def classify_openclaw_tool_scope(_msgs: List[Dict]) -> _OpenClawFallbackScope:
        return _OpenClawFallbackScope()

try:
    from .mcp_gateway import set_mcp_token_for_agent, set_openclaw_tool_scope_for_agent
except ImportError:
    from .mcp_gateway import set_mcp_token_for_agent

    def set_openclaw_tool_scope_for_agent(_agent_id: str, _headers: Dict[str, str]) -> None:
        return None


logger = logging.getLogger(__name__)
_BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
_USER_MEMORY_DIR = _BASE_DIR / "openclaw" / "user_memory"


OC_ONLY_CHAT_FAIL_DETAIL = (
    "已启用「仅 OpenClaw」但未配置 Gateway 或 Gateway 无有效回复。"
    "请配置 OPENCLAW_GATEWAY_URL、OPENCLAW_GATEWAY_TOKEN 并检查 OpenClaw 服务。"
)

OC_PREFIX_CHAT_FAIL_DETAIL = (
    "本句已使用 OpenClaw 前缀（如 /openclaw），仅走 Gateway；Gateway 未返回有效回复，且当前请求没有可用的直连/速推对话配置。"
    "若界面曾出现「401 status code (no body)」：多为 OpenClaw 调用上游模型时鉴权失败——请检查项目 openclaw/.env、openclaw.json 中的 Anthropic/OpenAI 等 Key，"
    "并确认龙虾后端 .env 的 OPENCLAW_GATEWAY_URL、OPENCLAW_GATEWAY_TOKEN 与 Gateway 一致。"
    "在线版在已配置 AUTH_SERVER_BASE 且本轮可解析为速推对话时，会自动回退到认证中心 sutui-chat。"
)


def openclaw_gateway_configured() -> bool:
    oc_base = (settings.openclaw_gateway_url or "").strip().rstrip("/")
    oc_token = (settings.openclaw_gateway_token or "").strip()
    return bool(oc_base and oc_token)


def _chat_route_mode() -> str:
    try:
        return get_chat_route_mode()
    except Exception as exc:
        logger.warning("[OPENCLAW] 读取智能对话路由配置失败，回退直连: %s", exc)
        return "direct"


def openclaw_only_chat_enabled() -> bool:
    return _chat_route_mode() == CHAT_ROUTE_MODE_OPENCLAW


def openclaw_chat_prefix_patterns() -> List[str]:
    raw = (getattr(settings, "lobster_openclaw_chat_prefixes", None) or "/openclaw").strip()
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    return sorted(parts, key=len, reverse=True)


def strip_openclaw_chat_prefix(raw_message: str) -> Tuple[str, bool]:
    """Return (message_without_prefix, matched). Empty prefix body is ignored."""
    orig = raw_message if raw_message is not None else ""
    s = orig.strip()
    if not s:
        return orig, False
    for p in openclaw_chat_prefix_patterns():
        pl = len(p)
        if pl == 0 or len(s) < pl:
            continue
        if s[:pl].lower() == p.lower():
            rest = s[pl:].strip()
            if not rest:
                return orig, False
            return rest, True
    return orig, False


def want_openclaw_first_this_turn(
    review_drafts_only: bool,
    direct_llm: bool,
    openclaw_from_message_prefix: bool,
) -> bool:
    if review_drafts_only:
        return False
    # UI setting is authoritative. Legacy env switches do not decide chat route.
    if openclaw_only_chat_enabled():
        return True
    if direct_llm:
        return False
    if openclaw_from_message_prefix:
        return True
    return False


def openclaw_fallback_model() -> str:
    """Fallback model used when direct LLM has no config."""
    try:
        p = _BASE_DIR / "openclaw" / "openclaw.json"
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            primary = ((data.get("agents") or {}).get("defaults") or {}).get("model") or {}
            if isinstance(primary, dict):
                pv = (primary.get("primary") or "").strip()
                if pv and "/" in pv:
                    return pv
    except Exception:
        pass
    return "anthropic/claude-sonnet-4-5"


def installation_id_from_request(request: Optional[Request]) -> Optional[str]:
    if request is None:
        return None
    xi = (request.headers.get("X-Installation-Id") or request.headers.get("x-installation-id") or "").strip()
    return xi or None


def _openclaw_body_looks_like_upstream_http_error(content: str) -> bool:
    t = (content or "").strip().lower()
    if not t:
        return False
    if "status code (no body)" in t:
        return True
    if re.match(r"^\d{3}\s+status code(\s|\(|$|,)", t):
        return True
    if "invalid_api_key" in t or "incorrect api key" in t:
        return True
    return False


def _openclaw_gateway_body_model(agent_id: str) -> str:
    aid = (agent_id or "").strip() or "main"
    if aid == "main":
        return "openclaw"
    return f"openclaw/{aid}"


def _openclaw_sutui_model_slug(mid: str) -> str:
    s = re.sub(r"[^\w.-]+", "-", (mid or "").strip(), flags=re.ASCII)
    s = re.sub(r"-+", "-", s).strip("-").lower()
    return s[:72] if s else "m"


def _openclaw_agent_id_from_chat_model(model: str) -> str:
    m = (model or "").strip()
    if not m or m.lower() == "openclaw":
        return "main"
    low = m.lower()
    if low.startswith("sutui/"):
        rest = m[6:].strip()
        if rest:
            return f"lobster-sutui-{_openclaw_sutui_model_slug(rest)}"
        return "main"
    if low.startswith("lobster-sutui/"):
        rest = m[14:].strip()
        if rest:
            return f"lobster-sutui-{_openclaw_sutui_model_slug(rest)}"
        return "main"
    if "/" in m:
        slug = re.sub(
            r"[^a-z0-9_-]",
            "-",
            m.lower().replace("/", "-").replace(".", "-"),
        )
        return re.sub(r"-+", "-", slug).strip("-")[:64] or "main"
    return "main"


_DSML_FC_RE = re.compile(
    r'<[\uff5c|]DSML[\uff5c|](?:function_calls|tool_calls)>(.*?)</[\uff5c|]DSML[\uff5c|](?:function_calls|tool_calls)>',
    re.DOTALL,
)
_PIPE_TOOL_CALLS_WRAPPER_RE = re.compile(
    r"<\s*\|\s*(?:tool_calls_begin|redacted_tool_calls_begin)\s*\|\s*>"
    r"(.*?)<\s*\|\s*(?:tool_calls_end|redacted_tool_calls_end)\s*\|\s*>",
    re.DOTALL | re.IGNORECASE,
)


def _strip_dsml(content: str) -> str:
    cleaned = _PIPE_TOOL_CALLS_WRAPPER_RE.sub("", content or "").strip()
    cleaned = _DSML_FC_RE.sub("", cleaned).strip()
    cleaned = re.sub(r'<[\uff5c|]DSML[\uff5c|][^>]*>', "", cleaned).strip()
    return cleaned


def _decode_token_user_id(raw_token: str, installation_id: str = "") -> Optional[int]:
    xi = (installation_id or "").strip()
    m = re.match(r"^lobster-internal-(\d+)$", xi, re.IGNORECASE)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None

    token = (raw_token or "").strip()
    parts = token.split(".")
    if len(parts) < 2:
        return None
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")).decode("utf-8"))
        sub = data.get("sub")
        if sub is None:
            return None
        return int(sub)
    except Exception:
        return None


def _last_user_text(msgs: List[Dict]) -> str:
    for m in reversed(msgs or []):
        if isinstance(m, dict) and m.get("role") == "user":
            return str(m.get("content") or "").strip()
    return ""


def _load_user_memory_index(user_id: int) -> List[Dict[str, Any]]:
    path = _USER_MEMORY_DIR / f"user_{user_id}" / "index.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        docs = data.get("documents") if isinstance(data, dict) else data
        if isinstance(docs, list):
            return [d for d in docs if isinstance(d, dict)]
    except Exception as exc:
        logger.warning("[OPENCLAW] read user memory index failed user_id=%s: %s", user_id, exc)
    return []


def _memory_doc_path(record: Dict[str, Any]) -> Optional[Path]:
    rel = str(record.get("canonical_path") or "").strip()
    if not rel:
        return None
    try:
        path = (_BASE_DIR / rel).resolve()
        if path.is_file() and _USER_MEMORY_DIR.resolve() in path.parents:
            return path
    except Exception:
        return None
    return None


def _read_memory_doc_text(record: Dict[str, Any], max_chars: int = 80_000) -> str:
    path = _memory_doc_path(record)
    if not path:
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    marker = "\n## Content\n\n"
    if marker in text:
        text = text.split(marker, 1)[1]
    text = re.sub(r"\x00", " ", text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text).strip()
    if len(text) > max_chars:
        return text[:max_chars].rstrip() + "\n..."
    return text


_MEMORY_POLICY_TERMS = (
    "用户倾向",
    "资料使用规则",
    "使用规则",
    "用户规则",
    "偏好",
    "人设",
    "画像",
    "规则",
    "policy",
    "preference",
)

_FAQ_TERMS = ("百问百答", "问答", "FAQ", "疑问", "顾虑", "话术")

_COMMON_MEMORY_TERMS = (
    "恒辉",
    "储能",
    "储能柜",
    "模型",
    "展示",
    "工厂",
    "源头",
    "价格",
    "成本",
    "售后",
    "交付",
    "周期",
    "免费",
    "打样",
    "方案",
    "质量",
    "精度",
    "3D",
    "CNC",
    "玻璃钢",
    "开门",
    "灯光",
    "沙盘",
    "设计",
    "视频",
    "口播",
    "脚本",
    "提示词",
)


def _doc_label(record: Dict[str, Any]) -> str:
    return str(record.get("title") or record.get("filename") or record.get("id") or "未命名资料").strip()


def _is_policy_memory_doc(record: Dict[str, Any]) -> bool:
    haystack = f"{_doc_label(record)} {record.get('filename') or ''} {record.get('notes') or ''}".lower()
    return any(term.lower() in haystack for term in _MEMORY_POLICY_TERMS)


def _is_faq_memory_doc(record: Dict[str, Any]) -> bool:
    haystack = f"{_doc_label(record)} {record.get('filename') or ''} {record.get('notes') or ''}".lower()
    return any(term.lower() in haystack for term in _FAQ_TERMS)


def _query_terms(query: str) -> List[str]:
    q = (query or "").strip()
    terms: set[str] = set()
    for term in _COMMON_MEMORY_TERMS:
        if term.lower() in q.lower():
            terms.add(term)
    for token in re.findall(r"[A-Za-z0-9_]{2,}", q):
        terms.add(token)
    for seq in re.findall(r"[\u4e00-\u9fff]{2,}", q):
        if len(seq) <= 8:
            terms.add(seq)
        # Add short Chinese n-grams so natural questions can match document copy.
        for n in (2, 3, 4):
            for i in range(0, max(0, len(seq) - n + 1)):
                gram = seq[i : i + n]
                if gram not in {"一下", "一个", "根据", "资料", "文档", "只写", "不要"}:
                    terms.add(gram)
    return sorted(terms, key=len, reverse=True)[:80]


def _doc_score(record: Dict[str, Any], text: str, query: str, terms: List[str]) -> int:
    label = f"{_doc_label(record)} {record.get('filename') or ''} {record.get('notes') or ''}"
    label_low = label.lower()
    text_low = text.lower()
    score = 0
    if _is_policy_memory_doc(record):
        score += 1000
    if _is_faq_memory_doc(record):
        score += 120
        if re.search(r"[?？]|为什么|怎么|如何|能不能|可不可以|吗|多久|多少|价格|售后|交付|质量", query or ""):
            score += 120
    for term in terms:
        tl = term.lower()
        if tl in label_low:
            score += 50
        if tl in text_low:
            score += min(30, text_low.count(tl)) * (4 if len(term) >= 3 else 2)
    if "恒辉" in (query or "") and ("恒辉" in label or "恒辉" in text[:5000]):
        score += 80
    return score


def _split_memory_chunks(text: str) -> List[str]:
    chunks = re.split(r"\n(?=\s*(?:#{1,4}\s+|\d+[\.、]|[一二三四五六七八九十]+[、.]))|\n{2,}", text or "")
    cleaned = [c.strip() for c in chunks if c and c.strip()]
    if cleaned:
        return cleaned
    return [text.strip()] if text.strip() else []


def _window_around_term(chunk: str, terms: List[str], width: int) -> str:
    if len(chunk) <= width:
        return chunk.strip()
    pos = -1
    for term in terms:
        pos = chunk.lower().find(term.lower())
        if pos >= 0:
            break
    if pos < 0:
        return chunk[:width].rstrip() + "..."
    start = max(0, pos - width // 3)
    end = min(len(chunk), start + width)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(chunk) else ""
    return prefix + chunk[start:end].strip() + suffix


def _best_memory_snippets(text: str, query: str, *, is_faq: bool, max_snippets: int = 3, width: int = 900) -> List[str]:
    terms = _query_terms(query)
    chunks = _split_memory_chunks(text)
    scored: List[Tuple[int, int, str]] = []
    for idx, chunk in enumerate(chunks):
        low = chunk.lower()
        score = 0
        for term in terms:
            if term.lower() in low:
                score += 8 if len(term) >= 3 else 3
        if is_faq and re.search(r"[?？]|为什么|怎么|如何|能不能|可不可以|吗|多久|多少|价格|售后|交付|质量", chunk):
            score += 10
        if "恒辉" in chunk:
            score += 3
        scored.append((score, idx, chunk))
    scored.sort(key=lambda x: (x[0], -x[1]), reverse=True)
    picked = [c for s, _idx, c in scored if s > 0][:max_snippets]
    if not picked:
        picked = chunks[:max_snippets]
    return [_window_around_term(c, terms, width) for c in picked if c.strip()]


def _build_openclaw_memory_context(msgs: List[Dict], raw_token: str, installation_id: str = "") -> str:
    user_id = _decode_token_user_id(raw_token, installation_id)
    if not user_id:
        return ""
    docs = _load_user_memory_index(user_id)
    if not docs:
        return ""

    query = _last_user_text(msgs)
    terms = _query_terms(query)
    doc_texts: Dict[str, str] = {}
    scored: List[Tuple[int, Dict[str, Any]]] = []
    for record in docs:
        doc_id = str(record.get("id") or "")
        text = _read_memory_doc_text(record)
        if not text:
            continue
        doc_texts[doc_id] = text
        scored.append((_doc_score(record, text, query, terms), record))
    if not scored:
        return ""

    selected: List[Dict[str, Any]] = []
    for _score, record in sorted(scored, key=lambda item: item[0], reverse=True):
        if _is_policy_memory_doc(record) and record not in selected:
            selected.append(record)
    for score, record in sorted(scored, key=lambda item: item[0], reverse=True):
        if score <= 0:
            continue
        if record not in selected:
            selected.append(record)
        if len(selected) >= 5:
            break

    if len(selected) <= 1 and ("恒辉" in query or "资料" in query or "文案" in query or "口播" in query):
        for _score, record in sorted(scored, key=lambda item: item[0], reverse=True):
            if record not in selected:
                selected.append(record)
            if len(selected) >= 4:
                break

    lines = [
        "【OpenClaw 本机记忆已检索】",
        "以下资料来自当前用户/设备已同步的 OpenClaw 记忆。系统已经完成本轮资料检索；请直接基于这些资料给用户最终答复，不要再输出“我先查一下/正在搜索/让我读取资料”等中间过程，也不要输出 DSML/tool_calls。",
        "如果用户问题命中“百问百答/问答/销售话术”类资料，请优先沿用文档中的原文文案和话术来回答；可以做少量整理，但不要改成泛泛总结。",
        "除非用户明确要求生成图片、生成视频或发布，否则本轮只输出文字方案，不调用生成或发布能力。",
        "",
    ]

    total = 0
    for record in selected:
        doc_id = str(record.get("id") or "")
        text = doc_texts.get(doc_id) or ""
        if not text:
            continue
        label = _doc_label(record)
        if _is_policy_memory_doc(record):
            kind = "使用规则/用户倾向"
            snippets = [_window_around_term(text, ["优先", "不要", "百问百答", "生成", "发布"], 2600)]
        elif _is_faq_memory_doc(record):
            kind = "百问百答/销售问答"
            snippets = _best_memory_snippets(text, query, is_faq=True, max_snippets=4, width=1100)
        else:
            kind = "资料"
            snippets = _best_memory_snippets(text, query, is_faq=False, max_snippets=3, width=950)
        block = [f"### {kind}: {label} (id={doc_id})"]
        for idx, snippet in enumerate(snippets, 1):
            block.append(f"[片段 {idx}]\n{snippet}")
        block_text = "\n\n".join(block).strip()
        if not block_text:
            continue
        total += len(block_text)
        if total > 13_000:
            break
        lines.append(block_text)
        lines.append("")

    return "\n".join(lines).strip()


def _inject_memory_context(messages: List[Dict], memory_context: str) -> List[Dict]:
    if not memory_context:
        return messages
    out = [dict(m) for m in messages]
    if out and out[0].get("role") == "system":
        out[0]["content"] = f"{out[0].get('content') or ''}\n\n{memory_context}".strip()
    else:
        out.insert(0, {"role": "system", "content": memory_context})
    return out


def _openclaw_reply_looks_incomplete(content: str) -> bool:
    raw = (content or "").strip()
    cleaned = _strip_dsml(raw).strip()
    if not raw:
        return True
    if _DSML_FC_RE.search(raw) or _PIPE_TOOL_CALLS_WRAPPER_RE.search(raw):
        if len(cleaned) < 180:
            return True
    if len(cleaned) < 120 and re.search(
        r"我先查|先查|正在查|搜索|检索|读取|让我.*资料|找到了.*让我|正在处理",
        cleaned,
    ):
        return True
    return False


_OPENCLAW_CHAT_SYSTEM_EXTRA = """\
OpenClaw 主对话补充规则：
- 每轮涉及资料问答、公司/产品介绍、文案/脚本/提示词创作、素材生成或发布前，先调用 memory_search 检索本机记忆中标题、文件名或内容包含“用户倾向”“资料使用规则”“使用规则”“用户规则”“偏好”“人设”“画像”的 MD/资料文档；如果找到，将这些规则作为最高优先级来理解和使用其它用户资料。规则内容由下发的 MD 决定，代码里不要固化具体行业或公司口径。
- 用户问“查资料 / 了解 / 介绍 / 继续细化 / 总结某个名称或公司/产品资料”时，必须先调用 memory_search 检索本机记忆和用户上传资料；只要 memory_search 有相关结果，本轮禁止再调用 web_search/web_fetch。只有 memory_search 没有相关结果，或用户明确要求联网/工商/网页搜索时，才使用 web_search。
- 如果 memory_search 返回用户上传文档，优先基于该文档回答；不要把同名网页公司误当成用户资料。
- 用户要求发布到某账号时，调用 list_publish_accounts 后必须扫描 accounts 全量列表，并同时核对 platform 与 nickname；“抖音账号123”匹配 platform="douyin" 且 nickname="123"，douyin_shop/抖店不是抖音。发布时传 account_nickname，不要把 id 当昵称。若历史回复曾说账号不存在，以最新工具结果为准。
- 绝对不要把 DSML、XML、tool_calls、function_calls 或工具调用参数作为正文输出给用户。需要工具时使用工具调用；不能调用时用自然语言说明。
"""


def _prepare_messages_for_openclaw(msgs: List[Dict], scope_hint: str = "") -> List[Dict]:
    prepared: List[Dict] = []
    for m in msgs or []:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        content = m.get("content")
        if isinstance(content, str):
            clean_content = content
            if role == "assistant":
                clean_content = _strip_dsml(clean_content).strip()
                if not clean_content:
                    continue
            copied = dict(m)
            copied["content"] = clean_content
            prepared.append(copied)
        else:
            prepared.append(dict(m))

    system_extra = _OPENCLAW_CHAT_SYSTEM_EXTRA
    if scope_hint:
        system_extra = f"{system_extra}\n{scope_hint}".strip()

    if prepared and prepared[0].get("role") == "system":
        base = str(prepared[0].get("content") or "").rstrip()
        prepared[0]["content"] = f"{base}\n\n{system_extra}".strip()
    else:
        prepared.insert(0, {"role": "system", "content": system_extra})

    if scope_hint:
        for idx in range(len(prepared) - 1, -1, -1):
            if prepared[idx].get("role") == "user":
                content = str(prepared[idx].get("content") or "").rstrip()
                prepared[idx]["content"] = (
                    f"{content}\n\n【本轮工具硬约束】\n{scope_hint}"
                ).strip()
                break
    return prepared


async def try_openclaw(
    msgs: List[Dict],
    model: str,
    raw_token: str,
    installation_id: Optional[str] = None,
) -> Optional[str]:
    """Attempt to get a reply via OpenClaw Gateway. Returns None on failure."""
    oc_base = (settings.openclaw_gateway_url or "").strip().rstrip("/")
    oc_token = (settings.openclaw_gateway_token or "").strip()
    if not oc_base or not oc_token:
        return None

    agent_id = _openclaw_agent_id_from_chat_model(model)
    openclaw_body_model = _openclaw_gateway_body_model(agent_id)
    scope = classify_openclaw_tool_scope(msgs)
    set_openclaw_tool_scope_for_agent(agent_id, scope.headers())
    logger.info(
        "[OPENCLAW] tool scope intent=%s tools=%s caps=%s",
        scope.intent,
        ",".join(sorted(scope.allowed_tools)) or "-",
        (
            "ALL"
            if scope.allowed_capabilities is None
            else (",".join(sorted(scope.allowed_capabilities)) or "-")
        ),
    )

    xi = (installation_id or "").strip()
    rt = (raw_token or "").strip()
    is_internal_lobster_jwt = bool(xi and xi.lower().startswith("lobster-internal-"))
    if rt and not is_internal_lobster_jwt:
        set_mcp_token_for_agent(agent_id, rt, installation_id=xi or None)

    headers: Dict[str, str] = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {oc_token}",
        "x-openclaw-agent-id": agent_id,
    }
    if rt and not is_internal_lobster_jwt:
        headers["x-user-authorization"] = f"Bearer {raw_token}"
    if xi and not is_internal_lobster_jwt:
        headers["X-Installation-Id"] = xi

    try:
        openclaw_messages = _prepare_messages_for_openclaw(msgs, scope.system_hint())
        memory_context = _build_openclaw_memory_context(msgs, raw_token, xi)
        if memory_context:
            openclaw_messages = _inject_memory_context(openclaw_messages, memory_context)

        async def _post_gateway(messages: List[Dict]) -> Tuple[Optional[str], Optional[httpx.Response]]:
            resp = await client.post(
                f"{oc_base}/v1/chat/completions",
                json={"model": openclaw_body_model, "messages": messages, "stream": False},
                headers=headers,
            )
            if resp.status_code != 200:
                return None, resp
            choices = resp.json().get("choices", [])
            if not choices:
                return None, resp
            return (choices[0].get("message", {}).get("content") or "").strip(), resp

        async with httpx.AsyncClient(timeout=120.0, trust_env=False) as client:
            raw_content, resp = await _post_gateway(openclaw_messages)
        if resp.status_code == 200:
            if raw_content:
                if _openclaw_body_looks_like_upstream_http_error(raw_content):
                    logger.warning(
                        "[OPENCLAW] Gateway 200 but body looks like upstream HTTP/auth error agent_id=%s snippet=%s",
                        agent_id,
                        (raw_content or "")[:300],
                    )
                    return None
                if memory_context and _openclaw_reply_looks_incomplete(raw_content):
                    followup_messages = list(openclaw_messages)
                    cleaned = _strip_dsml(raw_content).strip()
                    if cleaned:
                        followup_messages.append({"role": "assistant", "content": cleaned})
                    followup_messages.append(
                        {
                            "role": "user",
                            "content": (
                                "上面的 OpenClaw 本机记忆资料已经由系统检索并提供。"
                                "请不要再调用 memory_search/memory_get，也不要说正在查询。"
                                "请直接根据这些资料给出最终回答。"
                                "如果用户问题命中百问百答/销售问答资料，优先按文档里的原文文案和话术回答。"
                            ),
                        }
                    )
                    async with httpx.AsyncClient(timeout=120.0, trust_env=False) as client:
                        retry_content, retry_resp = await _post_gateway(followup_messages)
                    if retry_resp and retry_resp.status_code == 200 and retry_content:
                        if not _openclaw_body_looks_like_upstream_http_error(retry_content):
                            logger.info("[OPENCLAW] memory-context follow-up produced final reply agent_id=%s", agent_id)
                            return retry_content
                return raw_content
            logger.warning("[OPENCLAW] Gateway 200 but choices empty model=%s agent_id=%s", model, agent_id)
        else:
            body_prefix = (resp.text or "").replace("\n", " ").strip()
            if len(body_prefix) > 600:
                body_prefix = body_prefix[:600] + "…"
            logger.warning(
                "[OPENCLAW] Gateway HTTP %s model=%s agent_id=%s body_prefix=%s",
                resp.status_code,
                model,
                agent_id,
                body_prefix or "(empty body)",
            )
    except Exception as exc:
        logger.warning("[OPENCLAW] Gateway request failed: %s", exc)
    return None
