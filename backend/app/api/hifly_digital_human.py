from __future__ import annotations

import mimetypes
import json
import logging
import math
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from ..core.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

_HIFLY_API_BASE = "https://hfw-api.hifly.cc"
_MAX_AVATAR_PAGE_SIZE = 100
_MAX_VOICE_PAGE_SIZE = 300
_IMAGE_MAX_BYTES = 10 * 1024 * 1024
_VIDEO_MAX_BYTES = 500 * 1024 * 1024
_AUDIO_MAX_BYTES = 20 * 1024 * 1024

_IMAGE_EXTS = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}
_VIDEO_EXTS = {"mp4": "video/mp4", "mov": "video/quicktime"}
_AUDIO_EXTS = {"mp3": "audio/mpeg", "m4a": "audio/mp4", "wav": "audio/wav"}
_HIFLY_TTS_CAPABILITY_ID = "hifly.video.create_by_tts"
_HIFLY_TTS_UNIT_CREDITS = 10
_HIFLY_TTS_CHARS_PER_SECOND = 4
_ROOT_DIR = Path(__file__).resolve().parents[3]
_STATIC_DIR = _ROOT_DIR / "static"
_HIFLY_PREVIEWS_DIR = _STATIC_DIR / "hifly_previews"
_HIFLY_PREVIEWS_MANIFEST_PATH = _HIFLY_PREVIEWS_DIR / "manifest.json"
_HIFLY_PUBLIC_AVATARS_PATH = _ROOT_DIR / "hifly_public_avatars.json"
_HIFLY_PUBLIC_AVATAR_CACHE_PATH = _ROOT_DIR / "data" / "hifly_public_avatars_cache.json"
_HIFLY_BILLING_STATE_PATH = _ROOT_DIR / "data" / "hifly_billing_state.json"
_LOBSTER_SERVER_PUBLIC = "https://bhzn.top"

_GENERIC_FEMALE_COVER = "https://hfcdn.lingverse.co/c8fb4357c18dcbe55bb646a284ab43fe/69FF59FF/hf/input/6/videos/hansining-035/hansining-cover.jpg"
_GENERIC_MALE_COVER = "https://hfcdn.lingverse.co/a9303a866274b89806760ecedbc10a2a/69FF59FF/hf/input/6/videos/zhoujingxing-035/zhoujingxing-cover.jpg"
_GENERIC_LIFESTYLE_COVER = "https://hfcdn.lingverse.co/e8f529b038b153b1f5d0476d20744c4e/69FF59FF/hf/input/6/videos/ba3ed27f-0af9-4f22-8db6-b8c9ba90b819.mp4.face.png"
_GENERIC_ELDER_COVER = "https://hfcdn.lingverse.co/ec105461d442b7497845403aec41c8a8/69FF59FF/hf/input/6/videos/dcace540-45ec-4bf6-ba9e-c97d7f62a748.mp4.face.png"
_GENERIC_CHILD_COVER = "https://hfcdn.lingverse.co/c94a5c47961e788460da1c5acb6105fa/69FF59FF/hf/input/6/videos/d54bc230-cb9a-40b7-81cd-ed55d9519940.mp4.face.png"

# Explicit public cover overrides are used when HiFly's simplified avatar/list
# payload omits a usable cover_url. These links are manually verified and
# should win over our older guessed placeholder covers.
_PUBLIC_AVATAR_COVER_OVERRIDES_BY_AVATAR: Dict[str, str] = {
    "8g8VzZ-dU9xUQywBAZEASw": "https://hfcdn.lingverse.co/6aa1a564d0a0afe9fd0a15de5f7806fb/6A01FCFF/hf/input/6/videos/hansining-035/hansining-cover.jpg",
    "X1WNAsrIT9LhinslA0DNYA": "https://hfcdn.lingverse.co/6aa1a564d0a0afe9fd0a15de5f7806fb/6A01FCFF/hf/input/6/videos/hansining-035/hansining-cover.jpg",
}

