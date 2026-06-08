"""
大模型客户端 - 用于高意向评论筛选
"""
import json
import re
from typing import Any, Callable, Dict, List, Optional

import requests


def _safe_event_log(event_logger: Optional[Callable], event: str, **fields) -> None:
    if event_logger is None:
        return
    try:
        event_logger(event, **fields)
    except Exception:
        pass

DEFAULT_MODEL = "gpt-5.4"

# 筛选评论的系统提示词（索引输出，避免丢失用户主键）
FILTER_PROMPT_SYSTEM = """你是一个小红书运营专家。请分析评论，筛选高意向用户。

高意向客户特征：
1. 询问价格、费用、报价
2. 询问如何报名、如何购买
3. 表达强烈兴趣，想了解更多
4. 询问具体细节（课程内容、效果、时间等）
5. 有明确需求，描述自己情况

严格返回JSON，格式如下：
{
    "high_intent_refs": [
        {
            "comment_index": 1,
            "intent_level": "high/medium/low",
            "reason": "筛选理由",
            "score": 0.0
        }
    ]
}

要求：
- comment_index 为评论列表中的序号（从1开始）
- score 范围 0~1
- 仅返回 high 和 medium 意向
- 不要返回列表里不存在的序号
- 只返回 JSON，不要任何额外文本
"""

DOUYIN_TRANSACTION_FILTER_PROMPT_SYSTEM = """你是一个抖音评论筛选助手。

你的任务不是套用固定的“强成交”标准，而是优先执行用户提供的“精准客户筛选提示词”。
如果用户给了筛选提示词，必须以那份提示词为最高优先级，不要再额外套用更严格的隐藏规则。
只需要判断“这条评论是不是精准客户”，不要做分层，不要私自拆成强弱等级。
符合筛选提示词的评论就保留，不符合就不要返回。

严格返回JSON，格式如下：
{
    "high_intent_refs": [
        {
            "comment_index": 1,
            "intent_level": "high/medium/low",
            "reason": "筛选理由",
            "score": 0.0
        }
    ]
}

要求：
- comment_index 为评论列表中的序号（从1开始）
- score 范围 0~1
- 如果判定为精准客户，intent_level 统一返回 high
- 不要返回列表里不存在的序号
- 只返回 JSON，不要任何额外文本
"""

DOUYIN_REVERSE_FILTER_PROMPT_SYSTEM = """你是一个抖音评论反向筛选助手。

你的任务是尽量保留“和当前视频主题相关、且有意义的互动评论”，只排除明显不应该进入精准客户的内容。
只需要判断“这条评论是不是精准客户”，不要做分层，不要私自拆成强弱等级。

以下内容应排除：
1. 纯表情、纯符号、纯语气词、纯无意义短句
2. 纯路过、打卡、哈哈、支持、不错、来了这类无实际信息的评论
3. 纯辱骂、攻击、阴阳怪气、人身攻击
4. 完全无关内容、广告刷屏、恶意灌水
5. 虽然是正常说话，但和当前视频标题、当前话题、当前搜索关键词明显无关的闲聊，例如问吃了吗、穿什么、在干嘛这类跑题内容

以下内容应保留：
1. 有真实表达、真实观点、真实问题，且和当前视频主题相关的评论
2. 表达需求、兴趣、咨询、了解、联系、尝试、合作、购买意愿的评论
3. 虽然没有直接成交，但明显是在围绕当前视频内容认真互动、认真提问、认真表达想法的评论

严格返回JSON，格式如下：
{
    "high_intent_refs": [
        {
            "comment_index": 1,
            "intent_level": "high/medium/low",
            "reason": "筛选理由",
            "score": 0.0
        }
    ]
}

要求：
- comment_index 为评论列表中的序号（从1开始）
- score 范围 0~1
- 如果判定为精准客户，intent_level 统一返回 high
- 不要返回列表里不存在的序号
- 只返回 JSON，不要任何额外文本
"""

XHS_FILTER_PROMPT_SYSTEM = """你是一个小红书评论筛选助手，负责从评论里找出值得继续跟进的潜在客户。

执行规则：
1. 如果用户提供了“高意向筛选提示词”，必须把那份提示词视为最高优先级，按用户定义的人群口径筛选。
2. 只要评论明显符合用户提示词，就可以保留；不要额外强行要求必须出现价格、报名、购买等字样。
3. 如果用户没有提供自定义提示词，再参考默认高意向标准：
   - 询问价格、费用、报价
   - 询问如何报名、如何购买、怎么开始
   - 表达明确兴趣，想进一步了解
   - 询问具体细节、效果、流程、适合人群
   - 描述自身需求，希望获得方案或帮助
4. 拿不准但明显值得继续跟进的，也可以保留并标记为 medium，不要因为不够“强成交”就全部过滤掉。

严格返回 JSON，格式如下：
{
  "high_intent_refs": [
    {
      "comment_index": 1,
      "intent_level": "high/medium/low",
      "reason": "筛选理由",
      "score": 0.0
    }
  ]
}

要求：
- comment_index 是评论列表中的序号，从 1 开始
- score 范围 0~1
- 只返回你决定保留的评论
- 不要返回列表里不存在的序号
- 只返回 JSON，不要附加解释文本"""

