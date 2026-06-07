from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

from douyin_im_api_experiment import (
    DouyinImApiExperiment,
    ExperimentalDouyinAuth,
    HeaderBuilder,
    HeaderType,
    _platform_params,
    _with_a_bogus,
)


requests.packages.urllib3.disable_warnings()


class DouyinCommentApiExperimentError(RuntimeError):
    """Base error for the Douyin comment API experiment."""


def extract_aweme_id(video_url: str) -> str:
    text = str(video_url or "").strip()
    if not text:
        raise DouyinCommentApiExperimentError("缺少视频地址")
    match = re.search(r"/(?:video|note)/(\d+)", text)
    if match:
        return match.group(1)
    match = re.search(r"[?&]modal_id=(\d+)", text)
    if match:
        return match.group(1)
    if text.isdigit():
        return text
    raise DouyinCommentApiExperimentError(f"无法从地址中解析 aweme_id：{video_url}")


def build_video_url(video_url_or_aweme_id: str) -> str:
    aweme_id = extract_aweme_id(video_url_or_aweme_id)
    return f"https://www.douyin.com/video/{aweme_id}"


def extract_sec_user_id(profile_url_or_sec_uid: str) -> str:
    text = str(profile_url_or_sec_uid or "").strip()
    if not text:
        raise DouyinCommentApiExperimentError("缺少用户主页地址")
    match = re.search(r"/user/([^/?#]+)", text)
    if match:
        return match.group(1).strip()
    if re.match(r"^[A-Za-z0-9_=-]{8,}$", text):
        return text
    raise DouyinCommentApiExperimentError(f"无法从主页地址中解析 sec_user_id：{profile_url_or_sec_uid}")


def build_profile_url(profile_url_or_sec_uid: str) -> str:
    sec_user_id = extract_sec_user_id(profile_url_or_sec_uid)
    return f"https://www.douyin.com/user/{sec_user_id}"


def _format_compact_count(count: int) -> str:
    value = int(count or 0)
    if value >= 10000:
        text = f"{value / 10000:.1f}".rstrip("0").rstrip(".")
        return f"{text}万"
    return str(value) if value > 0 else ""


def _format_unix_timestamp_text(value: object) -> str:
    try:
        timestamp = int(value or 0)
    except Exception:
        timestamp = 0
    if timestamp <= 0:
        return ""
    try:
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(timestamp)