# 公共数字人封面回填表（标题统一按 lower() 后的字符串匹配）。
# HiFly 开放 API 的 avatar/list 简化响应不返回 cover_url，这里依据 lingverse 站
# 上抓到的真实封面 URL 进行兜底。后续若 HiFly 接口能返回 cover_url，会优先采用接口值。
_PUBLIC_AVATAR_COVER_OVERRIDES_BY_TITLE: Dict[str, str] = {
    "菲莹": "https://hfcdn.lingverse.co/d6b12308198a23fc313d11da056373a6/6A01FCFF/hf/local/68600/videos/0a2f5f1f-8493-44ef-8726-ea1dd9233067.mp4.face.png",
    "杜苇然": "https://hfcdn.lingverse.co/f8222ab80cd2fe23640e040aba11b265/6A01FCFF/hf/input/6/videos/duweiran-035/duweiran-cover.jpg",
    "韩思宁": "https://hfcdn.lingverse.co/6aa1a564d0a0afe9fd0a15de5f7806fb/6A01FCFF/hf/input/6/videos/hansining-035/hansining-cover.jpg",
    "周景行": "https://hfcdn.lingverse.co/d91e8bc5f3da484ad37f4541d11af5ef/6A01FCFF/hf/input/6/videos/zhoujingxing-035/zhoujingxing-cover.jpg",
    "徐皓然": "https://hfcdn.lingverse.co/206389b7ecfb7dedb9f675a3015b0e12/6A01FCFF/hf/input/6/videos/xuhaoran-035/xuhaoran-cover.jpg",
    "第一视角-探店博主萌萌": "https://hfcdn.lingverse.co/94a6d039b156c8f085013371520f09de/6A01FCFF/hf/input/6/videos/ba3ed27f-0af9-4f22-8db6-b8c9ba90b819.mp4.face.png",
    "章奶奶拜年": "https://hfcdn.lingverse.co/0fdb8da0a7dc7ce80d034e778d4b9ded/6A01FCFF/hf/input/6/videos/dcace540-45ec-4bf6-ba9e-c97d7f62a748.mp4.face.png",
    "小柳拜年": "https://hfcdn.lingverse.co/e29073c1c8d89a3d5aef60c2aaab742c/6A01FCFF/hf/input/6/videos/0b322cec-2c51-4645-85ac-1ba169a61e42.mp4.face.png",
    "其乐融融来拜年": "https://hfcdn.lingverse.co/d8d6558666da0546d3f944a2cb748324/6A01FCFF/hf/input/6/videos/e3d6203f-bd91-41d6-a248-a515219d13f6.mp4.face.png",
    "萌娃煊赫拜年": "https://hfcdn.lingverse.co/ce3cf91292de830820508a9fd6cf0fd7/6A01FCFF/hf/input/6/videos/d54bc230-cb9a-40b7-81cd-ed55d9519940.mp4.face.png",
    "萌娃晓柳拜年": "https://hfcdn.lingverse.co/f7b6165cced4256f3561f1b966f7679d/6A01FCFF/hf/input/6/videos/6252a68f-bb41-43c5-87db-7deb9bc82af0.mp4.face.png",
    "萌娃青青拜年": "https://hfcdn.lingverse.co/a8bb2bea2d1db4426e399f39d3244506/6A01FCFF/hf/input/6/videos/48a9bbc0-35a0-4cd5-ae46-b97ee61d05e3.mp4.face.png",
    "萌娃晓荷拜年": "https://hfcdn.lingverse.co/320c85d783031669fbc14aad6a763c2c/6A01FCFF/hf/input/6/videos/95233076-82ab-4b37-9662-cfe07be01757.mp4.face.png",
    "刘淼淼拜年": "https://hfcdn.lingverse.co/881d54527535dc33eb37d59f0f94849f/6A01FCFF/hf/input/6/videos/7344d157-33c0-4c82-b817-4492da0a9dc3.mp4.face.png",
    "拜年-老板": "https://hfcdn.lingverse.co/4451ed70d041b87836f3d0583c1359a9/6A01FCFF/hf/input/6/videos/845dfb4a-a51a-4784-8052-5d45843d4e38.mp4.face.png",
    "寿星": "https://hfcdn.lingverse.co/2ac14769f08178ce6fb31703a02d53df/6A01FCFF/hf/local/6/images/19e88134-aa2e-4ba1-8baf-416361e632f6.png.face.png",
    "灶神": "https://hfcdn.lingverse.co/49795efa13eaa0a87e2a4e79bfa528fb/6A01FCFF/hf/local/6/images/0c6697d3-8a6c-4021-873f-12c3a9899ac0.png.face.png",
    "灶神爷过小年": "https://hfcdn.lingverse.co/33354f6905d59420387496a173c42d75/6A01FCFF/hf/local/6/images/77af840a-6d3e-4e66-af79-9c805f1a96a2.png.face.png",
    "微课-木木老师": "https://hfcdn.lingverse.co/0aa312ad8332986aae15329ee0f292da/6A01FCFF/hf/input/6/videos/7c567c90-e8c3-44fa-a3f5-50f42b839176.mp4.face.png",
    "ai漫剧女主2": "https://hfcdn.lingverse.co/67843e7f561682f1bd65bc56e1b9bd6c/6A01FCFF/hf/input/6/videos/2f9830cc-edd5-4cee-9f87-6fa5e128f74c.mp4.face.png",
    "ai漫剧女主": "https://hfcdn.lingverse.co/e1c78b117fb2fc029b3b491ff08b4c9c/6A01FCFF/hf/input/6/videos/e95110fc-c1c9-47f8-90a2-ad78129b7c31.mp4.face.png",
    "清明节科普-静怡": "https://hfcdn.lingverse.co/0d2e0f83dc3cbdede6bcab6d108284ec/6A01FCFF/hf/local/11/images/af663661-6c89-48d0-b8ef-52343ec6c86d.png.face.png",
    "新概念女": "https://hfcdn.lingverse.co/9510044b26056bd53cb7f6f12deffe6b/6A01FCFF/hf/input/6/videos/156d5dd9-4847-4f9d-a69a-1ba9e84a7f16.mp4.face.png",
    "新概念男": "https://hfcdn.lingverse.co/96049814bc0b64b1ea9ce67a1269b715/6A01FCFF/hf/input/6/videos/2745af4e-8284-4447-8a01-a69c1766bc2a.mp4.face.png",
    "ai科技博主": "https://hfcdn.lingverse.co/6279434ec39fb2b5716f41baf6a730b3/6A01FCFF/hf/input/6/videos/baca3bec-3ea1-41d6-aade-1535ea44e437.mp4.face.png",
    "反诈宣传员": "https://hfcdn.lingverse.co/e08b21deedb3f9ee1c64a1db66ed58eb/6A01FCFF/hf/input/6/videos/bc1ae418-2d24-4818-afa9-dc58f293bee7.mp4.face.png",
    "老人健康-宁姨": "https://hfcdn.lingverse.co/ee7d057682b818d63cef5ba65dc92ff2/6A01FCFF/hf/local/11/images/9c0a3a46-c06d-4d29-9667-d217a603f28a.png.face.png",
    "如花": "https://hfcdn.lingverse.co/dfa5665f54536b9139109ecac99d9094/6A01FCFF/hf/input/11/videos/cd7afca4-cef7-410d-964b-eb739b47f081.mp4?x-oss-process=video/snapshot,t_0,m_fast,ar_auto",
    "健康宣传大使": "https://hfcdn.lingverse.co/507097e6486f788f7384f8d4ae107dff/6A01FCFF/hf/local/11/images/4b1578bb-bd0c-4bcf-b72f-747d936d7e24.png.face.png",
    "讲经济的李老师": "https://hfcdn.lingverse.co/3affdb6d8e4099a12de013eca5bddd26/6A01FCFF/hf/input/11/videos/64729c8d-9f51-4e16-b397-650962af85a3.mp4.face.png",
    "cici": "https://hfcdn.lingverse.co/df37da1ff93fd6c4339fd301c4e2549b/6A01FCFF/hf/local/6/images/5e5b2df2-421e-498f-b373-e41a12b94414.png.face.png",
    "两个萌娃": "https://hfcdn.lingverse.co/1ec91c9a2598d8448ae5186e94059a30/6A01FCFF/hf/local/1000419315/images/29feef64-665d-4ca0-8a1a-31a2495dbfe5.png.face.png",
    "新年卡通巴赫": "https://hfcdn.lingverse.co/3d021803e3f2076c90a85da168344aa5/6A01FCFF/hf/local/6/images/d780c05f-f352-40cc-90ef-38bb7e18f938.png.face1.png",
    "王晗": "https://hfcdn.lingverse.co/210fb2e13e215d7ec23aea0aafb644f7/6A01FCFF/hf/input/1000419315/videos/82512532-a1cc-477d-9ba9-3ee7c835c896.mp4.face.png",
    "新春": "https://hfcdn.lingverse.co/57e3c02f70744a9ec32faaf34eb74fad/6A01FCFF/hf/local/1000542633/images/d1ff63fd-4e59-47c1-97c7-3d8d973bbf2e.png.face.png",
    "沈云舟2.0": "https://hfcdn.lingverse.co/604980903eba65e748720daa5c89f0af/6A01FCFF/hf/local/6/images/603203e9-a566-4e15-928c-cf4debf80270.png.face.png",
    "留学卡姐": "https://hfcdn.lingverse.co/616d95c0396a1e6d77d2eadc247d2759/6A01FCFF/hf/input/6/videos/00bfd491-e576-468c-b761-44bba18248a5.mp4.face.png",
    "肖军-心理咨询老师": "https://hfcdn.lingverse.co/d404e7c7df54fe1a114cd4bc111ac66b/6A01FCFF/hf/input/6/videos/770bb3f9-65b7-426a-96b2-7df746d432a6.mp4.face.png",
    "coco导游": "https://hfcdn.lingverse.co/fd623a9ea1d0ba0daed5813fddc1cc9a/6A01FCFF/hf/input/6/videos/b18f60bb-6391-4b32-a787-b7b451cf5c3f.mp4.face.png",
    "未命名qofx": "https://hfcdn.lingverse.co/abac0182ae61a3e0b029c760271e6809/6A01FCFF/hf/local/6/images/29268e1f-186c-44c0-8936-ab888d189539.jpeg.face.png",
    "张莉": "https://hfcdn.lingverse.co/9634339e8536728a88c2ef8e94cc6043/6A01FCFF/hf/input/1000419315/videos/aac2cfeb-4c5f-42b9-a61d-f9a3e71e2470.mp4.face.png",
    "带货-抽纸": "https://hfcdn.lingverse.co/e27692cff988608fd95b7a97058d646c/6A01FCFF/hf/input/6/videos/f72c8910-1b15-4d25-ae92-3ac61adaf87a.mp4.face.png",
    "口播-汽修": "https://hfcdn.lingverse.co/5a57df841d01b7754a549129ecae7541/6A01FCFF/hf/input/6/videos/2d93985b-d326-4658-be7c-ed50da57ea2b.mp4.face.png",
    "飞影职场真心话": "https://hfcdn.lingverse.co/8aae18f49864d93db4ed4cc22346203b/6A01FCFF/hf/input/3/videos/25c77961-1a83-4b5e-8a34-41920b175d48.mp4.face.png",
    "播客主持": "https://hfcdn.lingverse.co/082aea44cb12f590346c9bcb6fbb68e6/6A01FCFF/hf/input/3/videos/f9d6ea54-f804-404f-b252-828c5a43f01a.mp4.face.png",
    "飞飞": "https://hfcdn.lingverse.co/80d7c2426b768f6c350c2d711eae4b45/6A01FCFF/hf/local/6/images/06d96e3a-4410-403a-ad7a-0329c6b4a416.jpeg.face.png",
    "啊名": "https://hfcdn.lingverse.co/53a18bb8c8397f9cc16d814898781867/6A01FCFF/hf/input/1000542633/videos/f4826abc-84d2-455b-bd6a-2f02b257d64d.mp4.face.png",
    "未命名d96e": "https://hfcdn.lingverse.co/fd9f60fabb891af92218a0df9b892247/6A01FCFF/hf/local/1000542633/images/b683919c-bbd1-4b25-9dfd-764f311e8555.png.face.png",
    "哈基米女孩": "https://hfcdn.lingverse.co/2aee17963637692539ecd2b828e227cd/6A01FCFF/hf/local/6/images/d4bf5e94-208d-4c40-90c2-7a7e3e12cf8e.jpeg.face1.png",
    # 兼容旧映射，防止回归
    "模特cici-中近景": "https://hfcdn.lingverse.co/6aa1a564d0a0afe9fd0a15de5f7806fb/6A01FCFF/hf/input/6/videos/hansining-035/hansining-cover.jpg",
    "模特cici-近景": "https://hfcdn.lingverse.co/6aa1a564d0a0afe9fd0a15de5f7806fb/6A01FCFF/hf/input/6/videos/hansining-035/hansining-cover.jpg",
    # HiFly 接口里出现的别名 / 不带子标题的基名，复用同一封面
    "新年高兴卡通巴赫": "https://hfcdn.lingverse.co/3d021803e3f2076c90a85da168344aa5/6A01FCFF/hf/local/6/images/d780c05f-f352-40cc-90ef-38bb7e18f938.png.face1.png",
    "ai科技博主1": "https://hfcdn.lingverse.co/6279434ec39fb2b5716f41baf6a730b3/6A01FCFF/hf/input/6/videos/baca3bec-3ea1-41d6-aade-1535ea44e437.mp4.face.png",
    "微课": "https://hfcdn.lingverse.co/0aa312ad8332986aae15329ee0f292da/6A01FCFF/hf/input/6/videos/7c567c90-e8c3-44fa-a3f5-50f42b839176.mp4.face.png",
    "飞飞1": "https://hfcdn.lingverse.co/80d7c2426b768f6c350c2d711eae4b45/6A01FCFF/hf/local/6/images/06d96e3a-4410-403a-ad7a-0329c6b4a416.jpeg.face.png",
    "飞飞2": "https://hfcdn.lingverse.co/80d7c2426b768f6c350c2d711eae4b45/6A01FCFF/hf/local/6/images/06d96e3a-4410-403a-ad7a-0329c6b4a416.jpeg.face.png",
    "飞飞3": "https://hfcdn.lingverse.co/80d7c2426b768f6c350c2d711eae4b45/6A01FCFF/hf/local/6/images/06d96e3a-4410-403a-ad7a-0329c6b4a416.jpeg.face.png",
}


class HiflyTokenBody(BaseModel):
    token: Optional[str] = None


class HiflyListBody(HiflyTokenBody):
    page: int = 1
    size: int = 50
    kind: int = 2


class HiflyAvatarLibraryBody(HiflyTokenBody):
    page: int = 1
    size: int = 10
    include_mine: bool = False