class AIClient:
    """大模型客户端"""

    def __init__(self, api_url: str, api_key: str, model: str = DEFAULT_MODEL):
        self.api_url = api_url
        self.api_key = api_key
        self.model = model
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }

    def filter_comments(
        self,
        post_title: str,
        comments: List[Dict],
        direction: str = "",
        intent_profile: str = "default",
        custom_prompt: str = "",
        filter_strategy: str = "prompt",
        event_logger: Optional[Callable] = None,
    ) -> List[Dict]:
        """
        用AI筛选高意向评论

        Args:
            post_title: 帖子标题
            comments: 评论列表 [{"username": "用户", "content": "评论"}]
            direction: 评论生成方向（可选）

        Returns:
            高意向用户列表（保留 user_id/xsec_token/comment_id 等主键）
        """
        if not comments:
            return []

        batch_size = 80
        merged: List[Dict] = []
        seen = set()

        total_batches = (len(comments) + batch_size - 1) // batch_size
        _safe_event_log(
            event_logger,
            "ai_filter_invoke",
            post_title=post_title,
            comments_total=len(comments),
            batch_size=batch_size,
            total_batches=total_batches,
            intent_profile=intent_profile,
            filter_strategy=filter_strategy,
            direction=direction,
            custom_prompt=custom_prompt,
        )

        for start in range(0, len(comments), batch_size):
            candidate_comments = comments[start:start + batch_size]
            batch_result = self._filter_comments_batch(
                post_title,
                candidate_comments,
                direction,
                intent_profile=intent_profile,
                custom_prompt=custom_prompt,
                filter_strategy=filter_strategy,
                event_logger=event_logger,
                batch_index=start // batch_size + 1,
                total_batches=total_batches,
            )
            for row in batch_result:
                key = row.get("comment_id") or f"{row.get('user_id', '')}|{row.get('content', '')}|{row.get('comment_time', '')}"
                if key in seen:
                    continue
                seen.add(key)
                merged.append(row)

        return merged

    def _filter_comments_batch(
        self,
        post_title: str,
        comments: List[Dict],
        direction: str = "",
        intent_profile: str = "default",
        custom_prompt: str = "",
        filter_strategy: str = "prompt",
        event_logger: Optional[Callable] = None,
        batch_index: int = 1,
        total_batches: int = 1,
    ) -> List[Dict]:
        if not comments:
            return []
        return self._filter_comments_batch_v2(
            post_title,
            comments,
            direction=direction,
            intent_profile=intent_profile,
            custom_prompt=custom_prompt,
            filter_strategy=filter_strategy,
            event_logger=event_logger,
            batch_index=batch_index,
            total_batches=total_batches,
        )

        candidate_comments = comments
        lines = []
        for i, c in enumerate(candidate_comments, start=1):
            lines.append(
                f"{i}. username={c.get('username', '')} | "
                f"user_id={c.get('user_id', '')} | "
                f"content={c.get('content', '')}"
            )

        is_douyin_transactional = intent_profile == "douyin_transactional"
        is_reverse_filter = is_douyin_transactional and str(filter_strategy or "prompt").strip().lower() == "reverse"
        custom_prompt_text = str(custom_prompt or "").strip()
        direction_text = (
            f"\n本次精准客户筛选提示词（最高优先级，直接决定筛选口径）:\n{direction}"
            if direction and is_douyin_transactional and not is_reverse_filter
            else (f"\n评论方向参考: {direction}" if direction else "")
        )
        reverse_rule_text = (
            f"\n本次反向筛选补充说明（可选参考）:\n{direction}"
            if direction and is_reverse_filter
            else ""
        )
        extra_rule_text = ""
        if custom_prompt_text and not is_douyin_transactional:
            extra_rule_text = f"\n本次高意向筛选额外规则（优先按此执行）:\n{custom_prompt_text}"
        user_prompt = f"""帖子标题: {post_title}

评论列表:
{chr(10).join(lines)}{direction_text}{reverse_rule_text}{extra_rule_text}

{"请严格按上面的筛选提示词筛选评论，只判断是否属于精准客户；符合就返回，不符合不要返回；返回结果里的 intent_level 统一写 high，并返回JSON。" if is_douyin_transactional and not is_reverse_filter else ("请按反向筛选规则处理：只保留和当前视频主题相关的有效互动；排除无意义、表情、攻击、灌水，以及和视频标题或当前话题明显无关的闲聊评论；返回结果里的 intent_level 统一写 high，并返回JSON。" if is_reverse_filter else "请筛选高意向用户并返回JSON。")}"""

        payload = {
            "model": self.model,
            "stream": False,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        DOUYIN_REVERSE_FILTER_PROMPT_SYSTEM
                        if is_reverse_filter
                        else DOUYIN_TRANSACTION_FILTER_PROMPT_SYSTEM
                        if is_douyin_transactional
                        else FILTER_PROMPT_SYSTEM
                    ),
                },
                {"role": "user", "content": user_prompt}
            ],
            "max_tokens": 2000
        }

        try:
            response = requests.post(
                self.api_url,
                json=payload,
                headers=self.headers,
                timeout=60
            )

            if response.status_code == 200:
                result = response.json()
                content = result.get("choices", [{}])[0].get("message", {}).get("content", "")

                data = self._parse_json_content(content)
                refs = data.get("high_intent_refs") if isinstance(data, dict) else None
                if isinstance(refs, list):
                    mapped = self._map_refs_to_comments(refs, candidate_comments)
                    if is_reverse_filter:
                        return self._merge_comment_rows(
                            mapped,
                            self._reverse_filter_comments(candidate_comments, post_title),
                        )
                    return mapped
                return self._fallback_filter(
                    candidate_comments,
                    intent_profile=intent_profile,
                    filter_strategy=filter_strategy,
                    post_title=post_title,
                )
            else:
                print(f"API错误: {response.status_code}, body={response.text[:500]}")
                return self._fallback_filter(
                    candidate_comments,
                    intent_profile=intent_profile,
                    filter_strategy=filter_strategy,
                    post_title=post_title,
                )

        except Exception as e:
            print(f"调用AI失败: {str(e)}")
            return self._fallback_filter(
                candidate_comments,
                intent_profile=intent_profile,
                filter_strategy=filter_strategy,
                post_title=post_title,
            )

    def _filter_comments_batch_v2(
        self,
        post_title: str,
        comments: List[Dict],
        direction: str = "",
        intent_profile: str = "default",
        custom_prompt: str = "",
        filter_strategy: str = "prompt",
        event_logger: Optional[Callable] = None,
        batch_index: int = 1,
        total_batches: int = 1,
    ) -> List[Dict]:
        candidate_comments = comments
        lines = []
        for i, c in enumerate(candidate_comments, start=1):
            lines.append(
                f"{i}. username={c.get('username', '')} | "
                f"user_id={c.get('user_id', '')} | "
                f"content={c.get('content', '')}"
            )

        is_douyin_transactional = intent_profile == "douyin_transactional"
        is_reverse_filter = is_douyin_transactional and str(filter_strategy or "prompt").strip().lower() == "reverse"
        custom_prompt_text = str(custom_prompt or "").strip()
        direction_text = (
            f"\n本次精准客户筛选提示词（最高优先级，直接决定筛选口径）:\n{direction}"
            if direction and is_douyin_transactional and not is_reverse_filter
            else ""
        )
        reverse_rule_text = (
            f"\n本次反向筛选补充说明（可选参考）:\n{direction}"
            if direction and is_reverse_filter
            else ""
        )
        extra_rule_text = ""
        if custom_prompt_text and not is_douyin_transactional:
            extra_rule_text = (
                "\n本次高意向筛选提示词（最高优先级，请严格按这份口径筛选；"
                "只要明显符合就可以保留，不要再套用更严格的隐藏标准）:\n"
                f"{custom_prompt_text}"
            )

        user_prompt = f"""帖子标题: {post_title}

评论列表:
{chr(10).join(lines)}{direction_text}{reverse_rule_text}{extra_rule_text}

{"请严格按上面的筛选提示词筛选评论，只判断是否属于精准客户；符合就返回，不符合不要返回；返回结果里的 intent_level 统一写 high，并返回 JSON。" if is_douyin_transactional and not is_reverse_filter else ("请按反向筛选规则处理：只保留和当前视频主题相关的有效互动；排除无意义、表情、攻击、灌水，以及和视频标题或当前话题明显无关的闲聊评论；返回结果里的 intent_level 统一写 high，并返回 JSON。" if is_reverse_filter else ("如果上面提供了“高意向筛选提示词”，请把它当作最高优先级；只要明显符合提示词就保留，拿不准但值得继续跟进的也可以保留为 medium。请筛选高意向用户并返回 JSON。" if custom_prompt_text else "请根据默认高意向标准筛选值得继续跟进的用户，并返回 JSON。"))}"""

        system_prompt_name = (
            "DOUYIN_REVERSE_FILTER_PROMPT_SYSTEM"
            if is_reverse_filter
            else "DOUYIN_TRANSACTION_FILTER_PROMPT_SYSTEM"
            if is_douyin_transactional
            else "XHS_FILTER_PROMPT_SYSTEM"
        )
        system_prompt = (
            DOUYIN_REVERSE_FILTER_PROMPT_SYSTEM
            if is_reverse_filter
            else DOUYIN_TRANSACTION_FILTER_PROMPT_SYSTEM
            if is_douyin_transactional
            else XHS_FILTER_PROMPT_SYSTEM
        )

        audit_comments = [
            {
                "comment_index": i,
                "username": c.get("username", ""),
                "user_id": c.get("user_id", ""),
                "content": c.get("content", ""),
                "comment_time": c.get("comment_time", ""),
                "location": c.get("location") or c.get("region") or c.get("ip_location") or "",
                "profile_url": c.get("profile_url", ""),
            }
            for i, c in enumerate(candidate_comments, start=1)
        ]

        payload = {
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": 2000,
        }

        _safe_event_log(
            event_logger,
            "ai_request",
            batch_index=batch_index,
            total_batches=total_batches,
            comments_in_batch=len(candidate_comments),
            model=self.model,
            api_url=self.api_url,
            system_prompt_name=system_prompt_name,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            input_comments=audit_comments,
            direction=direction,
            custom_prompt=custom_prompt_text,
            is_douyin_transactional=is_douyin_transactional,
            is_reverse_filter=is_reverse_filter,
        )

        try:
            response = requests.post(
                self.api_url,
                json=payload,
                headers=self.headers,
                timeout=60,
            )
            if response.status_code == 200:
                result = response.json()
                content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
                data = self._parse_json_content(content)
                refs = data.get("high_intent_refs") if isinstance(data, dict) else None
                _safe_event_log(
                    event_logger,
                    "ai_response",
                    batch_index=batch_index,
                    total_batches=total_batches,
                    http_status=response.status_code,
                    raw_response=content,
                    parsed_refs=refs if isinstance(refs, list) else [],
                    refs_count=len(refs) if isinstance(refs, list) else 0,
                    parsed_ok=isinstance(refs, list),
                )
                if isinstance(refs, list):
                    mapped = self._map_refs_to_comments_v2(
                        refs,
                        candidate_comments,
                        allow_low=bool(custom_prompt_text and not is_douyin_transactional),
                    )
                    _safe_event_log(
                        event_logger,
                        "ai_mapped",
                        batch_index=batch_index,
                        total_batches=total_batches,
                        refs_count=len(refs),
                        mapped_count=len(mapped),
                        mapped_users=[
                            {
                                "comment_index": row.get("comment_index", ""),
                                "username": row.get("username", ""),
                                "user_id": row.get("user_id", ""),
                                "content": row.get("content", ""),
                                "intent_level": row.get("intent_level", ""),
                                "intent_reason": row.get("intent_reason", ""),
                                "intent_score": row.get("intent_score", ""),
                            }
                            for row in mapped
                        ],
                        is_reverse_filter=is_reverse_filter,
                    )
                    if is_reverse_filter:
                        merged = self._merge_comment_rows(
                            mapped,
                            self._reverse_filter_comments(candidate_comments, post_title),
                        )
                        _safe_event_log(
                            event_logger,
                            "ai_reverse_merged",
                            batch_index=batch_index,
                            mapped_count=len(mapped),
                            merged_count=len(merged),
                        )
                        return merged
                    return mapped
            else:
                _safe_event_log(
                    event_logger,
                    "ai_http_error",
                    batch_index=batch_index,
                    http_status=response.status_code,
                    raw_response=response.text[:600],
                )
                print(f"API错误: {response.status_code}, body={response.text[:500]}")
        except Exception as e:
            _safe_event_log(
                event_logger,
                "ai_exception",
                batch_index=batch_index,
                error=str(e),
            )
            print(f"调用AI失败: {str(e)}")

        fallback = self._fallback_filter_v2(
            candidate_comments,
            intent_profile=intent_profile,
            filter_strategy=filter_strategy,
            post_title=post_title,
            custom_prompt=custom_prompt_text,
        )
        _safe_event_log(
            event_logger,
            "ai_fallback",
            batch_index=batch_index,
            total_batches=total_batches,
            comments_in_batch=len(candidate_comments),
            fallback_count=len(fallback),
            fallback_used=True,
        )
        return fallback

    def _parse_json_content(self, content: str) -> Dict[str, Any]:
        """从模型返回中尽量解析出JSON对象。"""
        if not content:
            return {}

        text = content.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                return {}
        return {}

    def _map_refs_to_comments_v2(
        self,
        refs: List[Dict[str, Any]],
        comments: List[Dict],
        *,
        allow_low: bool = False,
    ) -> List[Dict]:
        mapped: List[Dict] = []
        seen = set()
        allowed_levels = {"high", "medium", "low"} if allow_low else {"high", "medium"}

        for item in refs:
            try:
                idx = int(item.get("comment_index", 0)) - 1
            except (TypeError, ValueError):
                continue
            if idx < 0 or idx >= len(comments):
                continue

            source = comments[idx]
            key = source.get("comment_id") or f"{source.get('user_id', '')}|{source.get('content', '')}|{source.get('comment_time', '')}"
            if key in seen:
                continue

            level = str(item.get("intent_level", "medium")).lower()
            if level not in allowed_levels:
                continue
            normalized_level = "medium" if allow_low and level == "low" else level

            mapped.append({
                "comment_index": idx + 1,
                "username": source.get("username", ""),
                "user_id": source.get("user_id", ""),
                "user_xsec_token": source.get("user_xsec_token", ""),
                "comment_id": source.get("comment_id", ""),
                "comment": source.get("content", ""),
                "content": source.get("content", ""),
                "comment_time": source.get("comment_time", ""),
                "location": source.get("location", source.get("ip_location", "")),
                "ip_location": source.get("ip_location", source.get("location", "")),
                "like_count": source.get("like_count", ""),
                "reply_count": source.get("reply_count", ""),
                "profile_url": source.get("profile_url", ""),
                "avatar_url": source.get("avatar_url", ""),
                "reason": item.get("reason", ""),
                "intent_level": normalized_level,
                "score": item.get("score", 0),
            })
            seen.add(key)

        return mapped

    def _map_refs_to_comments(self, refs: List[Dict[str, Any]], comments: List[Dict]) -> List[Dict]:
        """将模型返回的 comment_index 映射回原始评论对象。"""
        if not refs:
            return []

        mapped: List[Dict] = []
        seen = set()
        for item in refs:
            try:
                idx = int(item.get("comment_index", 0)) - 1
            except (TypeError, ValueError):
                continue
            if idx < 0 or idx >= len(comments):
                continue

            source = comments[idx]
            key = source.get("comment_id") or f"{source.get('user_id', '')}|{source.get('content', '')}|{source.get('comment_time', '')}"
            if key in seen:
                continue

            level = str(item.get("intent_level", "medium")).lower()
            if level not in ("high", "medium"):
                continue

            mapped.append({
                "comment_index": idx + 1,
                "username": source.get("username", ""),
                "user_id": source.get("user_id", ""),
                "user_xsec_token": source.get("user_xsec_token", ""),
                "comment_id": source.get("comment_id", ""),
                "comment": source.get("content", ""),
                "content": source.get("content", ""),
                "comment_time": source.get("comment_time", ""),
                "location": source.get("location", source.get("ip_location", "")),
                "ip_location": source.get("ip_location", source.get("location", "")),
                "like_count": source.get("like_count", ""),
                "reply_count": source.get("reply_count", ""),
                "profile_url": source.get("profile_url", ""),
                "avatar_url": source.get("avatar_url", ""),
                "reason": item.get("reason", ""),
                "intent_level": level,
                "score": item.get("score", 0),
            })
            seen.add(key)

        return mapped

    def _merge_comment_rows(self, primary: List[Dict], extra: List[Dict]) -> List[Dict]:
        """合并 AI 结果与召回结果，优先保留 AI 命中的原始理由。"""
        merged: List[Dict] = []
        seen = set()
        for row in list(primary or []) + list(extra or []):
            key = row.get("comment_id") or f"{row.get('user_id', '')}|{row.get('content', '')}|{row.get('comment_time', '')}"
            if key in seen:
                continue
            seen.add(key)
            merged.append(row)
        return merged

    def _extract_prompt_keywords(self, custom_prompt: str) -> tuple[str, ...]:
        text = str(custom_prompt or "").strip().lower()
        if not text:
            return ()

        stopwords = {
            "高意向", "精准客户", "客户", "用户", "评论", "内容", "的人", "可以", "需要", "进行",
            "筛选", "符合", "属于", "优先", "保留", "不要", "排除", "提示词", "口径", "标准",
        }
        tokens: List[str] = []
        for token in re.findall(r"[A-Za-z0-9\u4e00-\u9fff]{2,12}", text):
            token = token.strip()
            if not token or token.isdigit() or token in stopwords:
                continue
            tokens.append(token)
        return tuple(dict.fromkeys(tokens))

    def _fallback_filter_v2(
        self,
        comments: List[Dict],
        intent_profile: str = "default",
        filter_strategy: str = "prompt",
        post_title: str = "",
        custom_prompt: str = "",
    ) -> List[Dict]:
        if intent_profile == "douyin_transactional" and str(filter_strategy or "prompt").strip().lower() == "reverse":
            return self._reverse_filter_comments(comments, post_title)
        if intent_profile == "douyin_transactional":
            return self._fallback_filter(
                comments,
                intent_profile=intent_profile,
                filter_strategy=filter_strategy,
                post_title=post_title,
            )

        prompt_keywords = self._extract_prompt_keywords(custom_prompt)
        keywords = (
            "多少", "价格", "报价", "怎么", "如何", "咨询", "联系方式", "可以吗", "吗", "？", "?",
            "了解", "想了解", "流程", "细节", "资料", "方案", "适合", "怎么做", "怎么开始",
            "想试试", "需要", "需求",
        )
        if prompt_keywords:
            keywords = tuple(dict.fromkeys(list(keywords) + list(prompt_keywords)))

        fallback = []
        seen = set()
        for idx, c in enumerate(comments, start=1):
            content = str(c.get("content", ""))
            compact_content = "".join(content.split())
            if not compact_content:
                continue
            if not any(k in compact_content for k in keywords):
                continue
            key = c.get("comment_id") or f"{c.get('user_id', '')}|{content}|{c.get('comment_time', '')}"
            if key in seen:
                continue
            fallback.append({
                "comment_index": idx,
                "username": c.get("username", ""),
                "user_id": c.get("user_id", ""),
                "user_xsec_token": c.get("user_xsec_token", ""),
                "comment_id": c.get("comment_id", ""),
                "comment": content,
                "content": content,
                "comment_time": c.get("comment_time", ""),
                "location": c.get("location", c.get("ip_location", "")),
                "ip_location": c.get("ip_location", c.get("location", "")),
                "like_count": c.get("like_count", ""),
                "reply_count": c.get("reply_count", ""),
                "profile_url": c.get("profile_url", ""),
                "avatar_url": c.get("avatar_url", ""),
                "reason": "关键词兜底筛选",
                "intent_level": "medium",
                "score": 0.55,
            })
            seen.add(key)
        return fallback

    def _fallback_filter(
        self,
        comments: List[Dict],
        intent_profile: str = "default",
        filter_strategy: str = "prompt",
        post_title: str = "",
    ) -> List[Dict]:
        """
        当模型返回异常时使用关键词兜底，避免整批丢失。
        仅作为保底策略，优先使用模型结果。
        """
        if intent_profile == "douyin_transactional" and str(filter_strategy or "prompt").strip().lower() == "reverse":
            return self._reverse_filter_comments(comments, post_title)
        if intent_profile == "douyin_transactional":
            keywords = (
                "多少钱", "价格", "费用", "收费", "报价", "套餐",
                "怎么买", "怎么下单", "怎么购买", "怎么报名", "哪里报名", "想报名", "想买", "下单",
                "怎么合作", "合作", "商务合作", "加盟", "代理",
                "怎么联系", "联系方式", "求联系方式", "电话", "微信", "私信", "私聊", "对接",
                "咨询", "想咨询", "想了解", "了解一下", "详细聊聊", "给个方案", "有没有方案",
                "我需要", "我想", "适合我吗", "适不适合我", "能不能做", "可以做吗",
                "感兴趣", "有兴趣", "想试试", "想做", "怎么弄", "怎么搞", "怎么开始",
                "想入手", "入手", "能下手吗", "能不能买", "可以买吗", "可以入吗",
                "有没有", "有吗", "怎么选", "推荐一下", "回复我", "回我一下",
            )
            exclude_keywords = ()
            fallback_reason = "抖音意向关键词兜底筛选"
            fallback_score = 0.6
        else:
            keywords = ("多少", "价格", "报价", "怎么", "如何", "咨询", "联系方式", "可以吗", "吗", "？", "?")
            exclude_keywords = ()
            fallback_reason = "关键词兜底筛选"
            fallback_score = 0.55
        fallback = []
        seen = set()
        for idx, c in enumerate(comments, start=1):
            content = str(c.get("content", ""))
            compact_content = "".join(content.split())
            if not content:
                continue
            if exclude_keywords and any(k in compact_content for k in exclude_keywords):
                continue
            if not any(k in compact_content for k in keywords):
                continue
            key = c.get("comment_id") or f"{c.get('user_id', '')}|{content}|{c.get('comment_time', '')}"
            if key in seen:
                continue
            fallback.append({
                "comment_index": idx,
                "username": c.get("username", ""),
                "user_id": c.get("user_id", ""),
                "user_xsec_token": c.get("user_xsec_token", ""),
                "comment_id": c.get("comment_id", ""),
                "comment": content,
                "content": content,
                "comment_time": c.get("comment_time", ""),
                "location": c.get("location", c.get("ip_location", "")),
                "ip_location": c.get("ip_location", c.get("location", "")),
                "like_count": c.get("like_count", ""),
                "reply_count": c.get("reply_count", ""),
                "profile_url": c.get("profile_url", ""),
                "avatar_url": c.get("avatar_url", ""),
                "reason": fallback_reason,
                "intent_level": "medium",
                "score": fallback_score,
            })
            seen.add(key)
        return fallback

    def _extract_topic_tokens(self, text: str) -> set[str]:
        raw = str(text or "").strip().lower()
        if not raw:
            return set()
        stopwords = {
            "什么", "怎么", "可以", "一下", "一个", "这个", "那个", "真的", "就是", "有没有",
            "视频", "作品", "内容", "关于", "分享", "推荐", "看看", "你们", "我们", "他们",
        }
        tokens: set[str] = set()
        for part in re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]+", raw):
            if re.fullmatch(r"[A-Za-z0-9]+", part):
                if len(part) >= 2:
                    tokens.add(part)
                continue
            if len(part) >= 2 and part not in stopwords:
                tokens.add(part)
            for size in (4, 3, 2):
                if len(part) < size:
                    continue
                for index in range(0, len(part) - size + 1):
                    token = part[index:index + size]
                    if token not in stopwords:
                        tokens.add(token)
        return tokens

    def _is_topic_related_comment(self, content: str, post_title: str) -> bool:
        compact_content = "".join(str(content or "").lower().split())
        compact_title = "".join(str(post_title or "").lower().split())
        if not compact_title:
            return True
        content_tokens = self._extract_topic_tokens(compact_content)
        title_tokens = self._extract_topic_tokens(compact_title)
        if not content_tokens or not title_tokens:
            return False
        if content_tokens & title_tokens:
            return True
        return any(token in compact_content for token in title_tokens if len(token) >= 2)

    def _reverse_filter_comments(self, comments: List[Dict], post_title: str = "") -> List[Dict]:
        trivial_exact = {
            "哈哈", "哈哈哈", "呵呵", "哦", "嗯", "好的", "收到", "来了", "路过", "打卡", "支持",
            "不错", "真好", "牛", "厉害", "赞", "好", "好看", "看看", "学到了", "收藏了",
            "先收藏", "滴滴", "在吗", "回我", "回一下", "回复一下",
        }
        off_topic_chat_exact = {
            "吃了吗", "吃饭了吗", "穿什么", "穿啥", "在干嘛", "干嘛呢", "睡了吗", "早安", "晚安",
            "几点睡", "忙什么", "约吗", "多大了", "结婚了吗", "帅不帅", "美不美",
        }
        abusive_keywords = (
            "骗子", "骗人", "垃圾", "滚", "有病", "智商税", "脑残", "傻", "装逼", "扯淡", "胡说",
            "坑人", "黑店", "去死", "恶心", "废物",
        )
        intent_keywords = (
            "价格", "费用", "收费", "报价", "多少钱", "怎么卖", "怎么买", "怎么下单", "怎么购买",
            "怎么报名", "合作", "加盟", "代理", "联系方式", "联系", "电话", "微信", "私信",
            "咨询", "了解", "想买", "想要", "需要", "求推荐", "推荐", "可以吗", "能吗", "适合",
        )
        meaningful = []
        seen = set()
        for idx, c in enumerate(comments, start=1):
            content = str(c.get("content", "") or "").strip()
            compact_content = "".join(content.split())
            if not compact_content:
                continue
            if any(word in compact_content for word in abusive_keywords):
                continue
            if compact_content in trivial_exact:
                continue
            if compact_content in off_topic_chat_exact:
                continue
            if not re.search(r"[\u4e00-\u9fffA-Za-z0-9]", compact_content):
                continue
            text_only = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]", "", compact_content)
            if len(text_only) <= 1:
                continue
            has_intent_signal = any(word in compact_content for word in intent_keywords)
            if not has_intent_signal and not self._is_topic_related_comment(compact_content, post_title):
                continue
            key = c.get("comment_id") or f"{c.get('user_id', '')}|{content}|{c.get('comment_time', '')}"
            if key in seen:
                continue
            meaningful.append({
                "comment_index": idx,
                "username": c.get("username", ""),
                "user_id": c.get("user_id", ""),
                "user_xsec_token": c.get("user_xsec_token", ""),
                "comment_id": c.get("comment_id", ""),
                "comment": content,
                "content": content,
                "comment_time": c.get("comment_time", ""),
                "location": c.get("location", c.get("ip_location", "")),
                "ip_location": c.get("ip_location", c.get("location", "")),
                "like_count": c.get("like_count", ""),
                "reply_count": c.get("reply_count", ""),
                "profile_url": c.get("profile_url", ""),
                "avatar_url": c.get("avatar_url", ""),
                "reason": "反向筛选：评论有实际内容，且与当前视频主题相关，不属于无意义/攻击/灌水",
                "intent_level": "high",
                "score": 0.58,
            })
            seen.add(key)
        return meaningful

    def filter_with_prompt(self, user_prompt: str) -> str:
        """直接使用prompt进行筛选，返回AI的原始回复"""
        try:
            response = requests.post(
                self.api_url,
                json={
                    "model": self.model,
                    "stream": False,
                    "messages": [
                        {"role": "system", "content": "你是一个专业的小红书内容分析师，擅长筛选高质量内容。"},
                        {"role": "user", "content": user_prompt}
                    ],
                    "max_tokens": 2000
                },
                headers=self.headers,
                timeout=60
            )

            if response.status_code == 200:
                result = response.json()
                return result.get("choices", [{}])[0].get("message", {}).get("content", "")
            else:
                print(f"API错误: {response.status_code}, body={response.text[:500]}")
                return "{}"
        except Exception as e:
            print(f"调用AI失败: {str(e)}")
            return "{}"

    def generate_comment(self, username: str, post_title: str, direction: str = "亲切、有趣") -> str:
        """
        用AI生成评论内容

        Args:
            username: 要评论的用户名
            post_title: 帖子标题
            direction: 评论风格方向

        Returns:
            生成的评论内容
        """
        system_prompt = f"""你是一个热情友好小红书用户。请根据以下信息生成一条评论。

要求：
1. 语气：{direction}
2. 自然真实，像真人评论
3. 不超过30字
4. 不要太官方或营销感
5. 可以适当提问或表达共鸣"""

        user_prompt = f"""帖子标题: {post_title}
要评论的用户: @{username}

请生成一条适合的评论。"""

        payload = {
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "max_tokens": 200
        }

        try:
            response = requests.post(
                self.api_url,
                json=payload,
                headers=self.headers,
                timeout=30
            )

            if response.status_code == 200:
                result = response.json()
                content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
                return content.strip()
            else:
                return "写得真好，支持一下！"

        except Exception as e:
            print(f"生成评论失败: {str(e)}")
            return "写得真好，支持一下！"

    @staticmethod
    def _clean_short_reply(text: str, limit: int = 32) -> str:
        value = " ".join(str(text or "").replace("\n", " ").replace("\r", " ").split()).strip()
        value = value.strip("\"'“”‘’")
        if len(value) > limit:
            value = value[:limit].rstrip("，。！？,.!? ")
        return value

    def generate_collection_reply(
        self,
        post_title: str,
        comment_content: str,
        direction: str = "",
    ) -> str:
        system_prompt = (
            "你是一个真实的小红书用户，正在回复别人对帖子的一条一级评论。\n"
            "要求：\n"
            "1. 回复非常短，12到28个中文字符，最多32字\n"
            "2. 自然口语，不要像客服，不要像营销号\n"
            "3. 要正面回应对方评论，可以轻微带一点经历感、故事感或观点\n"
            "4. 不能直接引流，不能出现主页、私信、加我、咨询、课程、报价、链接等词\n"
            "5. 目标是让对方觉得你有东西，愿意顺手点主页，但不能明说\n"
            "6. 只返回最终回复文本，不要解释"
        )
        user_prompt = (
            f"帖子主题：{post_title}\n"
            f"对方评论：{comment_content}\n"
            f"风格参考：{direction or '自然、克制、有点经历感'}\n"
            "请输出一条可直接发送的短回复。"
        )
        payload = {
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "max_tokens": 120
        }

        try:
            response = requests.post(
                self.api_url,
                json=payload,
                headers=self.headers,
                timeout=30
            )
            if response.status_code == 200:
                result = response.json()
                content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
                cleaned = self._clean_short_reply(content)
                if cleaned:
                    return cleaned
            else:
                print(f"AI生成采集回复失败: {response.status_code}, body={response.text[:300]}")
        except Exception as e:
            print(f"AI生成采集回复失败: {str(e)}")

        fallback = "我也是试了几版，后面才顺手" if any(token in str(comment_content or "") for token in ["?", "？", "吗", "么"]) else "我当时也踩过坑，后面才慢慢顺"
        return self._clean_short_reply(fallback)

    def test_connection(self) -> bool:
        """测试API连接"""
        try:
            payload = {
                "model": self.model,
                "stream": False,
                "messages": [
                    {"role": "user", "content": "你好"}
                ],
                "max_tokens": 50
            }

            response = requests.post(
                self.api_url,
                json=payload,
                headers=self.headers,
                timeout=10
            )

            return response.status_code == 200

        except Exception as e:
            print(f"API连接测试失败: {str(e)}")
            return False
