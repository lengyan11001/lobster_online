"""询盘接待的判定与背调结论化（纯函数，便于测试与复用）。

三件事：
1. is_human_like_message()  —— 按买家回复内容判断"是不是真人"（真人判定 A 方案）
2. assess_info_sufficiency() —— 手上这些字段够不够做背调，不够就说明还要问什么
3. backtest_verdict()        —— 把证据/字段收敛成"结论 + 置信度 + 缺口"，不再只堆来源

来源用注册表描述，方便以后接各国企查查、海关数据等（新增一项即可）。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

# ------------------------------------------------------------------ ① 真人判定

_REAL_QUESTION_HINTS = (
    "?", "？", "how much", "how many", "price", "moq", "lead time", "delivery", "sample",
    "do you", "can you", "could you", "please", "need", "want", "looking for", "quote",
    "spec", "specification", "catalog", "catalogue", "ship to", "shipment", "payment",
    "价格", "多少", "能不能", "可以", "请", "需要", "采购", "样品", "交期", "报价", "型号", "数量",
)
_SPECIFIC_SIGNALS = (
    ("model", re.compile(r"\b[A-Z]{1,4}[0-9]{2,}[A-Z0-9\-]*\b")),                 # R36U / T70EX
    ("quantity", re.compile(r"\b\d{2,}\s*(pcs|pieces|units|sets|k)\b", re.I)),
    ("company", re.compile(r"\b(ltd|llc|inc|co\.|corp|gmbh|s\.a\.|pvt|limited|trading|import|export)\b", re.I)),
    ("email", re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")),
    ("url", re.compile(r"https?://|www\.", re.I)),
    ("phone", re.compile(r"(?<!\d)(?:\+?\d[\d\s\-()]{6,}\d)(?!\d)")),
    ("whatsapp", re.compile(r"whats\s*app|wa\.me", re.I)),
)
_BOT_PATTERNS = (
    ("mass_template", re.compile(
        r"(dear (sir|valued customer|supplier)|to whom it may concern|we are interested in your products,? please send)",
        re.I)),
    ("catalog_only", re.compile(r"^\s*(please\s+)?(send|share)\s+(me\s+)?(your\s+)?(product\s+)?(catalog|catalogue|price\s*list)\s*[.!]?\s*$", re.I)),
    ("link_only", re.compile(r"^\s*(https?://\S+\s*)+$", re.I)),
    ("emoji_only", re.compile(r"^[\W_]+$")),
)


def is_human_like_message(text: str, *, previous_texts: Optional[List[str]] = None) -> Dict[str, Any]:
    """按内容判断买家这条回复像不像真人发的（A 方案：真人 + 真实提问才自动回）。"""
    body = re.sub(r"\s+", " ", str(text or "")).strip()
    reasons: List[str] = []
    signals: List[str] = []
    if not body:
        return {"human": False, "score": 0, "signals": [], "reasons": ["空消息"]}
    if len(body) <= 2:
        return {"human": False, "score": 5, "signals": [], "reasons": ["内容过短（≤2 字符）"]}

    for label, pattern in _BOT_PATTERNS:
        if pattern.search(body):
            reasons.append({"mass_template": "群发模板话术", "catalog_only": "只要目录/报价单（无其他信息）",
                            "link_only": "只发了链接", "emoji_only": "只有符号"}.get(label, label))

    score = 0
    if any(hint in body.lower() for hint in _REAL_QUESTION_HINTS):
        score += 35
        signals.append("含提问/需求词")
    for label, pattern in _SPECIFIC_SIGNALS:
        if pattern.search(body):
            score += 15
            signals.append(label)
    if len(body) >= 25:
        score += 10
    if len(body) >= 80:
        score += 10
    if previous_texts:
        normalized = body.lower()
        if any(normalized == str(prev or "").strip().lower() for prev in previous_texts):
            reasons.append("与之前的消息完全相同（重复发送）")
            score -= 20

    return {
        "human": score >= 35 and not reasons,
        "score": max(0, min(100, score)),
        "signals": signals,
        "reasons": reasons,
    }


# ------------------------------------------------------------------ ② 信息是否够做背调

# 字段 -> 能做多硬的锚定（这是"单有公司名或邮箱够不够"的答案）
FIELD_CAPABILITY: Dict[str, Dict[str, str]] = {
    "email": {"level": "medium", "anchor": "域名", "can": "反查邮箱域名 → 官网/企业注册线索"},
    "domain": {"level": "strong", "anchor": "域名", "can": "直接锚定官网 → 主营/规模/地区/是否真实经营"},
    "company_name": {"level": "medium", "anchor": "企业名", "can": "企业注册/LinkedIn/官网搜索（同名会混淆，需第二字段消歧）"},
    "website": {"level": "strong", "anchor": "官网", "can": "同 domain"},
    "phone": {"level": "weak", "anchor": "电话", "can": "只能做号码归属/是否绑定社媒，几乎不产生企业结论"},
    "whatsapp": {"level": "weak", "anchor": "WhatsApp", "can": "同上"},
    "country": {"level": "context", "anchor": "国别", "can": "只用于过滤与合规判断，不构成企业结论"},
    "buyer_login_id": {"level": "weak", "anchor": "平台账号", "can": "仅平台内身份，不对外"},
}

INFO_LEVELS = {
    "L0": "只有名字/国别：不能下结论（同名混淆）",
    "L1": "单锚点（只有公司名 或 只有邮箱）：能做初步排查，结论最多到『疑似』",
    "L2": "双锚点（公司名 + 邮箱/域名/官网）：能出可判定结论",
    "L3": "多锚点（再加电话/LinkedIn/采购历史）：可交叉验证，置信度高",
}


def assess_info_sufficiency(fields: Dict[str, Any]) -> Dict[str, Any]:
    """判断字段是否足够做"能给出结论"的背调，并给出下一步该问什么。"""
    present = {key for key, value in (fields or {}).items() if str(value or "").strip()}
    has_name = bool(present & {"company_name"})
    has_domain = bool(present & {"domain", "website", "email"})
    has_weak = bool(present & {"phone", "whatsapp"})
    has_context = bool(present & {"country"})

    if has_name and has_domain:
        level = "L3" if has_weak else "L2"
    elif has_name or has_domain:
        level = "L1"
    elif has_context or has_weak:
        level = "L0"
    else:
        level = "L0"

    missing: List[str] = []
    optional: List[str] = []
    if not has_name:
        missing.append("company_name")
    if not has_domain:
        missing.append("website/email")
    if not has_weak:
        optional.append("phone/whatsapp")

    return {
        "level": level,
        "level_desc": INFO_LEVELS[level],
        "can_enrich": level in {"L2", "L3"},
        "missing": missing,
        "optional": optional,
        "reason": (
            "信息够：可以出结论（%s）" % INFO_LEVELS[level] if level in {"L2", "L3"}
            else "信息不够：现在跑背调只会得到一堆来源而没有结论，先继续要信息（%s）" % INFO_LEVELS[level]
        ),
    }


# ------------------------------------------------------------------ ③ 背调结论化

# 可插拔来源注册表：以后接各国企查查 / 海关数据 / 老板提到的渠道，往这里加一项即可
SOURCE_REGISTRY: List[Dict[str, str]] = [
    {"key": "official_website", "label": "公司官网", "needs": "domain/website", "gives": "主营/规模/地区/真实性"},
    {"key": "web_search", "label": "公开搜索", "needs": "company_name 或 domain", "gives": "补充线索"},
    {"key": "company_registry", "label": "企业注册信息", "needs": "company_name + country", "gives": "注册状态/成立时间"},
    {"key": "tikhub_linkedin_company", "label": "职业社媒（LinkedIn）", "needs": "company_name 或 domain", "gives": "员工规模/业务范围"},
    {"key": "customs_data", "label": "海关数据（待接入）", "needs": "company_name + country", "gives": "真实进口记录/采购品类/频次"},
    {"key": "local_registry", "label": "各国企业信息平台（待接入）", "needs": "company_name + country", "gives": "当地主体与经营状态"},
]

VERDICT_LABELS = {
    "qualified": "值得跟（A/B）",
    "watch": "继续观察（C）",
    "not_fit": "不匹配（D）",
    "suspicious": "可疑/垃圾",
    "needs_more_info": "信息不够，先要信息",
}

FIELD_LABELS = {
    "company_name": "公司名称",
    "domain": "官网/域名",
    "website": "官网",
    "email": "邮箱",
    "phone": "电话",
    "whatsapp": "WhatsApp",
    "country": "所在国家",
    "buyer_login_id": "平台账号",
}


def build_info_request_instruction(
    sufficiency: Dict[str, Any],
    persona: Optional[Dict[str, Any]] = None,
    *,
    stage: str = "collect",
) -> str:
    """资料不够时：按缺口生成"继续追问"的话术指令；够了就转成确认+收尾。"""
    persona = persona or {}
    common = (
        "人设：%s %s @ %s；风格：%s。"
        % (
            persona.get("name") or "",
            persona.get("title") or "",
            persona.get("company") or "",
            persona.get("style") or "简短、口语化、专业",
        )
    )
    if not sufficiency.get("can_enrich"):
        pretty = {"company_name": "公司名称", "website/email": "官网或邮箱", "domain": "官网/域名", "email": "邮箱"}
        missing = [pretty.get(item, FIELD_LABELS.get(item.split("/")[0], item))
                   for item in (sufficiency.get("missing") or [])]
        ask = " 或 ".join(missing[:2]) if missing else "公司名称或官网/邮箱"
        return (
            common
            + "本轮目的：继续要信息（现在资料不够做背景核验）。"
            + "要问的字段：%s。" % ask
            + "要求：一次只问 1-2 个字段；先给一句对对方有用的价值/确认（比如按需求给他推荐方向），再自然地问；"
            "不要罗列清单、不要像表格、不要催促成交；如果对方已经给了部分信息，先复述确认再问下一个。"
        )
    return (
        common
        + "本轮目的：信息已够（可以做背景核验），所以这一轮用来确认关键需求并自然收尾："
        "确认型号/数量/交期这类关键点，告诉对方你会整理一份对应方案/报价，不要催促下单。"
    )


def backtest_verdict(
    *,
    fields: Dict[str, Any],
    evidence_count: int = 0,
    sources_hit: Optional[List[str]] = None,
    grade: str = "",
    score: Optional[int] = None,
) -> Dict[str, Any]:
    """把证据收敛成结论：verdict + 置信度 + 三句话理由 + 缺口 + 已用来源。不再只堆来源。"""
    sufficiency = assess_info_sufficiency(fields)
    sources_hit = [str(item) for item in (sources_hit or []) if str(item or "").strip()]
    grade = str(grade or "").upper()
    score_value = int(score or 0)

    if not sufficiency["can_enrich"]:
        return {
            "verdict": "needs_more_info",
            "verdict_label": VERDICT_LABELS["needs_more_info"],
            "confidence": "low",
            "why": [sufficiency["reason"]],
            "gaps": sufficiency["missing"],
            "sources_used": sources_hit,
            "notify": "none",
            "updated_basis": {"level": sufficiency["level"], "evidence": evidence_count},
        }

    if grade in {"A", "B"} and evidence_count >= 3:
        verdict, confidence = "qualified", "high" if evidence_count >= 5 else "medium"
    elif grade in {"A", "B"}:
        verdict, confidence = "qualified", "medium"
    elif grade == "C":
        verdict, confidence = "watch", "medium"
    elif grade in {"D"}:
        verdict, confidence = "not_fit", "medium"
    else:
        verdict = "watch" if score_value >= 40 else "not_fit"
        confidence = "low"

    why: List[str] = []
    why.append("%s；已用来源 %d 个（%s）" % (
        sufficiency["level_desc"], len(sources_hit), "、".join(sources_hit[:4]) or "无"))
    why.append("分级 %s / 评分 %s / 证据 %d 条" % (grade or "未分级", score_value or "—", evidence_count))
    if sufficiency["missing"]:
        why.append("可加置信度：补 " + "、".join(sufficiency["missing"][:2]))

    notify = "business" if verdict == "qualified" else "none"      # 只内部通知，绝不发给客户
    return {
        "verdict": verdict,
        "verdict_label": VERDICT_LABELS.get(verdict, verdict),
        "confidence": confidence,
        "why": why,
        "gaps": sufficiency["missing"],
        "sources_used": sources_hit,
        "notify": notify,
        "updated_basis": {"level": sufficiency["level"], "evidence": evidence_count},
    }