class HiflyCreateVideoBody(HiflyTokenBody):
    title: str = "数字人口播"
    avatar: str = Field(..., min_length=1)
    voice: str = Field(..., min_length=1)
    text: str = Field(..., min_length=1, max_length=10000)
    st_show: int = 0
    st_font_name: str = ""
    st_font_size: Optional[int] = None
    st_primary_color: str = ""
    st_outline_color: str = ""
    st_width: Optional[int] = None
    st_height: Optional[int] = None
    st_pos_x: Optional[int] = None
    st_pos_y: Optional[int] = None
    aigc_flag: int = 0


class HiflyTaskBody(HiflyTokenBody):
    task_id: str = Field(..., min_length=1)


class HiflyAvatarCreateBody(HiflyTokenBody):
    title: str = "未命名"
    file_id: str = Field(..., min_length=1)
    model: int = 2
    aigc_flag: int = 0


class HiflyAvatarVideoCreateBody(HiflyTokenBody):
    title: str = "未命名"
    file_id: str = Field(..., min_length=1)
    aigc_flag: int = 0


class HiflyVoiceCreateBody(HiflyTokenBody):
    title: str = Field(..., min_length=1, max_length=20)
    file_id: str = Field(..., min_length=1)
    voice_type: int = 8
    languages: str = "zh"


def _resolved_token(token: Optional[str]) -> str:
    value = (token or "").strip()
    if value:
        return value
    fallback = (settings.hifly_default_token or "").strip()
    if fallback:
        return fallback
    raise HTTPException(status_code=400, detail="请先填写 HiFly API Token，或在 .env 配置 HIFLY_DEFAULT_TOKEN")


def _has_hifly_token(token: Optional[str]) -> bool:
    return bool((token or "").strip() or (settings.hifly_default_token or "").strip())


def _headers(token: Optional[str]) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {_resolved_token(token)}",
        "Accept": "application/json",
    }


def _bearer_from_request(request: Optional[Request]) -> str:
    if request is None:
        return ""
    auth = (request.headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def _billing_headers(request: Request) -> Dict[str, str]:
    token = _bearer_from_request(request)
    if not token:
        raise HTTPException(status_code=401, detail="请先登录后再生成 HiFly 视频")
    headers: Dict[str, str] = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    installation_id = (request.headers.get("X-Installation-Id") or request.headers.get("x-installation-id") or "").strip()
    if installation_id:
        headers["X-Installation-Id"] = installation_id
    billing_key = (getattr(settings, "lobster_mcp_billing_internal_key", None) or "").strip()
    if not billing_key:
        billing_key = (os.environ.get("LOBSTER_MCP_BILLING_INTERNAL_KEY") or "").strip()
    if billing_key:
        headers["X-Lobster-Mcp-Billing"] = billing_key
    return headers


def _billing_base() -> str:
    base = (getattr(settings, "auth_server_base", None) or "").strip().rstrip("/")
    if not base:
        raise HTTPException(status_code=503, detail="未配置 AUTH_SERVER_BASE，无法完成 HiFly 算力计费")
    return base


def _remote_resource_base() -> str:
    return (
        (getattr(settings, "auth_server_base", None) or "").strip().rstrip("/")
        or _LOBSTER_SERVER_PUBLIC
    )


def _estimate_tts_seconds(text: str) -> int:
    clean = re.sub(r"\s+", "", str(text or ""))
    return max(1, int(math.ceil(len(clean) / _HIFLY_TTS_CHARS_PER_SECOND)))


def _duration_seconds(value: Any) -> int:
    try:
        return max(1, int(math.ceil(float(value or 0))))
    except (TypeError, ValueError):
        return 1


def _load_hifly_billing_state() -> Dict[str, Any]:
    try:
        if not _HIFLY_BILLING_STATE_PATH.exists():
            return {"tasks": {}}
        data = json.loads(_HIFLY_BILLING_STATE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"tasks": {}}
    except Exception:
        logger.exception("[hifly-billing] load state failed")
        return {"tasks": {}}


def _save_hifly_billing_state(data: Dict[str, Any]) -> None:
    _HIFLY_BILLING_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _HIFLY_BILLING_STATE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _store_hifly_billing_task(task_id: str, entry: Dict[str, Any]) -> None:
    state = _load_hifly_billing_state()
    tasks = state.setdefault("tasks", {})
    tasks[task_id] = entry
    _save_hifly_billing_state(state)


def _get_hifly_billing_task(task_id: str) -> Dict[str, Any]:
    state = _load_hifly_billing_state()
    tasks = state.get("tasks") if isinstance(state.get("tasks"), dict) else {}
    row = tasks.get(task_id)
    return row if isinstance(row, dict) else {}


def _update_hifly_billing_task(task_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    state = _load_hifly_billing_state()
    tasks = state.setdefault("tasks", {})
    row = tasks.get(task_id)
    if not isinstance(row, dict):
        row = {}
    row.update(patch)
    tasks[task_id] = row
    _save_hifly_billing_state(state)
    return row


async def _hifly_pre_deduct_tts(request: Request, payload: Dict[str, Any]) -> Dict[str, Any]:
    estimated_seconds = _estimate_tts_seconds(str(payload.get("text") or ""))
    expected_credits = estimated_seconds * _HIFLY_TTS_UNIT_CREDITS
    body = {
        "capability_id": _HIFLY_TTS_CAPABILITY_ID,
        "model": "hifly-text-driven",
        "params": {
            "estimated_seconds": estimated_seconds,
            "unit_credits": _HIFLY_TTS_UNIT_CREDITS,
            "text_length": len(str(payload.get("text") or "")),
            "expected_credits": expected_credits,
        },
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(f"{_billing_base()}/capabilities/pre-deduct", json=body, headers=_billing_headers(request))
    if resp.status_code == 402:
        detail = (resp.json() if resp.content else {}).get("detail", "算力不足")
        raise HTTPException(status_code=402, detail=f"算力不足，预计需预扣 {expected_credits} 算力。{detail}")
    if resp.status_code == 401:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录后再生成")
    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"HiFly 计费预扣失败 HTTP {resp.status_code}: {(resp.text or '')[:300]}")
    data = resp.json() if resp.content else {}
    charged = data.get("credits_charged")
    try:
        charged_value = float(charged)
    except (TypeError, ValueError):
        charged_value = float(expected_credits)
    return {
        "credits_pre_deducted": charged_value,
        "estimated_seconds": estimated_seconds,
        "expected_credits": expected_credits,
        "raw": data,
    }


async def _hifly_refund_tts(request: Request, credits: float) -> None:
    if credits <= 0:
        return
    body = {"capability_id": _HIFLY_TTS_CAPABILITY_ID, "credits": credits}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            await client.post(f"{_billing_base()}/capabilities/refund", json=body, headers=_billing_headers(request))
    except Exception:
        logger.exception("[hifly-billing] refund failed credits=%s", credits)


async def _hifly_record_tts(request: Request, task_id: str, entry: Dict[str, Any], result: Dict[str, Any]) -> None:
    credits_pre = float(entry.get("credits_pre_deducted") or 0)
    actual_seconds = _duration_seconds(result.get("duration"))
    credits_final = float(actual_seconds * _HIFLY_TTS_UNIT_CREDITS)
    body = {
        "capability_id": _HIFLY_TTS_CAPABILITY_ID,
        "success": True,
        "source": "hifly_video_task",
        "request_payload": {
            "task_id": task_id,
            "request_id": result.get("request_id") or entry.get("request_id") or "",
            "estimated_seconds": entry.get("estimated_seconds"),
        },
        "response_payload": {
            "duration": result.get("duration"),
            "video_url": result.get("video_url") or "",
        },
        "credits_charged": credits_final,
        "pre_deduct_applied": credits_pre > 0,
        "credits_pre_deducted": credits_pre,
        "credits_final": credits_final,
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(f"{_billing_base()}/capabilities/record-call", json=body, headers=_billing_headers(request))
    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"HiFly 计费结算失败 HTTP {resp.status_code}: {(resp.text or '')[:300]}")


def _url(path: str) -> str:
    return f"{_HIFLY_API_BASE}{path}"


def _raise_for_hifly_business_error(payload: Dict[str, Any]) -> None:
    code = payload.get("code", 0)
    if code in (None, 0):
        return
    message = str(payload.get("message") or payload.get("msg") or "HiFly 接口返回业务错误")
    raise HTTPException(status_code=502, detail=f"HiFly 错误 {code}: {message}")


async def _get(path: str, token: Optional[str], params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.get(_url(path), headers=_headers(token), params=params or {})
    if resp.status_code == 401:
        raise HTTPException(status_code=401, detail="HiFly Token 无效或已过期")
    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"HiFly HTTP {resp.status_code}: {(resp.text or '')[:500]}")
    try:
        payload = resp.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail="HiFly 返回格式不是 JSON") from exc
    if isinstance(payload, dict):
        _raise_for_hifly_business_error(payload)
        return payload
    raise HTTPException(status_code=502, detail="HiFly 返回格式无效")


async def _post(path: str, token: Optional[str], body: Dict[str, Any]) -> Dict[str, Any]:
    headers = _headers(token)
    headers["Content-Type"] = "application/json"
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(_url(path), headers=headers, json=body)
    if resp.status_code == 401:
        raise HTTPException(status_code=401, detail="HiFly Token 无效或已过期")
    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"HiFly HTTP {resp.status_code}: {(resp.text or '')[:500]}")
    try:
        payload = resp.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail="HiFly 返回格式不是 JSON") from exc
    if isinstance(payload, dict):
        _raise_for_hifly_business_error(payload)
        return payload
    raise HTTPException(status_code=502, detail="HiFly 返回格式无效")