def _pick_image_url(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    candidates = [
        payload.get("cover"),
        payload.get("origin_cover"),
        payload.get("dynamic_cover"),
        payload.get("avatar_thumb"),
        payload.get("avatar_medium"),
        payload.get("avatar_larger"),
    ]
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        url_list = candidate.get("url_list") or []
        if isinstance(url_list, list):
            for url in url_list:
                text = str(url or "").strip()
                if text:
                    return text
    return ""


class DouyinCommentApiExperiment:
    def __init__(self, account_id: int, cdp_port: Optional[int] = None):
        self.account_id = int(account_id)
        self.im_client = DouyinImApiExperiment(account_id=account_id, cdp_port=cdp_port)

    async def extract_auth(self) -> ExperimentalDouyinAuth:
        return await self.im_client.extract_auth()

    def get_user_posts_page(
        self,
        auth: ExperimentalDouyinAuth,
        profile_url_or_sec_uid: str,
        *,
        max_cursor: int = 0,
        count: int = 18,
    ) -> Dict[str, Any]:
        sec_user_id = extract_sec_user_id(profile_url_or_sec_uid)
        referer = build_profile_url(sec_user_id)
        params = _platform_params()
        params.update(
            {
                "sec_user_id": sec_user_id,
                "max_cursor": str(max(0, int(max_cursor or 0))),
                "count": str(max(1, min(int(count or 18), 35))),
                "locate_query": "false",
                "show_live_replay_strategy": "1",
                "need_time_list": "1",
                "time_list_query": "0",
                "whale_cut_token": "",
                "cut_version": "1",
            }
        )
        params["webid"] = self._generate_webid(auth, referer)
        params["verifyFp"] = auth.cookie["s_v_web_id"]
        params["fp"] = auth.cookie["s_v_web_id"]
        params["msToken"] = auth.ms_token
        params = _with_a_bogus(params)
        headers = HeaderBuilder.build(HeaderType.GET)
        headers.set_referer(referer)
        response = requests.get(
            "https://www.douyin.com/aweme/v1/web/aweme/post/",
            headers=headers.get(),
            cookies=auth.cookie,
            params=params,
            verify=False,
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        self._raise_for_status_code(payload, context="获取用户作品列表")
        return payload

    def get_user_posts(
        self,
        auth: ExperimentalDouyinAuth,
        profile_url_or_sec_uid: str,
        *,
        max_videos: int = 10,
    ) -> Dict[str, Any]:
        sec_user_id = extract_sec_user_id(profile_url_or_sec_uid)
        videos: List[Dict[str, Any]] = []
        cursor = 0
        has_more = 1
        latest_author: Dict[str, Any] = {}
        max_videos = max(1, min(int(max_videos or 10), 50))
        while has_more == 1 and len(videos) < max_videos:
            page = self.get_user_posts_page(auth, sec_user_id, max_cursor=cursor, count=min(18, max_videos))
            aweme_list = page.get("aweme_list") or []
            if not isinstance(aweme_list, list) or not aweme_list:
                break
            for item in aweme_list:
                if not isinstance(item, dict):
                    continue
                normalized = self._normalize_aweme(item, order_index=len(videos) + 1)
                if not normalized.get("aweme_id"):
                    continue
                videos.append(normalized)
                author = item.get("author") if isinstance(item.get("author"), dict) else {}
                if author:
                    latest_author = author
                if len(videos) >= max_videos:
                    break
            has_more = int(page.get("has_more", 0) or 0)
            next_cursor = int(page.get("max_cursor", 0) or 0)
            if next_cursor == cursor:
                break
            cursor = next_cursor
        profile = self._normalize_author(latest_author, fallback_sec_user_id=sec_user_id)
        return {
            "profile": profile,
            "videos": videos,
            "sec_user_id": sec_user_id,
            "profile_url": build_profile_url(sec_user_id),
            "count": len(videos),
        }

    def get_work_info(self, auth: ExperimentalDouyinAuth, video_url: str) -> Dict[str, Any]:
        normalized_url = build_video_url(video_url)
        aweme_id = extract_aweme_id(normalized_url)
        params = _platform_params()
        params.update({"aweme_id": aweme_id})
        params["webid"] = HeaderBuilder.ua and self._generate_webid(auth, normalized_url)
        params["msToken"] = auth.ms_token
        params["verifyFp"] = auth.cookie["s_v_web_id"]
        params["fp"] = auth.cookie["s_v_web_id"]
        params = _with_a_bogus(params)
        headers = HeaderBuilder.build(HeaderType.GET)
        headers.set_referer(normalized_url)
        response = requests.get(
            "https://www.douyin.com/aweme/v1/web/aweme/detail/",
            headers=headers.get(),
            cookies=auth.cookie,
            params=params,
            verify=False,
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        self._raise_for_status_code(payload, context="获取作品详情")
        return payload

    def get_work_author_profile(self, auth: ExperimentalDouyinAuth, video_url: str) -> Dict[str, Any]:
        payload = self.get_work_info(auth, video_url)
        aweme = (
            payload.get("aweme_detail")
            or payload.get("aweme_info")
            or payload.get("aweme")
            or {}
        )
        if not isinstance(aweme, dict):
            aweme = {}
        author = aweme.get("author") if isinstance(aweme.get("author"), dict) else {}
        profile = self._normalize_author(author)
        profile["source_video_url"] = build_video_url(video_url)
        profile["source_aweme_id"] = str(aweme.get("aweme_id", "") or extract_aweme_id(video_url)).strip()
        profile["source_video_title"] = str(aweme.get("desc", "") or "").strip()
        if not profile.get("profile_url"):
            raise DouyinCommentApiExperimentError("作品详情中没有拿到作者主页，无法添加同行监控")
        return profile

    def get_comments_page(
        self,
        auth: ExperimentalDouyinAuth,
        video_url: str,
        *,
        cursor: int = 0,
        count: int = 20,
    ) -> Dict[str, Any]:
        normalized_url = build_video_url(video_url)
        aweme_id = extract_aweme_id(normalized_url)
        params = _platform_params()
        params.update(
            {
                "aweme_id": aweme_id,
                "cursor": str(max(0, int(cursor or 0))),
                "count": str(max(1, min(int(count or 20), 50))),
                "item_type": "0",
                "whale_cut_token": "",
                "cut_version": "1",
                "rcFT": "",
            }
        )
        params["webid"] = self._generate_webid(auth, normalized_url)
        params["verifyFp"] = auth.cookie["s_v_web_id"]
        params["fp"] = auth.cookie["s_v_web_id"]
        params["msToken"] = auth.ms_token
        params = _with_a_bogus(params)
        headers = HeaderBuilder.build(HeaderType.GET)
        headers.set_referer(normalized_url)
        response = requests.get(
            "https://www.douyin.com/aweme/v1/web/comment/list/",
            headers=headers.get(),
            cookies=auth.cookie,
            params=params,
            verify=False,
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        self._raise_for_status_code(payload, context="获取一级评论")
        return payload

    def get_comment_replies_page(
        self,
        auth: ExperimentalDouyinAuth,
        *,
        aweme_id: str,
        comment_id: str,
        cursor: int = 0,
        count: int = 10,
    ) -> Dict[str, Any]:
        aweme_id = str(aweme_id or "").strip()
        comment_id = str(comment_id or "").strip()
        if not aweme_id or not comment_id:
            raise DouyinCommentApiExperimentError("缺少 aweme_id 或 comment_id，无法获取二级回复")
        referer = f"https://www.douyin.com/video/{aweme_id}"
        params = _platform_params()
        params.update(
            {
                "item_id": aweme_id,
                "comment_id": comment_id,
                "cut_version": "1",
                "cursor": str(max(0, int(cursor or 0))),
                "count": str(max(1, min(int(count or 10), 20))),
                "item_type": "0",
            }
        )
        params["webid"] = self._generate_webid(auth, referer)
        params["verifyFp"] = auth.cookie["s_v_web_id"]
        params["fp"] = auth.cookie["s_v_web_id"]
        params["msToken"] = auth.ms_token
        params = _with_a_bogus(params)
        headers = HeaderBuilder.build(HeaderType.GET)
        headers.set_referer(referer)
        response = requests.get(
            "https://www.douyin.com/aweme/v1/web/comment/list/reply/",
            headers=headers.get(),
            cookies=auth.cookie,
            params=params,
            verify=False,
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        self._raise_for_status_code(payload, context="获取二级回复")
        return payload

    def get_all_comments(
        self,
        auth: ExperimentalDouyinAuth,
        video_url: str,
        *,
        max_comments: int = 100,
        include_replies: bool = False,
    ) -> Dict[str, Any]:
        normalized_url = build_video_url(video_url)
        comments: List[Dict[str, Any]] = []
        cursor = 0
        has_more = 1
        while has_more == 1 and len(comments) < max_comments:
            page = self.get_comments_page(auth, normalized_url, cursor=cursor, count=min(20, max_comments))
            page_comments = page.get("comments") or []
            if not isinstance(page_comments, list) or not page_comments:
                break
            for item in page_comments:
                if not isinstance(item, dict):
                    continue
                normalized = self._normalize_comment(item)
                if include_replies and int(item.get("reply_comment_total", 0) or 0) > 0:
                    normalized["reply_comment"] = self.get_all_replies(
                        auth,
                        aweme_id=normalized["aweme_id"],
                        comment_id=normalized["comment_id"],
                    )
                comments.append(normalized)
                if len(comments) >= max_comments:
                    break
            has_more = int(page.get("has_more", 0) or 0)
            cursor = int(page.get("cursor", 0) or 0)
        return {
            "video_url": normalized_url,
            "aweme_id": extract_aweme_id(normalized_url),
            "count": len(comments),
            "comments": comments,
        }

    def get_all_replies(
        self,
        auth: ExperimentalDouyinAuth,
        *,
        aweme_id: str,
        comment_id: str,
        max_replies: int = 50,
    ) -> List[Dict[str, Any]]:
        replies: List[Dict[str, Any]] = []
        cursor = 0
        has_more = 1
        while has_more == 1 and len(replies) < max_replies:
            page = self.get_comment_replies_page(
                auth,
                aweme_id=aweme_id,
                comment_id=comment_id,
                cursor=cursor,
                count=min(10, max_replies),
            )
            page_replies = page.get("comments") or []
            if not isinstance(page_replies, list) or not page_replies:
                break
            for item in page_replies:
                if not isinstance(item, dict):
                    continue
                replies.append(self._normalize_comment(item))
                if len(replies) >= max_replies:
                    break
            has_more = int(page.get("has_more", 0) or 0)
            cursor = int(page.get("cursor", 0) or 0)
        return replies

    def publish_comment(
        self,
        auth: ExperimentalDouyinAuth,
        video_url: str,
        content: str,
        *,
        reply_id: str = "",
    ) -> Dict[str, Any]:
        normalized_url = build_video_url(video_url)
        aweme_id = extract_aweme_id(normalized_url)
        text = str(content or "").strip()
        if not text:
            raise DouyinCommentApiExperimentError("评论内容不能为空")

        api = "/aweme/v1/web/comment/publish"
        referer = f"https://www.douyin.com/discover?modal_id={aweme_id}"
        params = {
            "app_name": "aweme",
            "enter_from": "discover",
            "previous_page": "discover",
        }
        params.update(_platform_params())
        params["webid"] = self._generate_webid(auth, referer)
        params["msToken"] = auth.ms_token
        params["verifyFp"] = auth.cookie["s_v_web_id"]
        params["fp"] = auth.cookie["s_v_web_id"]
        data: Dict[str, Any] = {
            "aweme_id": aweme_id,
            "comment_send_celltime": random.randint(1000, 20000),
            "comment_video_celltime": random.randint(1000, 20000),
            "text": text,
            "text_extra": [],
        }
        if str(reply_id or "").strip():
            data["reply_id"] = str(reply_id or "").strip()
        params = _with_a_bogus(params, data)
        headers = HeaderBuilder.build(HeaderType.FORM)
        headers.set_header("Origin", "https://www.douyin.com")
        headers.with_bd(api, auth)
        headers.with_csrf(auth.cookie_str)
        headers.set_referer(referer)
        response = requests.post(
            f"https://www.douyin.com{api}",
            headers=headers.get(),
            cookies=auth.cookie,
            params=params,
            data=data,
            verify=False,
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        self._raise_for_status_code(payload, context="发布评论")
        comment = payload.get("comment") if isinstance(payload.get("comment"), dict) else {}
        cid = str(comment.get("cid", "") or payload.get("cid", "") or "").strip()
        if not cid:
            raise DouyinCommentApiExperimentError(
                f"发布评论接口未返回评论 ID，raw={json.dumps(payload, ensure_ascii=False)[:1000]}"
            )
        return {
            "aweme_id": aweme_id,
            "comment_id": cid,
            "text": text,
            "raw": payload,
        }

    def _generate_webid(self, auth: ExperimentalDouyinAuth, referer: str) -> str:
        from douyin_im_api_experiment import generate_webid

        return generate_webid(auth, referer)

    def _raise_for_status_code(self, payload: Dict[str, Any], *, context: str) -> None:
        status_code = int(payload.get("status_code", 0) or 0)
        if status_code == 0:
            return
        status_msg = str(payload.get("status_msg", "") or "").strip()
        raise DouyinCommentApiExperimentError(
            f"{context}失败：status_code={status_code}，status_msg={status_msg or '-'}"
        )

    def _normalize_comment(self, comment: Dict[str, Any]) -> Dict[str, Any]:
        user = comment.get("user") or {}
        text = str(comment.get("text", "") or "").strip()
        cid = str(comment.get("cid", "") or "").strip()
        aweme_id = str(comment.get("aweme_id", "") or "").strip()
        sec_uid = str(user.get("sec_uid", "") or "").strip()
        profile_url = f"https://www.douyin.com/user/{sec_uid}" if sec_uid else ""
        return {
            "comment_id": cid,
            "aweme_id": aweme_id,
            "text": text,
            "create_time": int(comment.get("create_time", 0) or 0),
            "digg_count": int(comment.get("digg_count", 0) or 0),
            "reply_comment_total": int(comment.get("reply_comment_total", 0) or 0),
            "nickname": str(user.get("nickname", "") or "").strip(),
            "uid": str(user.get("uid", "") or "").strip(),
            "sec_uid": sec_uid,
            "profile_url": profile_url,
            "raw": comment,
        }

    def _normalize_author(self, author: Dict[str, Any], *, fallback_sec_user_id: str = "") -> Dict[str, Any]:
        sec_user_id = str(author.get("sec_uid", "") or fallback_sec_user_id or "").strip()
        return {
            "username": str(author.get("nickname", "") or "").strip(),
            "douyin_id": str(author.get("unique_id", "") or author.get("short_id", "") or "").strip(),
            "sec_user_id": sec_user_id,
            "profile_url": build_profile_url(sec_user_id) if sec_user_id else "",
            "avatar_url": _pick_image_url(author),
            "signature": str(author.get("signature", "") or "").strip(),
            "bio": str(author.get("signature", "") or "").strip(),
            "fans_count_text": _format_compact_count(int(author.get("follower_count", 0) or 0)),
            "follow_count_text": _format_compact_count(int(author.get("following_count", 0) or 0)),
            "liked_count_text": _format_compact_count(int(author.get("total_favorited", 0) or 0)),
        }

    def _normalize_aweme(self, aweme: Dict[str, Any], *, order_index: int = 1) -> Dict[str, Any]:
        aweme_id = str(aweme.get("aweme_id", "") or "").strip()
        author = aweme.get("author") if isinstance(aweme.get("author"), dict) else {}
        statistics = aweme.get("statistics") if isinstance(aweme.get("statistics"), dict) else {}
        video = aweme.get("video") if isinstance(aweme.get("video"), dict) else {}
        create_time = int(aweme.get("create_time", 0) or 0)
        digg_count = int(statistics.get("digg_count", 0) or 0)
        comment_count = int(statistics.get("comment_count", 0) or 0)
        play_count = int(statistics.get("play_count", 0) or 0)
        return {
            "platform": "douyin",
            "aweme_id": aweme_id,
            "url": build_video_url(aweme_id) if aweme_id else "",
            "title": str(aweme.get("desc", "") or "").strip() or f"视频 {order_index}",
            "author": str(author.get("nickname", "") or "").strip(),
            "cover_image": _pick_image_url(video),
            "likes": digg_count,
            "likes_text": _format_compact_count(digg_count),
            "comments": comment_count,
            "comments_text": _format_compact_count(comment_count),
            "play_count": play_count,
            "play_text": _format_compact_count(play_count),
            "publish_time": _format_unix_timestamp_text(create_time),
            "publish_time_text": _format_unix_timestamp_text(create_time),
            "publish_timestamp": create_time,
            "video_order": int(order_index or 1),
            "raw": aweme,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Douyin comment API experiment")
    parser.add_argument("--account-id", type=int, default=1, help="抖音账号编号")
    subparsers = parser.add_subparsers(dest="command", required=True)

    auth_parser = subparsers.add_parser("auth", help="提取评论接口所需认证信息")

    info_parser = subparsers.add_parser("work-info", help="获取作品详情")
    info_parser.add_argument("--video-url", required=True, help="作品地址或 aweme_id")

    comments_parser = subparsers.add_parser("comments", help="获取作品评论")
    comments_parser.add_argument("--video-url", required=True, help="作品地址或 aweme_id")
    comments_parser.add_argument("--max-comments", type=int, default=20, help="最多拉取多少条一级评论")
    comments_parser.add_argument("--include-replies", action="store_true", help="顺带拉取二级回复")

    args = parser.parse_args()
    client = DouyinCommentApiExperiment(account_id=args.account_id)
    auth = asyncio.run(client.extract_auth()) if args.command in {"auth", "work-info", "comments"} else None

    if args.command == "auth":
        print(
            json.dumps(
                {
                    "account_id": args.account_id,
                    "cookie_names": sorted(auth.cookie.keys()),
                    "s_v_web_id": auth.cookie.get("s_v_web_id", ""),
                    "msToken": auth.ms_token[:6] + "***" if auth.ms_token else "",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if args.command == "work-info":
        payload = client.get_work_info(auth, args.video_url)
        aweme_detail = payload.get("aweme_detail") or {}
        print(
            json.dumps(
                {
                    "aweme_id": str(aweme_detail.get("aweme_id", "") or ""),
                    "desc": str(aweme_detail.get("desc", "") or ""),
                    "author_nickname": str(((aweme_detail.get("author") or {}).get("nickname", "")) or ""),
                    "comment_count": int(aweme_detail.get("statistics", {}).get("comment_count", 0) or 0),
                    "raw": payload,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if args.command == "comments":
        payload = client.get_all_comments(
            auth,
            args.video_url,
            max_comments=args.max_comments,
            include_replies=bool(args.include_replies),
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
