"""Parse short visible tags from an asset-understanding reply."""
import json
import re


def parse_ai_tag_text(text: str) -> str:
    """Return 2 or 3 comma-separated tags, or raise ValueError."""
    raw = str(text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", raw, re.IGNORECASE | re.DOTALL)
    if fenced:
        raw = fenced.group(1).strip()
    data = _load_json_value(raw)
    pieces = _tag_pieces(data, raw)
    seen = []
    for part in pieces:
        tag = re.sub(r"\s+", " ", str(part or "")).strip().strip("\"'`")
        tag = tag.strip("[]{}")
        if not tag or tag.lower() in {"tags", "json"}:
            continue
        tag = tag[:12]
        if tag not in seen:
            seen.append(tag)
        if len(seen) >= 3:
            break
    if len(seen) < 2:
        raise ValueError("AI \u6ca1\u6709\u8fd4\u56de\u8db3\u591f\u7684\u6807\u7b7e")
    return ",".join(seen)


def _load_json_value(raw: str):
    try:
        return json.loads(raw)
    except Exception:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except Exception:
            return None


def _tag_pieces(data, raw: str):
    tags = None
    if isinstance(data, dict):
        tags = data.get("tags")
    elif isinstance(data, list):
        tags = data
    if isinstance(tags, str):
        return re.split(r"[,，;；\n]+", tags)
    if isinstance(tags, list):
        pieces = []
        for item in tags:
            pieces.extend(re.split(r"[,，;；]+", str(item)))
        return pieces
    return re.split(r"[,，;；\n]+", raw)
