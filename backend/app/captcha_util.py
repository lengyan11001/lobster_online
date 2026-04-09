"""图片验证码：生成、存储、校验。验证码答案仅存服务端，传输依赖 HTTPS 加密。"""
import random
import secrets
import string
import threading
import time

_CAPTCHA_CHARS = "".join(c for c in string.ascii_uppercase + string.digits if c not in "0O1IL")
_CAPTCHA_STORE: dict[str, tuple[str, float]] = {}
_STORE_LOCK = threading.Lock()
_CAPTCHA_TTL = 300  # 5 分钟过期


def _clean_expired() -> None:
    now = time.time()
    with _STORE_LOCK:
        for cid in list(_CAPTCHA_STORE.keys()):
            if _CAPTCHA_STORE[cid][1] < now:
                del _CAPTCHA_STORE[cid]


def create_captcha(length: int = 4) -> tuple[str, str]:
    """生成验证码，返回 (captcha_id, image_base64)。image 为 data:image/svg+xml;base64,..."""
    _clean_expired()
    answer = "".join(random.choices(_CAPTCHA_CHARS, k=length))
    cid = secrets.token_urlsafe(16)
    with _STORE_LOCK:
        _CAPTCHA_STORE[cid] = (answer.upper(), time.time() + _CAPTCHA_TTL)
    w, h = 120, 44
    lines = []
    for _ in range(3):
        x1, y1 = random.randint(0, w), random.randint(0, h)
        x2, y2 = random.randint(0, w), random.randint(0, h)
        lines.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="#ccc" stroke-width="1"/>')
    text_x = 12
    chars_svg = []
    for i, c in enumerate(answer):
        y_offset = random.randint(-4, 4)
        chars_svg.append(
            f'<text x="{text_x + i * 26}" y="{28 + y_offset}" font-family="monospace" font-size="24" fill="#333">{c}</text>'
        )
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
        f'<rect width="100%" height="100%" fill="#f5f5f5"/>'
        + "".join(lines)
        + "".join(chars_svg)
        + "</svg>"
    )
    import base64
    b64 = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return cid, f"data:image/svg+xml;base64,{b64}"


def verify_captcha(captcha_id: str, answer: str) -> bool:
    if not captcha_id or not (answer or "").strip():
        return False
    key = captcha_id.strip()
    user_answer = (answer or "").strip().upper()
    with _STORE_LOCK:
        entry = _CAPTCHA_STORE.pop(key, None)
    if not entry:
        return False
    correct, expiry = entry
    if time.time() > expiry:
        return False
    return user_answer == correct
