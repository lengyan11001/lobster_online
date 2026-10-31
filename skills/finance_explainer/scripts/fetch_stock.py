#!/usr/bin/env python3
"""从免费素材库取图/视频，并把出处写进 credits.json（片尾致谢与合规用）。

用法：
  python3 fetch_stock.py <素材id> "<英文关键词>" <输出目录> [--kind photo|video] [--source unsplash|pexels|pixabay|wikimedia] [--n 3]

- key 从 ~/.config/xiaolin-explainer/keys.env 读（UNSPLASH_ACCESS_KEY / PEXELS_API_KEY / PIXABAY_API_KEY），不写进项目。
- 不指定 --source 时按「视频：pexels → pixabay；图片：unsplash → pexels → wikimedia」依次尝试，有 key 才用。
- Unsplash 规定：使用时要请求 download_location 端点，并署名摄影师 + Unsplash 链接；本脚本自动完成。
- Wikimedia 只取 CC0 / CC BY / CC BY-SA / 公有领域，协议名写进 credits。
- 输出：<输出目录>/<素材id>.<ext>，并在 <输出目录>/credits.json 里追加一条 {id, source, author, url, license}。
"""
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path


def arg(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def keys():
    f = Path.home() / ".config/xiaolin-explainer/keys.env"
    k = dict(os.environ)
    if f.exists():
        for line in f.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                a, _, b = line.partition("=")
                k.setdefault(a.strip(), b.strip())
    return k


def get(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": "xiaolin-explainer/1.0", **(headers or {})})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def unsplash(q, K, n):
    if not K.get("UNSPLASH_ACCESS_KEY"):
        return []
    h = {"Authorization": f"Client-ID {K['UNSPLASH_ACCESS_KEY']}"}
    d = json.loads(get(f"https://api.unsplash.com/search/photos?query={urllib.parse.quote(q)}&per_page={n}&orientation=landscape", h))
    out = []
    for p in d.get("results", []):
        out.append({"source": "Unsplash", "kind": "photo", "url": p["urls"]["full"] + "&w=2400", "page": p["links"]["html"],
                    "author": p["user"]["name"], "license": "Unsplash License", "ping": (p["links"]["download_location"], h)})
    return out


def pexels(q, K, n, kind):
    if not K.get("PEXELS_API_KEY"):
        return []
    h = {"Authorization": K["PEXELS_API_KEY"]}
    if kind == "video":
        d = json.loads(get(f"https://api.pexels.com/videos/search?query={urllib.parse.quote(q)}&per_page={n}&orientation=landscape", h))
        out = []
        for v in d.get("videos", []):
            files = sorted([f for f in v["video_files"] if (f.get("width") or 0) >= 1280], key=lambda f: f["width"])
            if files:
                out.append({"source": "Pexels", "kind": "video", "url": files[0]["link"], "page": v["url"], "author": v["user"]["name"], "license": "Pexels License"})
        return out
    d = json.loads(get(f"https://api.pexels.com/v1/search?query={urllib.parse.quote(q)}&per_page={n}&orientation=landscape", h))
    return [{"source": "Pexels", "kind": "photo", "url": p["src"]["large2x"], "page": p["url"], "author": p["photographer"], "license": "Pexels License"} for p in d.get("photos", [])]


def pixabay(q, K, n, kind):
    if not K.get("PIXABAY_API_KEY"):
        return []
    base = "https://pixabay.com/api/videos/" if kind == "video" else "https://pixabay.com/api/"
    d = json.loads(get(f"{base}?key={K['PIXABAY_API_KEY']}&q={urllib.parse.quote(q)}&per_page={max(3, n)}"))
    out = []
    for h in d.get("hits", []):
        url = (h.get("videos", {}).get("large") or h.get("videos", {}).get("medium") or {}).get("url") if kind == "video" else h.get("largeImageURL")
        if url:
            out.append({"source": "Pixabay", "kind": kind, "url": url, "page": h["pageURL"], "author": h.get("user", ""), "license": "Pixabay Content License"})
    return out


OK_LIC = ("cc0", "cc by", "cc-by", "public domain", "pd")


def wikimedia(q, n):
    api = "https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode({
        "action": "query", "format": "json", "generator": "search", "gsrsearch": f"filetype:bitmap {q}", "gsrnamespace": 6, "gsrlimit": n * 3,
        "prop": "imageinfo", "iiprop": "url|extmetadata", "iiurlwidth": 2400})
    d = json.loads(get(api))
    out = []
    for p in (d.get("query", {}).get("pages", {}) or {}).values():
        ii = (p.get("imageinfo") or [{}])[0]; md = ii.get("extmetadata", {})
        lic = (md.get("LicenseShortName", {}).get("value") or "").strip()
        if not lic or not any(lic.lower().startswith(x) for x in OK_LIC):
            continue
        author = md.get("Artist", {}).get("value", "")
        import re
        author = re.sub(r"<[^>]+>", "", author).strip()[:80]
        out.append({"source": "Wikimedia Commons", "kind": "photo", "url": ii.get("thumburl") or ii.get("url"), "page": ii.get("descriptionurl"), "author": author, "license": lic})
        if len(out) >= n:
            break
    return out


def main():
    sid, q, outdir = sys.argv[1], sys.argv[2], Path(sys.argv[3])
    kind, src, n = arg("--kind", "photo"), arg("--source"), int(arg("--n", "3"))
    K = keys(); outdir.mkdir(parents=True, exist_ok=True)
    order = [src] if src else (["pexels", "pixabay"] if kind == "video" else ["unsplash", "pexels", "wikimedia"])
    cands = []
    for s in order:
        try:
            cands = {"unsplash": lambda: unsplash(q, K, n), "pexels": lambda: pexels(q, K, n, kind), "pixabay": lambda: pixabay(q, K, n, kind),
                     "wikimedia": lambda: wikimedia(q, n)}[s]()
        except Exception as e:
            print(f"{s} 失败：{e}", file=sys.stderr); cands = []
        if cands:
            break
    if not cands:
        sys.exit(f"没找到「{q}」的{kind}（试过 {order}）")
    c = cands[int(arg("--pick", "0")) % len(cands)]
    ext = ".mp4" if c["kind"] == "video" else ".jpg"
    dst = outdir / f"{sid}{ext}"
    dst.write_bytes(get(c["url"]))
    if c.get("ping"):
        try: get(*c["ping"])
        except Exception: pass
    cred_f = outdir / "credits.json"
    cred = json.loads(cred_f.read_text()) if cred_f.exists() else []
    cred = [x for x in cred if x["id"] != sid] + [{"id": sid, "source": c["source"], "author": c["author"], "url": c["page"], "license": c["license"]}]
    cred_f.write_text(json.dumps(cred, ensure_ascii=False, indent=1))
    print(f"{dst}  ←  {c['source']} · {c['author']} · {c['license']}  ({len(cands)} 个候选，--pick 换一个)")


if __name__ == "__main__":
    main()
