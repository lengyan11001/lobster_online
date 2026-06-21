from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Set


HEADER_INTENT = "X-Lobster-OpenClaw-Intent"
HEADER_ALLOWED_TOOLS = "X-Lobster-Allowed-MCP-Tools"
HEADER_ALLOWED_CAPABILITIES = "X-Lobster-Allowed-Capabilities"
HEADER_DENIED_CAPABILITIES = "X-Lobster-Denied-Capabilities"
HEADER_VIDEO_MODEL_LOCK = "X-Lobster-Video-Model-Lock"
HEADER_VIDEO_MODEL_LOCK_SOURCE = "X-Lobster-Video-Model-Lock-Source"


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
CAP_VIDEO = frozenset({"video.generate", "task.get_result", "sutui.search_models", "sutui.guide", "sutui.transfer_url", "goal.video.pipeline"})
CAP_TVC = frozenset({"comfly.daihuo.pipeline", "comfly.daihuo", "task.get_result"})
CAP_TASK_RESULT = frozenset({"task.get_result"})
CAP_MEDIA_EDIT = frozenset({"media.edit"})
CAP_ECOMMERCE = frozenset({"comfly.ecommerce.detail_pipeline", "ecommerce.publish"})
CAP_PPT = frozenset({"ppt.create"})
CAP_WEWRITE = frozenset({"wewrite.article.pipeline", "wewrite.article.generate", "wewrite.article.draft"})
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
GOAL_VIDEO_RE = _rx(r"(创意成片|目标成片|根据.{0,8}记忆.{0,20}(生成|做|制作|创建).{0,12}(视频|短片|短视频|宣传片|成片)|用.{0,6}记忆.{0,20}(视频|短片|短视频|宣传片|成片))")
VIDEO_COPY_RE = _rx(r"(文案|脚本|口播|分镜建议|宣传语|标题|正文|话术)")
VIDEO_PLAN_TEXT_RE = _rx(
    r"(生成|写|出|给|给出|设计|制定|策划|整理|帮我).{0,24}"
    r"(短视频|视频|宣传片|tvc)?.{0,24}"
    r"(方案|创意方案|拍摄方案|镜头脚本|分镜脚本|分镜|镜头表|拍摄脚本|口播文案|脚本|文案|storyboard|shot\s*list)|"
    r"(短视频|视频|宣传片|tvc).{0,16}"
    r"(方案|创意方案|拍摄方案|镜头脚本|分镜脚本|分镜|镜头表|拍摄脚本|口播文案|脚本|文案)"
)
VIDEO_PLAN_EXEC_RE = _rx(
    r"(按|根据|用|把).{0,32}(这个|上面|刚才|方案|脚本|分镜|镜头脚本|提示词).{0,24}"
    r"(生成|制作|做成|渲染|输出|出).{0,16}(视频|成片|片子|mp4|视频文件)|"
    r"(开始|现在|直接|马上|立刻).{0,16}(生成|制作|做成|渲染|输出|出).{0,16}(视频|成片|片子|mp4|视频文件)|"
    r"(生成|制作|做成|渲染|输出).{0,16}(成片|mp4|视频文件)|"
    r"(调用|执行).{0,24}(video\.generate|goal\.video\.pipeline|生成能力|视频生成)"
)
TVC_RE = _rx(r"(爆款\s*TVC|tvc|带货视频|代货视频|comfly)")
EDIT_RE = _rx(r"(加字|叠字|裁剪|剪辑|改比例|静音|换音乐|配音轨|抽帧|转视频|素材编辑|去水印|字幕)")
ACCOUNT_RE = _rx(r"(发布中心|发布账号|账号列表|抖音账号|小红书账号|头条账号|抖店账号|检查账号|打开账号|登录状态|账号.*登录|有没有.*账号)")
MEMORY_RE = _rx(r"(查.{0,8}资料|了解|介绍|继续细化|总结|文档|上传.*文件|记忆|资料库|pdf|word|excel|docx|xlsx)")
SKILL_RE = _rx(r"(技能商店|技能库|安装技能|卸载技能|管理技能|有哪些能力|有什么能力|有什么功能|能做什么|可以做什么|能帮我做什么|你能帮我做什么|你能帮我干什么|你可以帮我做什么|会干什么|能力列表|list_capabilities)")
ECOMMERCE_RE = _rx(r"(电商|详情页|商品发布|发布商品|上架|抖店|淘宝|1688|拼多多|千帆|商品主图|长图)")
PPT_RE = _rx(r"(ppt|powerpoint|幻灯片|演示文稿|课件|汇报文档|做个演示|生成演示|制作演示)")
WEWRITE_RE = _rx(r"(公众号|微信\s*公众号|微信文章|微信推文|推文|草稿箱|wewrite).{0,80}(文章|写|生成|排版|配图|草稿|推送|主题)|帮我写个?公众号文章|公众号文章")
CREATOR_DATA_RE = _rx(r"(播放量|点赞|评论|互动|作品数据|发布数据|创作者数据|同步.*数据|数据报告|账号表现)")
YOUTUBE_RE = _rx(r"(youtube|油管)")
META_RE = _rx(r"(instagram|facebook|meta)")
ASSET_RE = _rx(r"(素材库|素材列表|查素材|找素材|asset_id|素材id)")
TASK_ID_RE = _rx(
    r"(任务\s*ID|task[_ -]?id|"
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b|"
    r"\b\d{12,}\b)"
)
TASK_STATUS_RE = _rx(
    r"(任务|task|生成|视频|图片).{0,24}(进度|状态|结果|完成|好了|成功|失败|查询|查|轮询|出来)"
    r"|(进度|状态|结果|完成|好了|成功|失败|查询|查|轮询|出来).{0,24}(任务|task|生成|视频|图片)"
)
TASK_LOOKUP_RE = _rx(r"(查|查询|查看|看看|看下|刷新|轮询|进度|状态|结果|好了没|出来没|完成了吗|完成没|成功了吗|失败了吗|怎么样了)")
TASK_LOOKUP_OBJECT_RE = _rx(r"(任务|task|生成|视频|图片|进度|状态|结果)")
TASK_STATUS_FOLLOWUP_RE = _rx(
    r"^(继续|再查|查一下|查询|查结果|看看|看下|刷新|轮询|好了没|出来没|完成了吗|怎么样了|"
    r"结果(?:是什么|呢|咋样|怎么样)?|视频结果(?:是什么|呢|咋样|怎么样)?|任务结果(?:是什么|呢|咋样|怎么样)?|"
    r"图片结果(?:是什么|呢|咋样|怎么样)?|一分钟查一次).{0,30}$"
)
GENERAL_ACTION_RE = _rx(r"(帮我|请帮|生成|写|制作|创建|新建|排版|配图|推送|发布|上传|自动生成|调用|执行|做一个|做个)")
CONTINUATION_RE = _rx(r"^(再来|再做|继续|继续优化|优化|换一版|重做|重新来|直接生成|开始生成|现在生成|发布|发出去|就这个|用这个|确认).{0,20}$")
RECENT_GENERATION_CONTEXT_RE = _rx(
    r"(video\.generate|image\.generate|task[_ -]?id|任务\s*ID|已提交|生成中|正在生成|"
    r"生成.{0,12}(视频|图片)|(?:视频|图片).{0,12}生成)"
)


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
    current = _last_text(messages)
    recent = _recent_text(messages)
    text = current or recent
    ctx = recent if CONTINUATION_RE.search(current or "") else text

    wants_publish = bool(PUBLISH_RE.search(text)) and not bool(NO_PUBLISH_RE.search(text))
    wants_video_plan_text = bool(VIDEO_PLAN_TEXT_RE.search(text)) and not bool(VIDEO_PLAN_EXEC_RE.search(text))
    wants_goal_video = bool(GOAL_VIDEO_RE.search(ctx))
    wants_image = bool(IMAGE_RE.search(ctx))
    wants_video = wants_goal_video or bool(VIDEO_RE.search(ctx))
    if wants_video_plan_text:
        wants_goal_video = False
        wants_video = False
    if (
        wants_video
        and VIDEO_COPY_RE.search(text)
        and not VIDEO_PLAN_EXEC_RE.search(text)
        and not CONTINUATION_RE.search(current or "")
    ):
        wants_video = False
    wants_tvc = bool(TVC_RE.search(ctx))

    explicit_task_lookup = (
        bool(TASK_ID_RE.search(text))
        or (
            bool(TASK_LOOKUP_RE.search(current or ""))
            and bool(TASK_LOOKUP_OBJECT_RE.search(text))
            and not bool(GENERAL_ACTION_RE.search(current or ""))
        )
    )
    if explicit_task_lookup or (
        TASK_STATUS_FOLLOWUP_RE.search(current or "")
        and (TASK_ID_RE.search(recent) or RECENT_GENERATION_CONTEXT_RE.search(recent))
    ):
        return _scope(
            "task_status_lookup",
            BASE_READ_TOOLS | {"invoke_capability", "save_asset"},
            CAP_TASK_RESULT,
            "只允许查询上下文或用户提供任务 ID 的生成结果；不要重新提交生成任务，也不要编造费用、素材 ID 或完成状态。没有任务 ID 时先说明需要任务 ID。",
        )
    if WEWRITE_RE.search(text):
        return _scope(
            "wewrite_article_pipeline",
            BASE_READ_TOOLS | {"invoke_capability", "save_asset"},
            CAP_WEWRITE,
            "用户要写公众号文章、微信推文、自动配图、公众号排版或推送草稿箱时，必须调用 wewrite.article.pipeline；不要把“自动生成/完成排版”误判为查询任务结果。",
        )
    if SKILL_RE.search(text):
        return _scope(
            "skill_or_capability_query",
            SKILL_TOOLS,
            None,
            "只能查看或管理技能；不要调用生成、发布、电商或素材编辑能力。",
        )
    if PPT_RE.search(text):
        return _scope(
            "ppt_create",
            BASE_READ_TOOLS | {"invoke_capability"},
            CAP_PPT,
            "用户要做PPT、幻灯片、演示文稿或课件时，优先调用 ppt.create。若只给主题或说AI模式，用 mode=ai、topic 和 slide_count（会先生成每页AI合成图再铺进PPT）；只有用户明确给了文字大纲时才用 mode=outline 和 outline_markdown。",
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
    if wants_goal_video:
        tools = BASE_READ_TOOLS | {"invoke_capability", "save_asset"}
        if wants_publish:
            tools = tools | PUBLISH_TOOLS
        return _scope(
            "goal_video_pipeline_publish" if wants_publish else "goal_video_pipeline",
            tools,
            {"goal.video.pipeline", "task.get_result"},
            "用户明确要“创意成片”“目标成片”或“根据记忆生成视频”时，优先直接调用 goal.video.pipeline，payload.action=start_pipeline，payload.goal 必填并直接使用用户原话或整理后的明确目标，不要反问视频主题。不要因为素材库没有产品图而停止；没有参考素材时 reference_asset_ids/reference_image_urls 留空，由流水线先根据记忆生成图片再生成视频。若返回 status=running/openclaw_async/next_payload，只能说明任务仍在运行并按 next_payload 继续 poll_pipeline；禁止说创意成片不可用，禁止改用普通 image.generate/video.generate 作为替代。",
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
    if GENERAL_ACTION_RE.search(text):
        return _scope(
            "general_action",
            BASE_READ_TOOLS | {"invoke_capability", "save_asset"} | PUBLISH_TOOLS,
            None,
            "用户在要求执行新任务但未命中特定意图时，先用 list_capabilities 选择合适能力，再调用对应 capability；不要默认改成 task.get_result，除非用户明确是在查已有任务进度或结果。",
        )

    return _scope(
        "general_safe",
        BASE_READ_TOOLS,
        frozenset(),
        "默认安全范围只允许只读查询；如需生成、发布、编辑或配置，先让用户明确任务。",
    )
