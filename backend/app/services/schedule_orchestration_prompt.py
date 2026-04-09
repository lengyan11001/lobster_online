"""
定时任务 → 智能编排：组装发给对话模型（+ MCP 工具）的用户消息正文。

说明：
- 「拆解步骤、组合能力」依赖龙虾 /chat 同款的 **直连 LLM + MCP**，不是 OpenClaw Gateway
  的纯 /v1/chat/completions 回退（后者无工具列表）。
- 调度器侧应使用与用户等效的 JWT（如 create_access_token(sub=user_id)）调用内部封装的
  chat 执行函数，并将下方 build_schedule_orchestration_user_message 的返回值作为 user message。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from ..models import PublishAccount, PublishAccountCreatorSchedule


def schedule_requirements_imply_publish(requirements_text: str) -> bool:
    """
    定时说明里是否包含「要发到平台」类意图。
    显式否定发布（仅生成、不要发）时返回 False。
    """
    t = (requirements_text or "").strip()
    if not t:
        return False
    neg = (
        "不要发布", "不发布", "无需发布", "不用发布", "别发布",
        "仅生成", "只生成", "不用发", "不要发", "不发到", "不发表",
    )
    if any(n in t for n in neg):
        return False
    pos = (
        "发布", "發布", "发到", "发帖", "发文", "微头条", "刊登", "推送",
        "publish",
    )
    return any(p in t for p in pos)


def build_schedule_orchestration_user_message(
    *,
    account: "PublishAccount",
    schedule: "PublishAccountCreatorSchedule",
    extra_context: Optional[Dict[str, Any]] = None,
) -> str:
    """
    将定时任务配置整理成一条「可交给模型拆解并调工具」的用户消息。

    建议在 system 或首条 user 中固定追加编排规则（见 docs/定时任务与智能编排.md），
    本条消息只承载任务实例数据与用户自然语言/半结构化说明。
    """
    platform = getattr(account, "platform", "") or ""
    nickname = getattr(account, "nickname", "") or ""
    aid = getattr(account, "id", None)
    kind = (getattr(schedule, "schedule_kind", None) or "image").strip().lower()
    if kind not in ("image", "video"):
        kind = "image"
    req = (getattr(schedule, "requirements_text", None) or "").strip()
    v_asset = (getattr(schedule, "video_source_asset_id", None) or "").strip()
    iv = int(getattr(schedule, "interval_minutes", None) or 60)

    lines = [
        "【定时任务 · 自动编排执行】",
        f"- 发布账号：{platform} · {nickname}（account_id={aid}）",
        f"- 内容类型：{'视频' if kind == 'video' else '图文'}",
        f"- 定时间隔：每 {iv} 分钟（本条为本次到点触发的执行说明）",
    ]
    if kind == "video" and v_asset:
        lines.append(f"- 参考素材 asset_id（图生视频首帧/参考）：{v_asset}")
    elif kind == "video":
        lines.append("- 参考素材：无（请走文生视频，除非说明中另有指定）")

    lines.append("")
    lines.append("【用户填报的说明（可能含模型、画面方向、生成素材要点、发布文案、是否发布等；图生视频的参考图 asset_id 已单独列出时请优先使用）】")
    lines.append(req if req else "（未填写，请根据账号与类型给出最小可行产出或明确说明无法执行）")

    if extra_context:
        lines.append("")
        lines.append("【系统附加上下文】")
        for k, v in extra_context.items():
            lines.append(f"- {k}: {v}")

    lines.append("")
    lines.append(
        "【请你执行】请在本轮起按依赖顺序调用 MCP 能力完成目标："
        "先理解说明中的模型、【生成素材】与【发布文案】分工；参考图以本消息已列出的 asset_id 为准，勿与用户正文中的垫图描述重复推断。"
        "需要现成垫图则 image.generate，再按需 video.generate（或仅图文则 image.generate 等）；"
        "若说明要求发布到上述账号，在生成成功并取得 asset_id 后调用 publish_content，"
        "并使用【发布文案】中的标题/描述/标签意图；**必须**使用上面给出的 account_id 作为 publish_content 的 account_id（勿仅用昵称，以免多平台串号）。"
        "小红书笔记必须有正文：publish_content.description 不可为空（勿只填 title）；若说明里只有一句标题、无单独正文，"
        "须把该句同时作为 title 与 description 传入（或 description 写同等内容），否则定时发布后笔记正文区会空。"
        "话题可放 tags 或写在 description。"
        "小红书标题最多 20 字；抖音视频标题约 30 字、图文约 20 字，描述与话题总长约 500 字；勿超长。"
        "生成素材阶段不必打开本地浏览器；仅在 publish_content 时本机会唤起该账号有头浏览器完成上传/登录。"
        "不要只回复计划而不调用工具。"
        "**禁止**在素材已就绪时用自然语言写「接下来检查账号/写文案/再发布」收尾而不调用 publish_content；"
        "必须调用 publish_content 且返回成功才算本任务完成。"
    )
    return "\n".join(lines)
