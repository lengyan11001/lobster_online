from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Set


HEADER_INTENT = "X-Lobster-OpenClaw-Intent"
HEADER_ALLOWED_TOOLS = "X-Lobster-Allowed-MCP-Tools"
HEADER_ALLOWED_CAPABILITIES = "X-Lobster-Allowed-Capabilities"


BASE_READ_TOOLS = frozenset({"list_capabilities", "list_assets"})
ACCOUNT_TOOLS = frozenset({"list_publish_accounts", "open_account_browser", "check_account_login"})
PUBLISH_TOOLS = frozenset({"list_publish_accounts", "list_assets", "publish_content", "open_account_browser", "check_account_login"})
YOUTUBE_TOOLS = frozenset({"list_assets", "list_youtube_accounts", "publish_youtube_video", "get_youtube_analytics", "sync_youtube_analytics"})
META_TOOLS = frozenset({"list_meta_social_accounts", "publish_meta_social", "get_meta_social_insights", "sync_meta_social_insights", "get_social_report"})
CREATOR_DATA_TOOLS = frozenset({"list_publish_accounts", "get_creator_publish_data", "sync_creator_publish_data"})
SKILL_TOOLS = frozenset({"list_capabilities", "manage_skills"})
AUTONOMOUS_OPENCLAW_TOOLS = frozenset(
    {
        "list_capabilities",
        "invoke_capability",
        "manage_skills",
        "save_asset",
        "list_assets",
        "list_publish_accounts",
        "publish_content",
        "open_account_browser",
        "check_account_login",
        "get_creator_publish_data",
        "sync_creator_publish_data",
        "list_youtube_accounts",
        "publish_youtube_video",
        "get_youtube_analytics",
        "sync_youtube_analytics",
        "list_meta_social_accounts",
        "publish_meta_social",
        "get_meta_social_data",
        "sync_meta_social_data",
        "get_social_report",
    }
)


CAP_IMAGE = frozenset({"image.generate", "task.get_result", "sutui.search_models", "sutui.guide"})
CAP_VIDEO = frozenset({"video.generate", "task.get_result", "sutui.search_models", "sutui.guide", "sutui.transfer_url"})
CAP_TVC = frozenset({"comfly.daihuo.pipeline", "comfly.daihuo", "task.get_result"})
CAP_MEDIA_EDIT = frozenset({"media.edit"})
CAP_ECOMMERCE = frozenset({"comfly.ecommerce.detail_pipeline", "ecommerce.publish"})
CAP_READ_MEDIA = frozenset({"image.understand", "video.understand", "sutui.parse_video"})


@dataclass(frozen=True)
class OpenClawToolScope:
    intent: str
    allowed_tools: frozenset[str]
    allowed_capabilities: Optional[frozenset[str]] = frozenset()
    instruction: str = ""

    def headers(self) -> Dict[str, str]:
        headers = {
            HEADER_INTENT: self.intent,
            HEADER_ALLOWED_TOOLS: ",".join(sorted(self.allowed_tools)),
        }
        if self.allowed_capabilities is not None:
            headers[HEADER_ALLOWED_CAPABILITIES] = ",".join(sorted(self.allowed_capabilities))
        return headers

    def system_hint(self) -> str:
        caps = (
            "全部已安装能力（以 list_capabilities 返回为准）"
            if self.allowed_capabilities is None
            else (", ".join(sorted(self.allowed_capabilities)) or "无")
        )
        return (
            "OpenClaw 本轮 MCP 工具范围：\n"
            f"- intent: {self.intent}\n"
            f"- allowed_tools: {', '.join(sorted(self.allowed_tools)) or '无'}\n"
            f"- allowed_capabilities: {caps}\n"
            "- 如果需要的工具或能力不在上述范围内，必须先向用户说明缺少哪些信息或能力，不要调用相似工具替代。\n"
            + (f"- {self.instruction}\n" if self.instruction else "")
        )


