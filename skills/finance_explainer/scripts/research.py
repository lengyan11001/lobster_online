#!/usr/bin/env python3
"""事实研究小工具：检索（Exa / Tavily / Serper）+ 抓原文（Firecrawl），给事实清单 facts.md 找一手来源。

用法：
  python3 research.py search "<查询>" [--engine exa|tavily|serper] [--n 8] [--since 2025-01-01]
  python3 research.py scrape <url> [--out page.md]          # Firecrawl 抓成干净 markdown（做「原文标黄」卡片用）
  python3 research.py multi "<查询>"                          # 三个引擎各查一遍，按域名去重合并

key 从 ~/.config/xiaolin-explainer/keys.env 读，不写进项目。输出 JSON 到 stdout：[{title, url, date, snippet, engine}]。
注意：检索结果是线索不是证据。写进 facts.md 的每条数字都要打开原文核对，并写明日期和出处。
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
                a, _, b = line.partition("="); k.setdefault(a.strip(), b.strip())
    return k


def post(url, body, headers):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", **headers}, method="POST")
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read())


def exa(q, K, n, since):
    body = {"query": q, "numResults": n, "contents": {"text": {"maxCharacters": 600}}}
    if since:
        body["startPublishedDate"] = since + "T00:00:00.000Z"
    d = post("https://api.exa.ai/search", body, {"x-api-key": K["EXA_API_KEY"]})
    return [{"title": r.get("title"), "url": r.get("url"), "date": (r.get("publishedDate") or "")[:10], "snippet": (r.get("text") or "")[:400], "engine": "exa"} for r in d.get("results", [])]


def tavily(q, K, n, since):
    d = post("https://api.tavily.com/search", {"api_key": K["TAVILY_API_KEY"], "query": q, "max_results": n, "search_depth": "advanced"}, {})
    return [{"title": r.get("title"), "url": r.get("url"), "date": r.get("published_date", ""), "snippet": (r.get("content") or "")[:400], "engine": "tavily"} for r in d.get("results", [])]


def serper(q, K, n, since):
    d = post("https://google.serper.dev/search", {"q": q, "num": n}, {"X-API-KEY": K["SERPER_API_KEY"]})
    return [{"title": r.get("title"), "url": r.get("link"), "date": r.get("date", ""), "snippet": r.get("snippet", ""), "engine": "serper"} for r in d.get("organic", [])]


def scrape(url, K):
    d = post("https://api.firecrawl.dev/v1/scrape", {"url": url, "formats": ["markdown"], "onlyMainContent": True}, {"Authorization": f"Bearer {K['FIRECRAWL_API_KEY']}"})
    return (d.get("data") or {}).get("markdown", "")


def main():
    K = keys(); cmd = sys.argv[1]
    if cmd == "scrape":
        md = scrape(sys.argv[2], K)
        if arg("--out"):
            Path(arg("--out")).write_text(md); print(f"{len(md)} 字 → {arg('--out')}")
        else:
            print(md)
        return
    q, n, since = sys.argv[2], int(arg("--n", "8")), arg("--since")
    engines = {"exa": exa, "tavily": tavily, "serper": serper}
    use = list(engines) if cmd == "multi" else [arg("--engine", "exa")]
    out, seen = [], set()
    for e in use:
        need = {"exa": "EXA_API_KEY", "tavily": "TAVILY_API_KEY", "serper": "SERPER_API_KEY"}[e]
        if not K.get(need):
            print(f"跳过 {e}：没配 {need}", file=sys.stderr); continue
        try:
            for r in engines[e](q, K, n, since):
                dom = urllib.parse.urlparse(r["url"] or "").netloc + urllib.parse.urlparse(r["url"] or "").path
                if dom not in seen:
                    seen.add(dom); out.append(r)
        except Exception as ex:
            print(f"{e} 失败：{ex}", file=sys.stderr)
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