async def _put_bytes_to_url(upload_url: str, data: bytes, content_type: str) -> None:
    headers = {"Content-Type": content_type or "application/octet-stream"}
    async with httpx.AsyncClient(timeout=300.0) as client:
        resp = await client.put(upload_url, headers=headers, content=data)
    if resp.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail=f"HiFly 上传文件失败 HTTP {resp.status_code}: {(resp.text or '')[:240]}",
        )


def _normalized_extension(upload: UploadFile, allowed_exts: Dict[str, str], fallback: str) -> str:
    filename_ext = Path(upload.filename or "").suffix.lower().lstrip(".")
    if filename_ext in allowed_exts:
        return filename_ext
    guessed = (mimetypes.guess_extension(upload.content_type or "") or "").lower().lstrip(".")
    if guessed == "jpe":
        guessed = "jpeg"
    if guessed in allowed_exts:
        return guessed
    return fallback


async def _upload_file_to_hifly(
    token: Optional[str],
    upload: UploadFile,
    *,
    allowed_exts: Dict[str, str],
    max_bytes: int,
    fallback_ext: str,
) -> Dict[str, Any]:
    raw = await upload.read()
    if not raw:
        raise HTTPException(status_code=400, detail="上传文件为空")
    if len(raw) > max_bytes:
        raise HTTPException(status_code=400, detail=f"上传文件不能超过 {max_bytes // (1024 * 1024)}MB")

    ext = _normalized_extension(upload, allowed_exts, fallback_ext)
    if ext not in allowed_exts:
        raise HTTPException(status_code=400, detail=f"仅支持 {', '.join(sorted(allowed_exts))} 格式")

    upload_meta = await _post("/api/v2/hifly/tool/create_upload_url", token, {"file_extension": ext})
    upload_url = str(upload_meta.get("upload_url") or "").strip()
    file_id = str(upload_meta.get("file_id") or "").strip()
    if not upload_url or not file_id:
        raise HTTPException(status_code=502, detail="HiFly 未返回 upload_url 或 file_id")

    content_type = str(upload_meta.get("content_type") or "").strip() or allowed_exts.get(ext) or upload.content_type or "application/octet-stream"
    await _put_bytes_to_url(upload_url, raw, content_type)
    return {
        "file_id": file_id,
        "content_type": content_type,
        "filename": upload.filename or f"upload.{ext}",
        "size": len(raw),
    }


def _safe_title(value: str, default: str = "未命名", max_len: int = 20) -> str:
    text = str(value or "").strip() or default
    return text[:max_len] or default


def _normalize_title(value: str) -> str:
    text = re.sub(r"\s+", "", str(value or ""))
    text = text.replace("（", "(").replace("）", ")")
    # 处理 "-视频素材N" 以及紧跟其后的动作描述后缀（如 "-视频素材5-微笑动作"）
    text = re.sub(r"-视频素材\d+(?:-.*)?$", "", text)
    text = re.sub(r"-(分享|直播|近景|中近景|远中景|造型\d*)$", "", text)
    text = re.sub(r"-\d{2}$", "", text)
    # HiFly 公共数字人常见的家庭角色 / 动作后缀
    text = re.sub(r"-(奶奶|爸爸|妈妈|爷爷|姥姥|外婆|外公)$", "", text)
    text = re.sub(r"-(静|走路有动作|拿着旗子走|10s行走停住|微笑动作|严肃|\d+秒)$", "", text)
    return text


def _base_name(title: str) -> str:
    return _normalize_title(title)


_VOICE_GROUP_STOP_WORDS = {
    "默认风格",
    "普通话",
    "英语",
    "英文",
    "日语",
    "韩语",
    "粤语",
    "四川话",
    "上海话",
    "天津话",
    "郑州话",
    "武汉话",
    "温柔",
    "舒缓",
    "气质",
    "浑厚",
    "直播",
    "分享",
    "授课",
    "默认",
}

_VOICE_LANGUAGE_TAGS = {
    "zh": "普通话",
    "en": "英语",
    "english": "英语",
    "jp": "日语",
    "ko": "韩语",
    "zh_cantonese": "粤语",
    "zh_sichuanese": "四川话",
    "zh_shanghainese": "上海话",
    "zh_tianjinese": "天津话",
    "zh_zhengzhounese": "郑州话",
    "zh_wuhanese": "武汉话",
}


def _voice_title_parts(title: str) -> tuple[str, str]:
    text = re.sub(r"\s+", " ", str(title or "").strip())
    if not text:
        return "未命名声音", ""
    if " " not in text:
        return text, ""
    base, style = text.split(" ", 1)
    return base.strip() or text, style.strip()


def _should_group_voice_title(base_title: str, full_title: str) -> bool:
    if not base_title or base_title == full_title:
        return False
    if base_title in _VOICE_GROUP_STOP_WORDS:
        return False
    return len(base_title) >= 2


def _style_label_for_voice(group_title: str, full_title: str, explicit_style: str) -> str:
    style = re.sub(r"\s+", " ", str(explicit_style or "").strip())
    if style:
        return style
    title = re.sub(r"\s+", " ", str(full_title or "").strip())
    if not title:
        return "默认风格"
    if title == group_title:
        return "默认风格"
    if title.startswith(group_title):
        suffix = title[len(group_title) :].strip()
        if suffix:
            return suffix
    return title


def _sort_style_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    def _sort_key(row: Dict[str, Any]) -> tuple[int, str]:
        label = str(row.get("label") or "")
        return (0 if label in {"默认风格", "默认"} else 1, label)

    return sorted(rows, key=_sort_key)


def _guess_voice_cover_and_tags(title: str, style_labels: List[str], section: str) -> Dict[str, Any]:
    base = str(title or "").strip()
    combined = " ".join([base] + [str(label or "").strip() for label in style_labels if str(label or "").strip()])
    lowered = combined.lower()

    tags: List[str] = ["我的声音" if section == "mine" else "公共声音"]
    for token in ("普通话", "英语", "日语", "韩语", "粤语", "四川话", "上海话", "天津话", "郑州话", "武汉话"):
        if token in combined and token not in tags:
            tags.append(token)

    if any(keyword in combined for keyword in ["奶奶", "长辈", "老人", "阿姨", "爷爷", "老中医", "大师"]):
        return {"cover_url": _GENERIC_ELDER_COVER, "tags": tags + ["长辈向"], "cover_rank": 1}
    if any(keyword in combined for keyword in ["萌娃", "宝宝", "儿童", "小孩", "萝莉", "卡通", "动漫"]):
        return {"cover_url": _GENERIC_CHILD_COVER, "tags": tags + ["角色感"], "cover_rank": 1}
    if any(keyword in combined for keyword in ["直播", "带货", "探店", "口播", "播客", "主持", "分享", "授课", "财经", "家政", "养生", "国学"]):
        return {"cover_url": _GENERIC_LIFESTYLE_COVER, "tags": tags + ["场景化"], "cover_rank": 1}
    if any(keyword in combined for keyword in ["男", "哥", "先生", "老师", "小伙", "老周", "沃克", "约翰", "卡尔", "尼奥", "浩", "风", "杰", "豪", "南栀"]) or "male" in lowered:
        return {"cover_url": _GENERIC_MALE_COVER, "tags": tags + ["男声"], "cover_rank": 2}
    return {"cover_url": _GENERIC_FEMALE_COVER, "tags": tags + ["女声"], "cover_rank": 2}


def _guess_avatar_cover_and_tags(title: str, section: str) -> Dict[str, Any]:
    raw = str(title or "")
    lowered = raw.lower()
    tags: List[str] = ["我的数字人" if section == "mine" else "公共数字人"]

    if any(keyword in raw for keyword in ["奶奶", "长辈", "老人", "阿姨", "爷爷"]):
        return {"cover_url": _GENERIC_ELDER_COVER, "tags": tags + ["长辈", "写实"], "cover_rank": 1}
    if any(keyword in raw for keyword in ["萌娃", "宝宝", "小孩", "儿童", "学生"]):
        return {"cover_url": _GENERIC_CHILD_COVER, "tags": tags + ["萌娃", "家庭"], "cover_rank": 1}
    if any(keyword in raw for keyword in ["探店", "导购", "旅游", "直播", "美妆"]) or "lifestyle" in lowered:
        return {"cover_url": _GENERIC_LIFESTYLE_COVER, "tags": tags + ["生活方式", "写实"], "cover_rank": 1}
    if any(keyword in raw for keyword in ["男", "先生", "总", "博士", "科技"]):
        return {"cover_url": _GENERIC_MALE_COVER, "tags": tags + ["男主播", "写实"], "cover_rank": 2}
    return {"cover_url": _GENERIC_FEMALE_COVER, "tags": tags + ["女主播", "写实"], "cover_rank": 2}


def _sort_avatar_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            int(row.get("cover_rank", 9)),
            _base_name(str(row.get("title") or "")),
            str(row.get("title") or ""),
        ),
    )