def _rx(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


PUBLISH_RE = _rx(r"(发布|发到|发去|发上|上传到|投放到|发抖音|发小红书|发头条|发视频号|发youtube|发到youtube|post|publish)")
NO_PUBLISH_RE = _rx(r"((不要|不用|别|无需|不).{0,6}(发布|发到|发出去|上传)|只.{0,8}(检查|查询|确认|看看).{0,12}(账号|发布中心))")
IMAGE_RE = _rx(r"(生成|画|出|做|来|创建|制作).{0,18}(图|图片|画|海报|插图|壁纸|头像|小猫|小狗|产品图)|文生图|图生图|生图|作图|gpt-image|flux|jimeng|seedream")
VIDEO_RE = _rx(r"(生成|做|制作|创建).{0,18}(视频|短片|短视频|片子|动画)|文生视频|图生视频|sora|seedance|wan|super-seed|video")
TVC_RE = _rx(r"(爆款\s*TVC|tvc|带货视频|代货视频|分镜|veo|comfly)")
EDIT_RE = _rx(r"(加字|叠字|裁剪|剪辑|改比例|静音|换音乐|配音轨|抽帧|转视频|素材编辑|去水印|字幕)")
ACCOUNT_RE = _rx(r"(发布中心|发布账号|账号列表|抖音账号|小红书账号|头条账号|抖店账号|检查账号|打开账号|登录状态|账号.*登录|有没有.*账号)")
MEMORY_RE = _rx(r"(查.{0,8}资料|了解|介绍|继续细化|总结|文档|上传.*文件|记忆|资料库|pdf|word|excel|docx|xlsx)")
SKILL_RE = _rx(r"(技能商店|技能库|安装技能|卸载技能|管理技能|有哪些能力|能做什么|会干什么|能力列表|list_capabilities)")
ECOMMERCE_RE = _rx(r"(电商|详情页|商品发布|发布商品|上架|抖店|淘宝|1688|拼多多|千帆|商品主图|长图)")
CREATOR_DATA_RE = _rx(r"(播放量|点赞|评论|互动|作品数据|发布数据|创作者数据|同步.*数据|数据报告|账号表现)")
YOUTUBE_RE = _rx(r"(youtube|油管)")
META_RE = _rx(r"(instagram|facebook|meta)")
ASSET_RE = _rx(r"(素材库|素材列表|查素材|找素材|asset_id|素材id)")
CONTINUATION_RE = _rx(r"^(再来|再做|继续|继续优化|优化|换一版|重做|重新来|发布|发出去|就这个|用这个|确认).{0,20}$")


def _strip_lobster_injected_blocks(text: str) -> str:
    """Classify only the user's request, not backend attachment/tool hints."""
    return re.split(r"\n*【用户本条消息上传的素材】", str(text or ""), maxsplit=1)[0].strip()


def _last_text(messages: Sequence[Dict]) -> str:
    for msg in reversed(messages or []):
        if not isinstance(msg, dict):
            continue
        if msg.get("role") == "user":
            return _strip_lobster_injected_blocks(str(msg.get("content") or ""))
    return ""


def _recent_text(messages: Sequence[Dict], limit: int = 6) -> str:
    parts: List[str] = []
    for msg in list(messages or [])[-limit:]:
        if isinstance(msg, dict):
            txt = _strip_lobster_injected_blocks(str(msg.get("content") or ""))
            if txt:
                parts.append(txt[:1200])
    return "\n".join(parts)


def _scope(
    intent: str,
    tools: Iterable[str],
    caps: Optional[Iterable[str]] = (),
    instruction: str = "",
) -> OpenClawToolScope:
    return OpenClawToolScope(
        intent=intent,
        allowed_tools=frozenset(tools),
        allowed_capabilities=None if caps is None else frozenset(caps),
        instruction=instruction,
    )


def classify_openclaw_tool_scope(messages: Sequence[Dict]) -> OpenClawToolScope:
    # OpenClaw 主链路不再由本地正则预判“图片/视频/发布/TVC”并裁剪能力。
    # 这些正则容易把“继续细化”等自然语言续接到上一轮生成流程里，导致误触发。
    # 现在仅把 Lobster 能力暴露给 OpenClaw，由模型结合记忆、用户原话和
    # list_capabilities 的真实结果自主判断该做什么。
    return _scope(
        "ai_autonomous",
        AUTONOMOUS_OPENCLAW_TOOLS,
        None,
        "",
    )

    current = _last_text(messages)
    recent = _recent_text(messages)
    text = current or recent
    ctx = recent if CONTINUATION_RE.search(current or "") else text

    wants_publish = bool(PUBLISH_RE.search(text)) and not bool(NO_PUBLISH_RE.search(text))
    wants_image = bool(IMAGE_RE.search(ctx))
    wants_video = bool(VIDEO_RE.search(ctx))
    wants_tvc = bool(TVC_RE.search(ctx))

    if SKILL_RE.search(text):
        return _scope(
            "skill_or_capability_query",
            SKILL_TOOLS,
            None,
            "只能查看或管理技能；不要调用生成、发布、电商或素材编辑能力。",
        )
    if ECOMMERCE_RE.search(text):
        tools = BASE_READ_TOOLS | {"invoke_capability", "list_assets"}
        if wants_publish:
            tools = tools | PUBLISH_TOOLS
        return _scope(
            "ecommerce",
            tools,
            CAP_ECOMMERCE | (CAP_IMAGE if wants_image else frozenset()),
            "电商商品/详情页只允许调用电商相关能力；不要误用普通发布或普通视频生成替代。",
        )
    if YOUTUBE_RE.search(text):
        return _scope("youtube", YOUTUBE_TOOLS, frozenset(), "YouTube 只能使用 YouTube 专用工具。")
    if META_RE.search(text):
        return _scope("meta_social", META_TOOLS, frozenset(), "Meta/Instagram/Facebook 只能使用 Meta 专用工具。")
    if CREATOR_DATA_RE.search(text):
        return _scope("creator_data", CREATOR_DATA_TOOLS, frozenset(), "只读取或同步创作者数据，不要生成或发布内容。")
    if ACCOUNT_RE.search(text) and not wants_publish:
        return _scope(
            "account_lookup",
            ACCOUNT_TOOLS,
            frozenset(),
            "只检查或打开账号；不要生成素材，也不要发布。",
        )
    if EDIT_RE.search(text):
        return _scope(
            "media_edit",
            BASE_READ_TOOLS | {"invoke_capability", "save_asset"},
            CAP_MEDIA_EDIT,
            "素材编辑只能调用 media.edit；禁止用生图替代叠字、裁剪或改比例。",
        )
    if wants_tvc:
        tools = BASE_READ_TOOLS | {"invoke_capability", "save_asset"}
        caps: Set[str] = set(CAP_TVC)
        if wants_publish:
            tools = tools | PUBLISH_TOOLS
        return _scope(
            "tvc_pipeline_publish" if wants_publish else "tvc_pipeline",
            tools,
            caps,
            "爆款TVC/带货视频优先用 comfly.daihuo.pipeline；不要误用普通 video.generate。",
        )
    if wants_publish and wants_video:
        return _scope(
            "video_generate_publish",
            BASE_READ_TOOLS | {"invoke_capability", "save_asset"} | PUBLISH_TOOLS,
            CAP_VIDEO,
            "先生成本轮视频并取得 saved_assets，再发布到匹配账号。",
        )
    if wants_publish and wants_image:
        return _scope(
            "image_generate_publish",
            BASE_READ_TOOLS | {"invoke_capability", "save_asset"} | PUBLISH_TOOLS,
            CAP_IMAGE,
            "先生成本轮图片并取得 saved_assets，再发布到匹配账号。",
        )
    if wants_publish:
        return _scope(
            "publish_existing_asset",
            PUBLISH_TOOLS,
            frozenset(),
            "只发布已有素材或上下文中已生成的素材；不要重新生成内容。",
        )
    if wants_video:
        return _scope(
            "video_generate",
            BASE_READ_TOOLS | {"invoke_capability", "save_asset"},
            CAP_VIDEO,
            "只允许生视频与查询本次任务结果；不要发布。",
        )
    if wants_image:
        return _scope(
            "image_generate",
            BASE_READ_TOOLS | {"invoke_capability", "save_asset"},
            CAP_IMAGE,
            "只允许生图与查询本次任务结果；不要发布。",
        )
    if ASSET_RE.search(text):
        return _scope("asset_lookup", {"list_assets", "save_asset"}, frozenset(), "只查询或保存素材，不要生成或发布。")
    if MEMORY_RE.search(text):
        return _scope(
            "memory_research",
            frozenset(),
            frozenset(),
            "必须优先使用 OpenClaw 内置 memory_search。只要 memory_search 有相关结果，本轮禁止再调用 web_search 或 web_fetch；除非用户明确说要联网、工商、网页搜索。",
        )

    return _scope(
        "general_safe",
        BASE_READ_TOOLS,
        frozenset(),
        "默认安全范围只允许只读查询；如需生成、发布、编辑或配置，先让用户明确任务。",
    )
