from __future__ import annotations

import argparse
import asyncio
import base64
import importlib.util
import json
import random
import sys
import time
import uuid
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import requests
from google.protobuf.json_format import MessageToDict
from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from douyin_client import DouyinClient, is_port_open


warnings.filterwarnings("ignore", message="Protobuf gencode version .*")
requests.packages.urllib3.disable_warnings()

from douyin_protocol_runtime import (  # noqa: E402
    HeaderBuilder,
    HeaderType,
    STATIC_DIR,
    generate_a_bogus,
    generate_msToken,
    generate_req_sign,
    generate_webid,
    splice_url,
)


def _load_module(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RequestProto = _load_module("douyin_im_request_pb2", STATIC_DIR / "Request_pb2.py")
ResponseProto = _load_module("douyin_im_response_pb2", STATIC_DIR / "Response_pb2.py")


class DouyinImApiError(RuntimeError):
    """Base error for the Douyin IM experiment runtime."""


class DouyinImApiUnavailableError(DouyinImApiError):
    """Raised when the protocol runtime is unavailable and callers may fallback."""


class DouyinImApiProfileError(DouyinImApiError):
    """Raised when the target user/profile is invalid and should fail directly."""


def _mask_secret(value: str, keep: int = 6) -> str:
    text = str(value or "").strip()
    if len(text) <= keep:
        return text
    return f"{text[:keep]}***"


def _decode_storage_payload(raw: Any) -> Dict[str, Any]:
    if raw is None:
        return {}
    text = raw.decode("utf-8", errors="ignore") if isinstance(raw, bytes) else str(raw)
    text = text.strip()
    if not text:
        return {}
    try:
        outer = json.loads(text)
    except Exception:
        return {}
    if isinstance(outer, dict) and isinstance(outer.get("data"), str):
        try:
            inner = json.loads(outer["data"])
        except Exception:
            return {}
        return inner if isinstance(inner, dict) else {}
    return outer if isinstance(outer, dict) else {}


def _platform_params() -> Dict[str, str]:
    return {
        "device_platform": "webapp",
        "aid": "6383",
        "channel": "channel_pc_web",
        "pc_client_type": "1",
        "update_version_code": "170400",
        "version_code": "170400",
        "version_name": "17.4.0",
        "cookie_enabled": "true",
        "screen_width": "1707",
        "screen_height": "960",
        "browser_language": "zh-CN",
        "browser_platform": "Win32",
        "browser_name": "Edge",
        "browser_version": "125.0.0.0",
        "browser_online": "true",
        "engine_name": "Blink",
        "engine_version": "125.0.0.0",
        "os_name": "Windows",
        "os_version": "10",
        "cpu_core_num": "32",
        "device_memory": "8",
        "platform": "PC",
        "downlink": "10",
        "effective_type": "4g",
        "round_trip_time": "100",
    }


def _with_a_bogus(params: Dict[str, Any], data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    params = dict(params)
    query = splice_url(params)
    data_text = splice_url(data or {}) if data else ""
    params["a_bogus"] = generate_a_bogus(query, data_text)
    return params


def _protobuf_to_dict(message: Any) -> Dict[str, Any]:
    return MessageToDict(message, preserving_proto_field_name=True)


def _extract_send_message_ack(payload: Dict[str, Any]) -> Dict[str, Any]:
    body = payload.get("body") if isinstance(payload.get("body"), dict) else {}
    notify = body.get("new_message_notify") if isinstance(body.get("new_message_notify"), dict) else {}
    message_body = notify.get("message") if isinstance(notify.get("message"), dict) else {}
    server_message_id = str(message_body.get("server_message_id", "") or "").strip()
    return {
        "server_message_id": server_message_id,
        "index_in_conversation": message_body.get("index_in_conversation"),
        "message_type": message_body.get("message_type"),
        "sender": message_body.get("sender"),
        "content": message_body.get("content"),
    }


def _extract_sec_user_id(profile_url: str) -> str:
    text = str(profile_url or "").strip()
    if not text:
        raise DouyinImApiProfileError("缺少用户主页地址")
    if "/user/" not in text:
        raise DouyinImApiProfileError("用户主页链接无效，无法发送私信")
    return text.split("/user/", 1)[1].split("?", 1)[0].split("/", 1)[0].strip()


@dataclass
class ExperimentalDouyinAuth:
    cookie: Dict[str, str]
    cookie_str: str
    ms_token: str
    private_key: str
    ticket: str
    ts_sign: str
    client_cert: str
    web_protect_raw: str
    crypt_sdk_raw: str
    my_uid: Optional[int] = None

    def ensure_my_uid(self, client: "DouyinImApiExperiment") -> int:
        if self.my_uid is None:
            self.my_uid = client.get_my_uid(self)
        return int(self.my_uid)


class DouyinImApiExperiment:
    def __init__(self, account_id: int, cdp_port: Optional[int] = None):
        self.account_id = int(account_id)
        self.cdp_port = int(cdp_port or (9331 + int(account_id)))

    async def extract_auth(self) -> ExperimentalDouyinAuth:
        async with async_playwright() as playwright:
            browser: Optional[Browser] = None
            context: Optional[BrowserContext] = None
            owns_context = False
            profile_dir = DouyinClient(self.cdp_port, account_id=self.account_id).resolve_profile_dir()
            try:
                client = DouyinClient(self.cdp_port, account_id=self.account_id)
                if not is_port_open(self.cdp_port):
                    client.launch_browser(start_url="https://www.douyin.com/")

                if is_port_open(self.cdp_port):
                    browser = await playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{self.cdp_port}")
                    context = await self._get_context(browser)
                else:
                    context = await playwright.chromium.launch_persistent_context(
                        user_data_dir=profile_dir,
                        headless=False,
                        channel="chrome",
                        locale="zh-CN",
                        viewport={"width": 1440, "height": 960},
                        args=[
                            "--disable-blink-features=AutomationControlled",
                            "--disable-dev-shm-usage",
                            "--no-sandbox",
                        ],
                    )
                    owns_context = True
                page = await self._get_page(context)
                cookies, crypt_sdk_raw, web_protect_raw, missing = await self._extract_auth_material_from_page(
                    page,
                    context,
                )
            finally:
                if owns_context and context is not None:
                    await context.close()

        cookie_map = {str(item.get("name", "")).strip(): str(item.get("value", "")).strip() for item in cookies}
        cookie_map = {key: value for key, value in cookie_map.items() if key}
        ms_token = cookie_map.get("msToken") or generate_msToken()
        cookie_map["msToken"] = ms_token
        cookie_str = "; ".join(f"{key}={value}" for key, value in cookie_map.items())

        crypt_sdk = _decode_storage_payload(crypt_sdk_raw)
        web_protect = _decode_storage_payload(web_protect_raw)
        private_key = str(crypt_sdk.get("ec_privateKey", "") or "").strip()
        ticket = str(web_protect.get("ticket", "") or "").strip()
        ts_sign = str(web_protect.get("ts_sign", "") or "").strip()
        client_cert = str(web_protect.get("client_cert", "") or "").strip()
        missing = [
            name
            for name, value in (
                ("ec_privateKey", private_key),
                ("ticket", ticket),
                ("ts_sign", ts_sign),
                ("client_cert", client_cert),
                ("s_v_web_id", cookie_map.get("s_v_web_id", "")),
            )
            if not str(value or "").strip()
        ]
        if missing:
            raise DouyinImApiUnavailableError(f"douyin im auth material incomplete: {', '.join(missing)}")

        return ExperimentalDouyinAuth(
            cookie=cookie_map,
            cookie_str=cookie_str,
            ms_token=ms_token,
            private_key=private_key,
            ticket=ticket,
            ts_sign=ts_sign,
            client_cert=client_cert,
            web_protect_raw=str(web_protect_raw or ""),
            crypt_sdk_raw=str(crypt_sdk_raw or ""),
        )

    async def _extract_auth_material_from_page(
        self,
        page: Page,
        context: BrowserContext,
    ) -> tuple[list[dict], str, str, list[str]]:
        last_cookies: list[dict] = []
        last_crypt_sdk_raw = ""
        last_web_protect_raw = ""
        last_missing: list[str] = []
        urls = [
            "https://www.douyin.com/",
            "https://www.douyin.com/user/self?from_tab_name=main",
            "https://www.douyin.com/",
        ]
        for index, url in enumerate(urls, start=1):
            try:
                await page.bring_to_front()
            except Exception:
                pass
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            except Exception:
                if index == 1:
                    raise
            try:
                await page.wait_for_timeout(2500 if index == 1 else 3500)
            except Exception:
                await asyncio.sleep(2.5 if index == 1 else 3.5)

            cookies, crypt_sdk_raw, web_protect_raw = await self._read_auth_material_once(page, context)
            missing = self._missing_auth_material(cookies, crypt_sdk_raw, web_protect_raw)
            last_cookies = cookies
            last_crypt_sdk_raw = crypt_sdk_raw
            last_web_protect_raw = web_protect_raw
            last_missing = missing
            if not missing:
                return cookies, crypt_sdk_raw, web_protect_raw, missing

            try:
                await page.reload(wait_until="domcontentloaded", timeout=45000)
                await page.wait_for_timeout(3000)
                cookies, crypt_sdk_raw, web_protect_raw = await self._read_auth_material_once(page, context)
                missing = self._missing_auth_material(cookies, crypt_sdk_raw, web_protect_raw)
                last_cookies = cookies
                last_crypt_sdk_raw = crypt_sdk_raw
                last_web_protect_raw = web_protect_raw
                last_missing = missing
                if not missing:
                    return cookies, crypt_sdk_raw, web_protect_raw, missing
            except Exception:
                pass

        return last_cookies, last_crypt_sdk_raw, last_web_protect_raw, last_missing

    async def _read_auth_material_once(
        self,
        page: Page,
        context: BrowserContext,
    ) -> tuple[list[dict], str, str]:
        cookies = await context.cookies("https://www.douyin.com")
        crypt_sdk_raw = ""
        web_protect_raw = ""
        candidate_pages = [page]
        for item in context.pages:
            if item is page:
                continue
            try:
                if "douyin.com" in str(item.url or ""):
                    candidate_pages.append(item)
            except Exception:
                continue

        for candidate in candidate_pages:
            if crypt_sdk_raw and web_protect_raw:
                break
            try:
                if not crypt_sdk_raw:
                    crypt_sdk_raw = await candidate.evaluate(
                        '() => localStorage.getItem("security-sdk/s_sdk_crypt_sdk")'
                    )
            except Exception:
                pass
            try:
                if not web_protect_raw:
                    web_protect_raw = await candidate.evaluate(
                        '() => localStorage.getItem("security-sdk/s_sdk_sign_data_key/web_protect")'
                    )
            except Exception:
                pass
        return cookies, str(crypt_sdk_raw or ""), str(web_protect_raw or "")

    def _missing_auth_material(
        self,
        cookies: list[dict],
        crypt_sdk_raw: str,
        web_protect_raw: str,
    ) -> list[str]:
        cookie_map = {str(item.get("name", "")).strip(): str(item.get("value", "")).strip() for item in cookies}
        cookie_map = {key: value for key, value in cookie_map.items() if key}
        crypt_sdk = _decode_storage_payload(crypt_sdk_raw)
        web_protect = _decode_storage_payload(web_protect_raw)
        checks = (
            ("ec_privateKey", str(crypt_sdk.get("ec_privateKey", "") or "").strip()),
            ("ticket", str(web_protect.get("ticket", "") or "").strip()),
            ("ts_sign", str(web_protect.get("ts_sign", "") or "").strip()),
            ("client_cert", str(web_protect.get("client_cert", "") or "").strip()),
            ("s_v_web_id", cookie_map.get("s_v_web_id", "")),
        )
        return [name for name, value in checks if not str(value or "").strip()]

    async def _get_context(self, browser: Browser) -> BrowserContext:
        if browser.contexts:
            return browser.contexts[0]
        return await browser.new_context(locale="zh-CN", viewport={"width": 1440, "height": 960})

    async def _get_page(self, context: BrowserContext) -> Page:
        for page in context.pages:
            try:
                if "douyin.com" in str(page.url or ""):
                    return page
            except Exception:
                continue
        return await context.new_page()

    def get_my_uid(self, auth: ExperimentalDouyinAuth) -> int:
        url = "https://www.douyin.com/aweme/v1/web/query/user/"
        referer = "https://www.douyin.com/"
        params = _platform_params()
        params["webid"] = generate_webid(auth, referer)
        params["msToken"] = auth.ms_token
        params["verifyFp"] = auth.cookie["s_v_web_id"]
        params["fp"] = auth.cookie["s_v_web_id"]
        params = _with_a_bogus(params)
        headers = HeaderBuilder.build(HeaderType.GET)
        headers.set_referer(referer)
        response = requests.get(url, headers=headers.get(), cookies=auth.cookie, params=params, verify=False, timeout=20)
        response.raise_for_status()
        payload = response.json()
        if str(payload.get("status_code", 0) or 0) not in {"0", "None"} and int(payload.get("status_code", 0) or 0) != 0:
            raise DouyinImApiUnavailableError(
                f"query current user failed: {json.dumps(payload, ensure_ascii=False)}"
            )
        return int(payload["user_uid"])

    def get_user_info(self, auth: ExperimentalDouyinAuth, profile_url: str) -> Dict[str, Any]:
        sec_user_id = _extract_sec_user_id(profile_url)
        api = "https://www.douyin.com/aweme/v1/web/user/profile/other/"
        params = _platform_params()
        params.update(
            {
                "publish_video_strategy_type": "2",
                "source": "channel_pc_web",
                "sec_user_id": sec_user_id,
                "personal_center_strategy": "1",
            }
        )
        params["webid"] = generate_webid(auth, profile_url)
        params["msToken"] = auth.ms_token
        params["verifyFp"] = auth.cookie["s_v_web_id"]
        params["fp"] = auth.cookie["s_v_web_id"]
        params = _with_a_bogus(params)
        headers = HeaderBuilder.build(HeaderType.GET)
        headers.set_referer(profile_url)
        response = requests.get(api, headers=headers.get(), cookies=auth.cookie, params=params, verify=False, timeout=20)
        response.raise_for_status()
        payload = response.json()
        status_code = int(payload.get("status_code", 0) or 0)
        user = payload.get("user") or {}
        if status_code != 0:
            status_msg = str(payload.get("status_msg", "") or "").strip()
            if "不存在" in status_msg or "user not found" in status_msg.lower():
                raise DouyinImApiProfileError("用户不存在：该抖音主页已失效或已被删除，无法发送私信")
            raise DouyinImApiUnavailableError(
                f"query target user failed: {json.dumps(payload, ensure_ascii=False)}"
            )
        if not isinstance(user, dict) or not str(user.get("uid", "") or "").strip():
            raise DouyinImApiProfileError("用户不存在：该抖音主页已失效或已被删除，无法发送私信")
        return payload

    def create_conversation(self, auth: ExperimentalDouyinAuth, to_user_id: int) -> Dict[str, Any]:
        request = self._build_normal_request(auth, 609)
        request.body.create_conversation_v2_body.conversation_type = 1
        request.body.create_conversation_v2_body.participants.extend([int(to_user_id), auth.ensure_my_uid(self)])
        request.reuqest_sign = generate_req_sign(
            {
                "sign_data": f"avatar_url=&idempotent_id=&name=&participants={int(to_user_id)},{auth.ensure_my_uid(self)}",
                "certType": "cookie",
                "scene": "web_protect",
            },
            auth.private_key,
        )
        headers = HeaderBuilder.build(HeaderType.PROTOBUF)
        headers.set_referer("https://www.douyin.com/")
        response = requests.post(
            "https://imapi.douyin.com/v2/conversation/create",
            headers=headers.get(),
            cookies=auth.cookie,
            data=request.SerializeToString(),
            verify=False,
            timeout=20,
        )
        response.raise_for_status()
        payload = self._parse_response_proto(response.content)
        conversations = (
            payload.get("body", {})
            .get("create_conversation_v2_body", {})
            .get("conversation_info_list", [])
        )
        if not conversations:
            raise DouyinImApiUnavailableError(f"create conversation failed: {json.dumps(payload, ensure_ascii=False)}")
        conversation = conversations[0]
        return {
            "conversation_id": conversation.get("conversation_id", ""),
            "conversation_short_id": conversation.get("conversation_short_id", ""),
            "ticket": conversation.get("ticket", ""),
            "raw": payload,
        }

    def send_text_message(
        self,
        auth: ExperimentalDouyinAuth,
        conversation_id: str,
        conversation_short_id: str,
        ticket: str,
        message: str,
    ) -> Dict[str, Any]:
        request = self._build_normal_request(auth, 100)
        client_message_id = str(uuid.uuid4())
        conversation_short_id_int = int(str(conversation_short_id or "0").strip() or "0")
        msg_content = {
            "mention_users": [],
            "aweType": 700,
            "richTextInfos": [],
            "text": str(message or ""),
        }
        request.body.send_message_body.conversation_id = str(conversation_id)
        request.body.send_message_body.conversation_type = 1
        request.body.send_message_body.conversation_short_id = conversation_short_id_int
        request.body.send_message_body.content = json.dumps(msg_content, ensure_ascii=False, separators=(",", ":"))
        request.body.send_message_body.ext.append(
            RequestProto.ExtValue(key="s:client_message_id", value=client_message_id)
        )
        request.body.send_message_body.ext.append(
            RequestProto.ExtValue(key="s:stime", value=str(int(time.time() * 1000)))
        )
        request.body.send_message_body.ext.append(
            RequestProto.ExtValue(key="s:mentioned_users", value="")
        )
        request.body.send_message_body.message_type = 7
        request.body.send_message_body.ticket = str(ticket)
        request.body.send_message_body.client_message_id = client_message_id
        request.reuqest_sign = generate_req_sign(
            {
                "sign_data": (
                    f"content={json.dumps(msg_content, ensure_ascii=False, separators=(',', ':'))}"
                    f"&conversation_id={conversation_id}&conversation_short_id={conversation_short_id}"
                ),
                "certType": "cookie",
                "scene": "web_protect",
            },
            auth.private_key,
        )
        params = {
            "verifyFp": auth.cookie["s_v_web_id"],
            "fp": auth.cookie["s_v_web_id"],
            "msToken": generate_msToken(),
        }
        params = _with_a_bogus(params)
        headers = HeaderBuilder.build(HeaderType.PROTOBUF)
        headers.set_referer("https://www.douyin.com/")
        response = requests.post(
            "https://imapi.douyin.com/v1/message/send",
            params=params,
            headers=headers.get(),
            cookies=auth.cookie,
            data=request.SerializeToString(),
            verify=False,
            timeout=20,
        )
        response.raise_for_status()
        payload = self._parse_response_proto(response.content)
        response_message = str(payload.get("message", "") or "").strip()
        if response_message and response_message.upper() != "OK":
            raise DouyinImApiUnavailableError(f"send im message failed: {json.dumps(payload, ensure_ascii=False)}")
        ack = _extract_send_message_ack(payload)
        server_message_id = str(ack.get("server_message_id", "") or "").strip()
        if not server_message_id:
            raise DouyinImApiUnavailableError(
                "send im message returned OK but no server_message_id; "
                f"raw={json.dumps(payload, ensure_ascii=False)[:2000]}"
            )
        return {
            "raw": payload,
            "server_message_id": server_message_id,
            "ack": ack,
            "client_message_id": client_message_id,
        }

    def send_private_message(self, auth: ExperimentalDouyinAuth, profile_url: str, message: str) -> Dict[str, Any]:
        user_info = self.get_user_info(auth, profile_url)
        user = user_info.get("user") or {}
        to_user_id = int(user.get("uid") or 0)
        if not to_user_id:
            raise DouyinImApiProfileError("用户不存在：该抖音主页已失效或已被删除，无法发送私信")
        conversation = self.create_conversation(auth, to_user_id)
        send_result = self.send_text_message(
            auth,
            conversation["conversation_id"],
            conversation["conversation_short_id"],
            conversation["ticket"],
            message,
        )
        return {
            "target_uid": to_user_id,
            "conversation_id": conversation["conversation_id"],
            "conversation_short_id": conversation["conversation_short_id"],
            "ticket": conversation["ticket"],
            "send_result": send_result,
        }

    def _build_normal_request(self, auth: ExperimentalDouyinAuth, cmd: int):
        request = RequestProto.Request()
        request.cmd = int(cmd)
        request.sequence_id = random.randint(10000, 11000)
        request.sdk_version = "1.1.3"
        request.token = auth.ticket
        request.refer = 3
        request.inbox_type = 0
        request.build_number = "5fa6ff1:Detached: 5fa6ff1111fd53aafc4c753505d3c93daad74d27"
        request.device_id = "0"
        request.device_platform = "douyin_pc"
        request.headers["session_aid"] = "6383"
        request.headers["session_did"] = "0"
        request.headers["app_name"] = "douyin_pc"
        request.headers["priority_region"] = "cn"
        request.headers["user_agent"] = HeaderBuilder.ua
        request.headers["cookie_enabled"] = "true"
        request.headers["browser_language"] = "zh-CN"
        request.headers["browser_platform"] = "Win32"
        request.headers["browser_name"] = "Mozilla"
        request.headers["browser_version"] = HeaderBuilder.ua.split("Mozilla/")[-1]
        request.headers["browser_online"] = "true"
        request.headers["screen_width"] = "1707"
        request.headers["screen_height"] = "960"
        request.headers["referer"] = ""
        request.headers["timezone_name"] = "Etc/GMT-8"
        request.headers["deviceId"] = "0"
        request.headers["webid"] = generate_webid(auth, "https://www.douyin.com/")
        request.headers["fp"] = auth.cookie["s_v_web_id"]
        request.headers["is-retry"] = "0"
        request.auth_type = 4
        request.biz = "douyin_web"
        request.access = "web_sdk"
        request.ts_sign = auth.ts_sign
        request.sdk_cert = base64.b64encode(auth.client_cert.encode("utf-8")).decode("utf-8")
        return request

    def _parse_response_proto(self, content: bytes) -> Dict[str, Any]:
        response = ResponseProto.Response()
        response.ParseFromString(content)
        return _protobuf_to_dict(response)


async def _async_main() -> int:
    parser = argparse.ArgumentParser(description="Douyin IM API experiment using existing logged-in browser profile.")
    parser.add_argument("--account-id", type=int, default=1, help="douyin account id, default 1")
    parser.add_argument("--port", type=int, default=0, help="cdp port override, default 9331 + account_id")

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("extract", help="extract current cookies + web_protect + crypt_sdk from browser")

    lookup_parser = subparsers.add_parser("lookup-user", help="resolve target uid from profile url")
    lookup_parser.add_argument("--profile-url", required=True, help="douyin profile url")

    send_parser = subparsers.add_parser("send", help="send a text private message by IM API")
    send_parser.add_argument("--profile-url", required=True, help="douyin profile url")
    send_parser.add_argument("--message", required=True, help="text content to send")

    args = parser.parse_args()
    client = DouyinImApiExperiment(account_id=args.account_id, cdp_port=(args.port or None))
    auth = await client.extract_auth()

    if args.command == "extract":
        payload = {
            "account_id": args.account_id,
            "port": client.cdp_port,
            "cookie_names": sorted(auth.cookie.keys()),
            "s_v_web_id": auth.cookie.get("s_v_web_id", ""),
            "msToken": _mask_secret(auth.ms_token),
            "ticket": _mask_secret(auth.ticket),
            "ts_sign": _mask_secret(auth.ts_sign),
            "client_cert": _mask_secret(auth.client_cert),
            "ec_privateKey": _mask_secret(auth.private_key),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if args.command == "lookup-user":
        payload = client.get_user_info(auth, args.profile_url)
        user = payload.get("user") or {}
        print(
            json.dumps(
                {
                    "uid": user.get("uid", ""),
                    "nickname": user.get("nickname", ""),
                    "sec_uid": user.get("sec_uid", ""),
                    "raw": payload,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if args.command == "send":
        payload = client.send_private_message(auth, args.profile_url, args.message)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    raise RuntimeError(f"unsupported command: {args.command}")


def main() -> int:
    return asyncio.run(_async_main())


if __name__ == "__main__":
    raise SystemExit(main())
