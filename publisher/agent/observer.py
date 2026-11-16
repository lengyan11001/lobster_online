"""Browser observer: DOM summary + downscaled screenshot + page signature."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
from typing import Any

from .models import Observation

logger = logging.getLogger(__name__)

_JS_SUMMARY = r"""
(() => {
  const out = [];
  const sel = 'button,a,input,textarea,select,[role=button],[role=tab],[role=textbox],[contenteditable=true]';
  const nodes = Array.from(document.querySelectorAll(sel));
  for (const el of nodes) {
    if (out.length >= 80) break;
    const r = el.getBoundingClientRect();
    if (!r || r.width < 4 || r.height < 4) continue;
    const style = window.getComputedStyle(el);
    if (!style || style.visibility === 'hidden' || style.display === 'none' || Number(style.opacity || 1) === 0) continue;
    let text = '';
    try { text = String(el.innerText || el.value || el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.title || '').trim(); } catch (e) {}
    if (text.length > 80) text = text.slice(0, 80);
    out.push({ tag: el.tagName.toLowerCase(), role: el.getAttribute('role') || '', type: el.getAttribute('type') || '',
      text: text, placeholder: el.getAttribute('placeholder') || '', accept: el.getAttribute('accept') || '',
      disabled: !!el.disabled, x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height) });
  }
  return JSON.stringify(out);
})()
"""

def _downscale(raw: bytes) -> bytes:
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        longest = max(img.size)
        if longest > 1024:
            ratio = 1024.0 / longest
            img = img.resize((max(1, int(img.width * ratio)), max(1, int(img.height * ratio))))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=70)
        return buf.getvalue()
    except Exception:
        return raw


async def observe_browser(page: Any, *, platform: str, stage: str) -> Observation:
    nodes = []
    try:
        raw = await page.evaluate(_JS_SUMMARY)
        nodes = json.loads(raw) if isinstance(raw, str) else (raw or [])
    except Exception as exc:
        logger.debug("[agent] dom summary failed: %s", exc)
    shot_b64 = ""
    try:
        raw_shot = await page.screenshot(type="png", full_page=False)
        shot_b64 = base64.b64encode(_downscale(raw_shot)).decode("ascii")
    except Exception as exc:
        logger.debug("[agent] screenshot failed: %s", exc)
    text = ""
    try:
        text = await page.evaluate("() => (document.body && document.body.innerText || '').slice(0, 2000)")
    except Exception:
        pass
    sig_src = platform + "|" + stage + "|" + str(getattr(page, "url", "")) + "|" + "|".join(
        (str(n.get("role") or n.get("tag")) + ":" + str(n.get("text") or ""))[:40] for n in nodes[:40]
    )
    signature = "sig_" + hashlib.sha256(sig_src.encode("utf-8", errors="ignore")).hexdigest()[:16]
    return Observation(
        env="browser", stage=stage, url=str(getattr(page, "url", "") or ""), title="",
        nodes=nodes, visible_text=str(text or ""), screenshot_b64=shot_b64, signature=signature,
    )