def _apply_material_counts(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    counts: Dict[str, int] = {}
    for row in rows:
        key = _base_name(str(row.get("title") or row.get("avatar") or ""))
        counts[key] = counts.get(key, 0) + 1
    for row in rows:
        key = _base_name(str(row.get("title") or row.get("avatar") or ""))
        row["material_count"] = counts.get(key, 1)
    return rows


def _pick_cover_url(item: Dict[str, Any]) -> str:
    for key in ("cover_url", "cover", "image_url", "avatar_url", "poster_url", "thumbnail_url", "preview_url"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return ""


def _public_avatar_cover_override(avatar_id: str, title: str) -> str:
    override = str(_PUBLIC_AVATAR_COVER_OVERRIDES_BY_AVATAR.get(avatar_id) or "").strip()
    if override:
        return override
    raw_title = str(title or "").strip()
    # 1) 完整标题（小写）精确查表
    hit = _PUBLIC_AVATAR_COVER_OVERRIDES_BY_TITLE.get(raw_title.lower())
    if hit:
        return str(hit).strip()
    # 2) 去掉 "-视频素材N"、"-近景/中近景/远中景/直播/分享" 等后缀的基名再查一次
    base = _base_name(raw_title).lower()
    if base and base != raw_title.lower():
        hit = _PUBLIC_AVATAR_COVER_OVERRIDES_BY_TITLE.get(base)
        if hit:
            return str(hit).strip()
    return ""


def _enrich_avatar_rows(rows: List[Dict[str, Any]], section: str) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for item in rows or []:
        avatar_id = str(item.get("avatar") or "").strip()
        if not avatar_id:
            continue
        title = str(item.get("title") or avatar_id).strip() or avatar_id
        guessed = _guess_avatar_cover_and_tags(title, section)
        actual_cover_url = _pick_cover_url(item)
        override_cover_url = _public_avatar_cover_override(avatar_id, title) if section == "public" else ""
        resolved_cover_url = actual_cover_url or override_cover_url
        # 公共数字人列表只展示有真实封面的项，无封面的直接丢弃，避免渲染渐变占位
        if section == "public" and not resolved_cover_url:
            continue
        result.append(
            {
                "avatar": avatar_id,
                "title": title,
                "kind": item.get("kind"),
                "section": section,
                "section_label": "我的数字人" if section == "mine" else "公共数字人",
                "cover_url": resolved_cover_url,
                "cover_guessed": not bool(resolved_cover_url),
                "cover_rank": int(guessed.get("cover_rank", 9)),
                "material_count": None,
                "tags": list(guessed.get("tags") or []),
            }
        )
    result = _apply_material_counts(result)
    return _sort_avatar_rows(result)


def _read_json_dict(path: Path) -> Dict[str, Any]:
    try:
        if not path.is_file():
            return {}
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except Exception:
        logger.warning("[hifly] failed to read json %s", path, exc_info=True)
        return {}


def _load_local_public_avatar_rows() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for path in (_HIFLY_PUBLIC_AVATAR_CACHE_PATH, _HIFLY_PUBLIC_AVATARS_PATH):
        data = _read_json_dict(path)
        candidates = data.get("public")
        if not isinstance(candidates, list):
            candidates = data.get("data")
        if not isinstance(candidates, list):
            continue
        for item in candidates:
            if not isinstance(item, dict):
                continue
            avatar_id = str(item.get("avatar") or item.get("avatar_id") or "").strip()
            if not avatar_id or avatar_id in seen:
                continue
            seen.add(avatar_id)
            rows.append(item)
    return rows


def _merge_avatar_rows(*row_groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for rows in row_groups:
        for row in rows or []:
            key = str(row.get("avatar") or "").strip()
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(row)
    return _sort_avatar_rows(_apply_material_counts(merged))


def _local_preview_url(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    name = Path(raw.split("?", 1)[0]).name
    if not name:
        return ""
    local_path = _HIFLY_PREVIEWS_DIR / name
    if local_path.is_file():
        return f"/static/hifly_previews/{name}"
    return ""


def _with_local_preview_urls(row: Dict[str, Any]) -> Dict[str, Any]:
    cloned = dict(row)
    styles: List[Dict[str, Any]] = []
    for style in cloned.get("styles") or []:
        if not isinstance(style, dict):
            continue
        style_copy = dict(style)
        local_url = _local_preview_url(str(style_copy.get("demo_url") or style_copy.get("preview_url") or ""))
        if local_url:
            style_copy["demo_url"] = local_url
            style_copy["preview_source"] = "local"
        styles.append(style_copy)
    if styles:
        cloned["styles"] = styles
        primary = next((item for item in styles if item.get("demo_url")), styles[0])
        if primary.get("demo_url"):
            cloned["demo_url"] = primary["demo_url"]
            cloned["voice"] = primary.get("voice") or cloned.get("voice")
    else:
        local_url = _local_preview_url(str(cloned.get("demo_url") or ""))
        if local_url:
            cloned["demo_url"] = local_url
            cloned["preview_source"] = "local"
    return cloned


def _voice_group_key(row: Dict[str, Any]) -> str:
    return str(row.get("title") or row.get("voice") or "").strip()


def _remote_static_url(url: str, remote_base: str) -> str:
    value = str(url or "").strip()
    if not value:
        return ""
    if value.startswith(("http://", "https://")):
        return value
    if value.startswith("/"):
        return f"{remote_base.rstrip('/')}{value}"
    return value


def _with_remote_preview_urls(row: Dict[str, Any], remote_base: str) -> Dict[str, Any]:
    cloned = dict(row)
    if cloned.get("demo_url"):
        cloned["demo_url"] = _remote_static_url(str(cloned.get("demo_url") or ""), remote_base)
    styles: List[Dict[str, Any]] = []
    for style in cloned.get("styles") or []:
        if not isinstance(style, dict):
            continue
        style_copy = dict(style)
        if style_copy.get("demo_url"):
            style_copy["demo_url"] = _remote_static_url(str(style_copy.get("demo_url") or ""), remote_base)
        styles.append(style_copy)
    if styles:
        cloned["styles"] = styles
    return cloned


def _preview_filename_from_url(url: str) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    name = Path(raw.split("?", 1)[0]).name
    if not name or "." not in name:
        return ""
    ext = Path(name).suffix.lower()
    if ext not in {".wav", ".mp3", ".m4a"}:
        return ""
    return name


def _cache_remote_voice_previews(rows: List[Dict[str, Any]], remote_base: str) -> None:
    try:
        _HIFLY_PREVIEWS_DIR.mkdir(parents=True, exist_ok=True)
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            for row in rows or []:
                for style in row.get("styles") or []:
                    if not isinstance(style, dict):
                        continue
                    source_url = _remote_static_url(str(style.get("demo_url") or ""), remote_base)
                    filename = _preview_filename_from_url(source_url)
                    if not source_url or not filename:
                        continue
                    target = _HIFLY_PREVIEWS_DIR / filename
                    if target.is_file():
                        continue
                    resp = client.get(source_url)
                    if resp.status_code >= 400 or not resp.content:
                        continue
                    target.write_bytes(resp.content)
        _write_preview_manifest_from_rows(rows)
    except Exception:
        logger.warning("[hifly] remote voice preview cache refresh failed", exc_info=True)


def _write_preview_manifest_from_rows(rows: List[Dict[str, Any]]) -> None:
    groups: List[Dict[str, Any]] = []
    for row in rows or []:
        title = str(row.get("title") or "").strip()
        members: List[Dict[str, Any]] = []
        for style in row.get("styles") or []:
            if not isinstance(style, dict):
                continue
            filename = _preview_filename_from_url(str(style.get("demo_url") or ""))
            if not filename or not (_HIFLY_PREVIEWS_DIR / filename).is_file():
                continue
            stem = Path(filename).stem
            try:
                member_id = int(stem)
            except ValueError:
                continue
            members.append(
                {
                    "id": member_id,
                    "title": str(style.get("title") or style.get("label") or title or "").strip(),
                    "preview_url": f"/static/hifly_previews/{filename}",
                    "preview_text": str(style.get("preview_text") or "").strip(),
                    "tts_level": style.get("tts_level"),
                }
            )
        if members:
            groups.append(
                {
                    "title": title or str(row.get("voice") or "").strip(),
                    "cover_url": str(row.get("cover_url") or "").strip(),
                    "members": members,
                }
            )
    if groups:
        _HIFLY_PREVIEWS_MANIFEST_PATH.write_text(
            json.dumps(
                {
                    "cached_at": datetime.now().isoformat(timespec="seconds"),
                    "groups": groups,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )


def _local_preview_manifest_rows() -> List[Dict[str, Any]]:
    rows = _load_consumer_preview_manifest(require_local_files=True)
    return [_with_local_preview_urls(row) for row in rows]


async def _fetch_remote_library(path: str, token: Optional[str]) -> Dict[str, Any]:
    base = _remote_resource_base()
    body: Dict[str, Any] = {"token": (token or "").strip()}
    if not body["token"]:
        body.pop("token", None)
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(f"{base}{path}", json=body)
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=(resp.text or "")[:500])
    try:
        payload = resp.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail="远端 HiFly 资源返回格式不是 JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="远端 HiFly 资源返回格式无效")
    return payload


async def _cache_remote_public_avatars(token: Optional[str]) -> None:
    try:
        rows = await _fetch_remote_public_rows("/api/hifly/avatar/library", token)
        _save_public_avatar_cache(rows)
    except Exception:
        logger.warning("[hifly] remote avatar cache refresh failed", exc_info=True)


async def _fetch_remote_public_rows(path: str, token: Optional[str]) -> List[Dict[str, Any]]:
    payload = await _fetch_remote_library(path, token)
    rows = payload.get("public") or []
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _save_public_avatar_cache(rows: List[Dict[str, Any]]) -> None:
    if not rows:
        return
    _HIFLY_PUBLIC_AVATAR_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _HIFLY_PUBLIC_AVATAR_CACHE_PATH.write_text(
        json.dumps(
            {
                "cached_at": datetime.now().isoformat(timespec="seconds"),
                "source": "remote",
                "public": rows,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _enrich_voice_rows(rows: List[Dict[str, Any]], section: str) -> List[Dict[str, Any]]:
    prepared: List[Dict[str, Any]] = []
    for item in rows or []:
        voice_id = str(item.get("voice") or item.get("voice_id") or "").strip()
        if not voice_id:
            continue
        title = str(item.get("title") or voice_id).strip() or voice_id
        base_title, explicit_style = _voice_title_parts(title)
        demo_url = str(item.get("demo_url") or item.get("audio_url") or item.get("preview_url") or "").strip()
        language = str(item.get("languages") or item.get("language") or "").strip()
        prepared.append(
            {
                "voice": voice_id,
                "title": title,
                "base_title": base_title,
                "explicit_style": explicit_style,
                "kind": item.get("kind"),
                "demo_url": demo_url,
                "rate": str(item.get("rate") or "").strip(),
                "pitch": str(item.get("pitch") or "").strip(),
                "volume": str(item.get("volume") or "").strip(),
                "language": language,
            }
        )

    groups: Dict[str, Dict[str, Any]] = {}
    for item in prepared:
        full_title = str(item["title"] or "")
        base_title = str(item["base_title"] or full_title)
        if _should_group_voice_title(base_title, full_title):
            group_key = f"group::{section}::{base_title}"
            group_title = base_title
        else:
            group_key = f"voice::{section}::{item['voice']}"
            group_title = full_title

        group = groups.setdefault(
            group_key,
            {
                "voice": str(item["voice"]),
                "title": group_title,
                "kind": item.get("kind"),
                "section": section,
                "section_label": "我的声音" if section == "mine" else "公共声音",
                "styles": [],
            },
        )

        style_label = _style_label_for_voice(group_title, full_title, str(item.get("explicit_style") or ""))
        language_tag = _VOICE_LANGUAGE_TAGS.get(str(item.get("language") or "").strip(), str(item.get("language") or "").strip())
        group["styles"].append(
            {
                "voice": str(item["voice"]),
                "title": full_title,
                "label": style_label,
                "demo_url": str(item.get("demo_url") or ""),
                "rate": str(item.get("rate") or ""),
                "pitch": str(item.get("pitch") or ""),
                "volume": str(item.get("volume") or ""),
                "language": language_tag,
            }
        )

    result: List[Dict[str, Any]] = []
    for group in groups.values():
        styles = _sort_style_rows(list(group.get("styles") or []))
        if not styles:
            continue

        seen_labels: Dict[str, int] = {}
        normalized_styles: List[Dict[str, Any]] = []
        for style in styles:
            label = str(style.get("label") or "默认风格").strip() or "默认风格"
            seen_labels[label] = seen_labels.get(label, 0) + 1
            if seen_labels[label] > 1:
                label = f"{label} {seen_labels[label]}"
            normalized_style = dict(style)
            normalized_style["label"] = label
            normalized_styles.append(normalized_style)

        style_labels = [str(style.get("label") or "") for style in normalized_styles]
        guessed = _guess_voice_cover_and_tags(str(group.get("title") or ""), style_labels, section)
        tags = list(guessed.get("tags") or [])
        language_tags = [str(style.get("language") or "").strip() for style in normalized_styles if str(style.get("language") or "").strip()]
        for token in language_tags[:2]:
            if token not in tags:
                tags.append(token)

        primary_style = next((style for style in normalized_styles if str(style.get("demo_url") or "").strip()), normalized_styles[0])
        search_text = " ".join(
            filter(
                None,
                [
                    str(group.get("title") or ""),
                    " ".join(style_labels),
                    " ".join(str(style.get("voice") or "") for style in normalized_styles),
                ],
            )
        )

        result.append(
            {
                "voice": str(primary_style.get("voice") or group.get("voice") or ""),
                "title": str(group.get("title") or ""),
                "kind": group.get("kind"),
                "section": section,
                "section_label": "我的声音" if section == "mine" else "公共声音",
                "demo_url": str(primary_style.get("demo_url") or ""),
                "cover_url": str(guessed.get("cover_url") or ""),
                "cover_rank": int(guessed.get("cover_rank", 9)),
                "style_count": len(normalized_styles),
                "styles": normalized_styles,
                "tags": tags,
                "search_text": search_text,
            }
        )

    return sorted(
        result,
        key=lambda row: (
            int(row.get("cover_rank", 9)),
            0 if row.get("section") == "mine" else 1,
            _base_name(str(row.get("title") or "")),
            str(row.get("title") or ""),
        ),
    )


async def _fetch_avatar_page(token: Optional[str], kind: int, page: int, size: int) -> List[Dict[str, Any]]:
    payload = await _get(
        "/api/v2/hifly/avatar/list",
        token,
        {"page": page, "size": max(1, min(_MAX_AVATAR_PAGE_SIZE, size)), "kind": kind},
    )
    return payload.get("data") or []


async def _fetch_all_avatar_pages(
    token: Optional[str],
    kind: int,
    page_size: int = _MAX_AVATAR_PAGE_SIZE,
    max_pages: int = 12,
) -> List[Dict[str, Any]]:
    all_rows: List[Dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        rows = await _fetch_avatar_page(token, kind, page, page_size)
        if not rows:
            break
        all_rows.extend(rows)
        if len(rows) < page_size:
            break
    return all_rows


async def _fetch_voice_page(token: Optional[str], kind: int, page: int, size: int) -> List[Dict[str, Any]]:
    payload = await _get(
        "/api/v2/hifly/voice/list",
        token,
        {"page": page, "size": max(1, min(_MAX_VOICE_PAGE_SIZE, size)), "kind": kind},
    )
    return payload.get("data") or []


async def _fetch_all_voice_pages(
    token: Optional[str],
    kind: int,
    page_size: int = 120,
    max_pages: int = 12,
) -> List[Dict[str, Any]]:
    all_rows: List[Dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        rows = await _fetch_voice_page(token, kind, page, page_size)
        if not rows:
            break
        all_rows.extend(rows)
        if len(rows) < page_size:
            break
    return all_rows


async def _safe_avatar_list(token: Optional[str], kind: int, size: int = _MAX_AVATAR_PAGE_SIZE) -> Dict[str, Any]:
    try:
        rows = await _fetch_all_avatar_pages(token, kind, page_size=size)
        return {"ok": True, "kind": kind, "data": rows, "message": ""}
    except HTTPException as exc:
        detail = str(exc.detail)
        if exc.status_code == 502 and ("不支持此分类" in detail or "错误 11" in detail):
            logger.info("[hifly] avatar kind=%s unsupported", kind)
            return {"ok": False, "kind": kind, "data": [], "message": detail, "unsupported": True}
        raise


async def _safe_voice_list(token: Optional[str], kind: int, size: int = 120) -> Dict[str, Any]:
    try:
        rows = await _fetch_all_voice_pages(token, kind, page_size=size)
        return {"ok": True, "kind": kind, "data": rows, "message": ""}
    except HTTPException as exc:
        detail = str(exc.detail)
        if exc.status_code == 502 and ("不支持此分类" in detail or "错误 11" in detail):
            logger.info("[hifly] voice kind=%s unsupported", kind)
            return {"ok": False, "kind": kind, "data": [], "message": detail, "unsupported": True}
        raise


def _status_text(status: int) -> str:
    return {1: "等待中", 2: "处理中", 3: "已完成", 4: "失败"}.get(status, "未知")


@router.post("/api/hifly/account/credit")
async def hifly_account_credit(body: HiflyTokenBody):
    payload = await _get("/api/v2/hifly/account/credit", body.token)
    return {"ok": True, "left": payload.get("left"), "raw": payload}


@router.post("/api/hifly/avatar/list")
async def hifly_avatar_list(body: HiflyListBody):
    payload = await _get(
        "/api/v2/hifly/avatar/list",
        body.token,
        {"page": max(1, body.page), "size": max(1, min(_MAX_AVATAR_PAGE_SIZE, body.size)), "kind": body.kind},
    )
    return {"ok": True, "data": payload.get("data") or [], "raw": payload}


@router.post("/api/hifly/avatar/library")
async def hifly_avatar_library(body: HiflyAvatarLibraryBody, background_tasks: BackgroundTasks):
    mine_rows: List[Dict[str, Any]] = []
    mine_supported = True
    mine_message = ""
    if body.include_mine and _has_hifly_token(body.token):
        mine_resp = await _safe_avatar_list(body.token, 1)
        mine_rows = _enrich_avatar_rows(mine_resp.get("data") or [], "mine")
        mine_seen: set[str] = set()
        mine_rows = [row for row in mine_rows if not (row["avatar"] in mine_seen or mine_seen.add(row["avatar"]))]
        mine_supported = not bool(mine_resp.get("unsupported"))
        mine_message = mine_resp.get("message") or ""

    source = "local"
    local_public = _enrich_avatar_rows(_load_local_public_avatar_rows(), "public")
    if local_public:
        public_rows = local_public
        try:
            remote_rows = await _fetch_remote_public_rows("/api/hifly/avatar/library", body.token)
            background_tasks.add_task(_save_public_avatar_cache, remote_rows)
            public_rows = _merge_avatar_rows(local_public, remote_rows)
            source = "local+remote"
        except HTTPException:
            background_tasks.add_task(_cache_remote_public_avatars, body.token)
            logger.warning("[hifly] remote avatar library refresh failed; using local public rows", exc_info=True)
    else:
        public_rows = await _fetch_remote_public_rows("/api/hifly/avatar/library", body.token)
        source = "remote"
        background_tasks.add_task(_save_public_avatar_cache, public_rows)
    public_rows = _merge_avatar_rows(public_rows)

    return {
        "ok": True,
        "mine": mine_rows,
        "public": public_rows,
        "mine_supported": mine_supported,
        "mine_message": mine_message,
        "public_total": len(public_rows),
        "public_page": 1,
        "public_size": len(public_rows),
        "public_has_more": False,
        "mine_total": len(mine_rows),
        "using_default_token": bool((settings.hifly_default_token or "").strip() and not (body.token or "").strip()),
        "source": source,
    }


@router.post("/api/hifly/voice/list")
async def hifly_voice_list(body: HiflyListBody):
    payload = await _get(
        "/api/v2/hifly/voice/list",
        body.token,
        {"page": max(1, body.page), "size": max(1, min(_MAX_VOICE_PAGE_SIZE, body.size)), "kind": body.kind},
    )
    return {"ok": True, "data": payload.get("data") or [], "raw": payload}


def _load_consumer_preview_manifest(require_local_files: bool = False) -> List[Dict[str, Any]]:
    """读取 prefetch_hifly_previews.py 生成的 manifest，转换为 voice/library 公共声音条目。

    每个 manifest member 的 numeric id 用作 voice 字段，附 demo_url 指向本地 wav。
    注意：这些 voice 字符串不是 HiFly 开放 API 标识，**不能直接用于 TTS 任务**；
    仅供前端"试听"显示使用。如需提交 TTS，需要后续做 numeric id ↔ open API voice 映射。
    """
    import json
    from pathlib import Path

    manifest_path = _HIFLY_PREVIEWS_MANIFEST_PATH
    if not manifest_path.exists():
        return []
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    groups = raw.get("groups") or []

    result: List[Dict[str, Any]] = []
    for grp in groups:
        if not isinstance(grp, dict):
            continue
        gtitle = str(grp.get("title") or "").strip()
        cover_url = str(grp.get("cover_url") or "").strip()
        members = grp.get("members") or []
        # 收集组内有可用预览的 members
        usable = []
        for member in members:
            if not isinstance(member, dict) or not member.get("preview_url"):
                continue
            if require_local_files and not _local_preview_url(str(member.get("preview_url") or "")):
                continue
            usable.append(member)
        if not usable:
            continue

        styles: List[Dict[str, Any]] = []
        for m in usable:
            mid = m.get("id")
            if not isinstance(mid, int):
                continue
            mtitle = str(m.get("title") or "").strip() or gtitle
            # voice 字段用 "consumer_<numeric_id>" 前缀，避免与开放 API voice 混淆
            voice_str = f"consumer_{mid}"
            label = mtitle if mtitle and mtitle != gtitle else "默认风格"
            styles.append({
                "voice": voice_str,
                "title": mtitle or gtitle,
                "label": label,
                "demo_url": str(m.get("preview_url") or ""),
                "rate": "", "pitch": "", "volume": "", "language": "",
            })

        if not styles:
            continue

        primary = styles[0]
        search_text = " ".join(filter(None, [gtitle] + [s["label"] for s in styles]))
        result.append({
            "voice": str(primary["voice"]),
            "title": gtitle or primary["title"],
            "kind": "consumer_public",
            "section": "public",
            "section_label": "公共声音",
            "demo_url": primary["demo_url"],
            "cover_url": cover_url,
            "cover_rank": 1 if cover_url else 9,
            "style_count": len(styles),
            "styles": styles,
            "tags": ["公共声音", "可试听"],
            "search_text": search_text,
        })
    return result


def _merge_voice_rows(primary_rows: List[Dict[str, Any]], extra_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """合并公共声音；同一标题保留主来源，并用额外来源补齐缺失 style。"""
    by_key: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for row in primary_rows or []:
        key = _voice_group_key(row)
        if not key or key in by_key:
            continue
        by_key[key] = dict(row)
        order.append(key)
    for row in extra_rows or []:
        key = _voice_group_key(row)
        if not key:
            continue
        if key not in by_key:
            by_key[key] = dict(row)
            order.append(key)
            continue
        base = by_key[key]
        base_styles = [
            dict(style)
            for style in (base.get("styles") or [])
            if isinstance(style, dict)
        ]
        seen_styles = {
            str(style.get("voice") or style.get("label") or style.get("title") or "").strip()
            for style in base_styles
        }
        for style in row.get("styles") or []:
            if not isinstance(style, dict):
                continue
            style_key = str(style.get("voice") or style.get("label") or style.get("title") or "").strip()
            if not style_key or style_key in seen_styles:
                continue
            seen_styles.add(style_key)
            base_styles.append(dict(style))
        if base_styles:
            base["styles"] = base_styles
            base["style_count"] = len(base_styles)
    return [by_key[key] for key in order]


@router.post("/api/hifly/voice/library")
async def hifly_voice_library(body: HiflyTokenBody, background_tasks: BackgroundTasks):
    mine_resp: Dict[str, Any] = {"data": [], "unsupported": False, "message": ""}
    if _has_hifly_token(body.token):
        mine_resp = await _safe_voice_list(body.token, 1)
    public_manifest = _local_preview_manifest_rows()
    source = "local" if public_manifest else "remote"
    if public_manifest:
        remote_base = _remote_resource_base()
        try:
            remote_public = [
                _with_remote_preview_urls(row, remote_base)
                for row in await _fetch_remote_public_rows("/api/hifly/voice/library", body.token)
            ]
            if remote_public:
                background_tasks.add_task(_cache_remote_voice_previews, remote_public, remote_base)
        except HTTPException:
            remote_public = []
            logger.warning("[hifly] remote voice library refresh failed; using local preview manifest", exc_info=True)
        public_merged = _merge_voice_rows(public_manifest, remote_public)
    else:
        remote_base = _remote_resource_base()
        remote_public = [
            _with_remote_preview_urls(row, remote_base)
            for row in await _fetch_remote_public_rows("/api/hifly/voice/library", body.token)
        ]
        if remote_public:
            background_tasks.add_task(_cache_remote_voice_previews, remote_public, remote_base)
        public_merged = remote_public
    return {
        "ok": True,
        "mine": _enrich_voice_rows(mine_resp.get("data") or [], "mine"),
        "public": public_merged,
        "mine_supported": not bool(mine_resp.get("unsupported")),
        "mine_message": mine_resp.get("message") or "",
        "manifest_count": len(public_manifest),
        "using_default_token": bool((settings.hifly_default_token or "").strip() and not (body.token or "").strip()),
        "source": source,
    }


@router.post("/api/hifly/video/create-by-tts")
async def hifly_video_create_by_tts(body: HiflyCreateVideoBody, request: Request):
    title = _safe_title(body.title, default="数字人口播")
    voice_clean = body.voice.strip()
    if voice_clean.startswith("consumer_"):
        raise HTTPException(
            status_code=400,
            detail="该公共声音目前仅支持试听，暂不支持用于生成口播视频。请改选「我的声音」或带 voice id 的公共声音。",
        )
    payload: Dict[str, Any] = {
        "title": title,
        "avatar": body.avatar.strip(),
        "voice": voice_clean,
        "text": body.text.strip(),
        "st_show": 1 if int(body.st_show or 0) == 1 else 0,
        "aigc_flag": int(body.aigc_flag or 0),
    }
    optional_fields = {
        "st_font_name": body.st_font_name,
        "st_font_size": body.st_font_size,
        "st_primary_color": body.st_primary_color,
        "st_outline_color": body.st_outline_color,
        "st_width": body.st_width,
        "st_height": body.st_height,
        "st_pos_x": body.st_pos_x,
        "st_pos_y": body.st_pos_y,
    }
    for key, value in optional_fields.items():
        if value not in (None, ""):
            payload[key] = value

    billing = await _hifly_pre_deduct_tts(request, payload)
    try:
        data = await _post("/api/v2/hifly/video/create_by_tts", body.token, payload)
        nested = data.get("data") if isinstance(data.get("data"), dict) else {}
        task_id = str(data.get("task_id") or nested.get("task_id") or "").strip()
        if not task_id:
            await _hifly_refund_tts(request, float(billing.get("credits_pre_deducted") or 0))
            raise HTTPException(status_code=502, detail="HiFly 未返回 task_id")
    except HTTPException:
        if "task_id" not in locals():
            await _hifly_refund_tts(request, float(billing.get("credits_pre_deducted") or 0))
        raise
    request_id = data.get("request_id") or nested.get("request_id") or ""
    _store_hifly_billing_task(task_id, {
        "capability_id": _HIFLY_TTS_CAPABILITY_ID,
        "billing_status": "pending",
        "credits_pre_deducted": billing.get("credits_pre_deducted"),
        "estimated_seconds": billing.get("estimated_seconds"),
        "expected_credits": billing.get("expected_credits"),
        "request_id": request_id,
        "created_at": datetime.utcnow().isoformat() + "Z",
        "title": title,
    })
    logger.info("[hifly] create_by_tts ok task_id=%s avatar=%s voice=%s title=%s", task_id, body.avatar, body.voice, title)
    return {
        "ok": True,
        "task_id": task_id,
        "request_id": request_id,
        "billing": {
            "capability_id": _HIFLY_TTS_CAPABILITY_ID,
            "credits_pre_deducted": billing.get("credits_pre_deducted"),
            "estimated_seconds": billing.get("estimated_seconds"),
            "expected_credits": billing.get("expected_credits"),
            "status": "pending",
        },
        "raw": data,
    }


@router.post("/api/hifly/video/task")
async def hifly_video_task(body: HiflyTaskBody, request: Request):
    payload = await _get("/api/v2/hifly/video/task", body.token, {"task_id": body.task_id.strip()})
    # HiFly 部分接口会把业务字段嵌在 data 里（{code, message, data: {...}}），兼容两种写法
    nested = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    def _pick(key: str):
        if key in payload and payload.get(key) not in (None, ""):
            return payload.get(key)
        return nested.get(key)
    status = int(_pick("status") or 0)
    video_url = str(
        _pick("video_Url")
        or _pick("video_url")
        or _pick("videoUrl")
        or ""
    ).strip()
    duration = _pick("duration")
    request_id = _pick("request_id") or ""
    result = {
        "ok": status != 4,
        "task_id": body.task_id,
        "status": status,
        "status_text": _status_text(status),
        "video_url": video_url,
        "duration": duration,
        "request_id": request_id,
        "message": payload.get("message") or "",
        "raw": payload,
    }
    task_key = body.task_id.strip()
    billing_entry = _get_hifly_billing_task(task_key)
    if billing_entry and billing_entry.get("billing_status") not in ("settled", "refunded"):
        if status == 3:
            try:
                await _hifly_record_tts(request, task_key, billing_entry, result)
                actual_seconds = _duration_seconds(duration)
                result["billing"] = _update_hifly_billing_task(task_key, {
                    "billing_status": "settled",
                    "credits_final": float(actual_seconds * _HIFLY_TTS_UNIT_CREDITS),
                    "actual_seconds": actual_seconds,
                    "duration": duration,
                    "video_url": video_url,
                    "request_id": request_id or billing_entry.get("request_id") or "",
                    "settled_at": datetime.utcnow().isoformat() + "Z",
                })
            except HTTPException as exc:
                logger.exception("[hifly-billing] settle failed task_id=%s", task_key)
                result["billing"] = _update_hifly_billing_task(task_key, {
                    "billing_status": "settle_failed",
                    "billing_error": str(exc.detail)[:500],
                    "updated_at": datetime.utcnow().isoformat() + "Z",
                })
        elif status == 4:
            credits_pre = float(billing_entry.get("credits_pre_deducted") or 0)
            await _hifly_refund_tts(request, credits_pre)
            result["billing"] = _update_hifly_billing_task(task_key, {
                "billing_status": "refunded",
                "credits_refunded": credits_pre,
                "message": result.get("message") or "",
                "refunded_at": datetime.utcnow().isoformat() + "Z",
            })
    elif billing_entry:
        result["billing"] = billing_entry
    return result


@router.post("/api/hifly/tool/upload")
async def hifly_tool_upload(
    token: Optional[str] = Form(None),
    upload_kind: str = Form("image"),
    file: UploadFile = File(...),
):
    upload_kind = str(upload_kind or "image").strip().lower()
    if upload_kind == "video":
        uploaded = await _upload_file_to_hifly(token, file, allowed_exts=_VIDEO_EXTS, max_bytes=_VIDEO_MAX_BYTES, fallback_ext="mp4")
    elif upload_kind == "audio":
        uploaded = await _upload_file_to_hifly(token, file, allowed_exts=_AUDIO_EXTS, max_bytes=_AUDIO_MAX_BYTES, fallback_ext="mp3")
    else:
        uploaded = await _upload_file_to_hifly(token, file, allowed_exts=_IMAGE_EXTS, max_bytes=_IMAGE_MAX_BYTES, fallback_ext="png")
    return {"ok": True, **uploaded}


@router.post("/api/hifly/avatar/create-by-image")
async def hifly_avatar_create_by_image(body: HiflyAvatarCreateBody):
    payload = {
        "title": _safe_title(body.title),
        "file_id": body.file_id.strip(),
        "model": 1 if int(body.model or 0) == 1 else 2,
        "aigc_flag": int(body.aigc_flag or 0),
    }
    data = await _post("/api/v2/hifly/avatar/create_by_image", body.token, payload)
    task_id = str(data.get("task_id") or "").strip()
    if not task_id:
        raise HTTPException(status_code=502, detail="HiFly 未返回 task_id")
    return {"ok": True, "task_id": task_id, "raw": data}


@router.post("/api/hifly/avatar/create-by-video")
async def hifly_avatar_create_by_video(body: HiflyAvatarVideoCreateBody):
    payload = {
        "title": _safe_title(body.title),
        "file_id": body.file_id.strip(),
        "aigc_flag": int(body.aigc_flag or 0),
    }
    data = await _post("/api/v2/hifly/avatar/create_by_video", body.token, payload)
    task_id = str(data.get("task_id") or "").strip()
    if not task_id:
        raise HTTPException(status_code=502, detail="HiFly 未返回 task_id")
    return {"ok": True, "task_id": task_id, "raw": data}


@router.post("/api/hifly/avatar/create-by-image-upload")
async def hifly_avatar_create_by_image_upload(
    token: Optional[str] = Form(None),
    title: str = Form("未命名"),
    model: int = Form(2),
    aigc_flag: int = Form(0),
    file: UploadFile = File(...),
):
    uploaded = await _upload_file_to_hifly(token, file, allowed_exts=_IMAGE_EXTS, max_bytes=_IMAGE_MAX_BYTES, fallback_ext="png")
    data = await hifly_avatar_create_by_image(
        HiflyAvatarCreateBody(
            token=token,
            title=title,
            file_id=uploaded["file_id"],
            model=model,
            aigc_flag=aigc_flag,
        )
    )
    return {"ok": True, "file_id": uploaded["file_id"], **data}


@router.post("/api/hifly/avatar/create-by-video-upload")
async def hifly_avatar_create_by_video_upload(
    token: Optional[str] = Form(None),
    title: str = Form("未命名"),
    aigc_flag: int = Form(0),
    file: UploadFile = File(...),
):
    uploaded = await _upload_file_to_hifly(token, file, allowed_exts=_VIDEO_EXTS, max_bytes=_VIDEO_MAX_BYTES, fallback_ext="mp4")
    data = await hifly_avatar_create_by_video(
        HiflyAvatarVideoCreateBody(
            token=token,
            title=title,
            file_id=uploaded["file_id"],
            aigc_flag=aigc_flag,
        )
    )
    return {"ok": True, "file_id": uploaded["file_id"], **data}


@router.post("/api/hifly/avatar/task")
async def hifly_avatar_task(body: HiflyTaskBody):
    payload = await _get("/api/v2/hifly/avatar/task", body.token, {"task_id": body.task_id.strip()})
    status = int(payload.get("status") or 0)
    avatar_id = str(payload.get("avatar") or "").strip()
    return {
        "ok": status != 4,
        "task_id": body.task_id,
        "status": status,
        "status_text": _status_text(status),
        "avatar": avatar_id,
        "title": payload.get("title") or "",
        "message": payload.get("message") or "",
        "raw": payload,
    }


@router.post("/api/hifly/voice/create")
async def hifly_voice_create(body: HiflyVoiceCreateBody):
    payload = {
        "title": _safe_title(body.title),
        "voice_type": int(body.voice_type or 8),
        "file_id": body.file_id.strip(),
        "languages": (body.languages or "zh").strip() or "zh",
    }
    data = await _post("/api/v2/hifly/voice/create", body.token, payload)
    task_id = str(data.get("task_id") or "").strip()
    if not task_id:
        raise HTTPException(status_code=502, detail="HiFly 未返回 task_id")
    return {"ok": True, "task_id": task_id, "raw": data}


@router.post("/api/hifly/voice/create-upload")
async def hifly_voice_create_upload(
    token: Optional[str] = Form(None),
    title: str = Form(...),
    voice_type: int = Form(8),
    languages: str = Form("zh"),
    file: UploadFile = File(...),
):
    uploaded = await _upload_file_to_hifly(token, file, allowed_exts=_AUDIO_EXTS, max_bytes=_AUDIO_MAX_BYTES, fallback_ext="mp3")
    data = await hifly_voice_create(
        HiflyVoiceCreateBody(
            token=token,
            title=title,
            voice_type=voice_type,
            languages=languages,
            file_id=uploaded["file_id"],
        )
    )
    return {"ok": True, "file_id": uploaded["file_id"], **data}


@router.post("/api/hifly/voice/task")
async def hifly_voice_task(body: HiflyTaskBody):
    payload = await _get("/api/v2/hifly/voice/task", body.token, {"task_id": body.task_id.strip()})
    status = int(payload.get("status") or 0)
    voice_id = str(payload.get("voice") or payload.get("voice_id") or "").strip()
    demo_url = str(payload.get("demo_url") or payload.get("audio_url") or payload.get("preview_url") or "").strip()
    return {
        "ok": status != 4,
        "task_id": body.task_id,
        "status": status,
        "status_text": _status_text(status),
        "voice": voice_id,
        "demo_url": demo_url,
        "title": payload.get("title") or "",
        "message": payload.get("message") or "",
        "raw": payload,
    }
