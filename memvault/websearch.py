#!/usr/bin/env python3
"""memvault.websearch — 给网关用的联网搜索。

设计取舍：
  1. 只用标准库（urllib），不引第三方依赖 —— 和整个 memvault 一致。
  2. 有 key 走 API（tavily / bocha / zhipu），没 key 回落到 Bing HTML 抓取。
     实测：DuckDuckGo / SearXNG 在本机出口被 TLS 重置；百度返回反爬页（1.4KB）；
     Bing 能拿到 122KB 但结果相关性差。所以**真正可用的只有 API 那条路**，
     HTML 抓取只当"没配 key 时别报错"的兜底。
  3. 返回统一结构 [{title, url, snippet}]，调用方不需要知道引擎差异。
"""
import html
import json
import re
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")

# 只保留需要 key 的引擎。
# Bing/Google/百度 的 HTML 抓取实测在这台机器出口 IP 上全部失败
# （kiwi-mem 容器内实测也是 0 条），代码保留但不对外暴露。
ENGINES = {
    "tavily": "Tavily（需 key · 免费 1000 次/月）",
    "bocha": "博查（需 key）",
    "zhipu": "智谱（需 key）",
}


def _get(url, headers=None, timeout=15, data=None):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    req.add_header("user-agent", UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _clean(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


# ---------------- API 引擎 ----------------

def search_tavily(q, limit=5, key=""):
    if not key:
        raise ValueError("Tavily 需要 API key")
    body = json.dumps({"api_key": key, "query": q, "max_results": limit,
                       "search_depth": "basic"}).encode()
    raw = _get("https://api.tavily.com/search", data=body,
               headers={"content-type": "application/json"})
    d = json.loads(raw)
    return [{"title": r.get("title", ""), "url": r.get("url", ""),
             "snippet": (r.get("content") or "")[:400]}
            for r in (d.get("results") or [])][:limit]


def search_bocha(q, limit=5, key=""):
    if not key:
        raise ValueError("博查需要 API key")
    body = json.dumps({"query": q, "count": limit, "summary": True}).encode()
    raw = _get("https://api.bochaai.com/v1/web-search", data=body,
               headers={"content-type": "application/json",
                        "authorization": "Bearer " + key})
    d = json.loads(raw)
    pages = (((d.get("data") or {}).get("webPages") or {}).get("value")) or []
    return [{"title": p.get("name", ""), "url": p.get("url", ""),
             "snippet": (p.get("snippet") or p.get("summary") or "")[:400]}
            for p in pages][:limit]


def search_zhipu(q, limit=5, key=""):
    if not key:
        raise ValueError("智谱需要 API key")
    body = json.dumps({"search_query": q, "search_engine": "search_pro_jina",
                       "count": limit, "content_size": "medium"}).encode()
    raw = _get("https://open.bigmodel.cn/api/paas/v4/web_search", data=body,
               headers={"content-type": "application/json",
                        "authorization": "Bearer " + key})
    d = json.loads(raw)
    items = d.get("search_result") or d.get("data") or []
    return [{"title": i.get("title", ""), "url": i.get("link") or i.get("url", ""),
             "snippet": (i.get("content") or "")[:400]} for i in items][:limit]


# ---------------- HTML 兜底 ----------------

def search_bing(q, limit=5, key=""):
    """实测可用性一般：能拿到页面，但无 key 时 Bing 常返回弱相关结果。"""
    url = "https://www.bing.com/search?q=" + urllib.parse.quote(q)
    h = _get(url).decode("utf-8", "replace")
    out = []
    for blk in re.findall(r'<li class="b_algo".*?</li>', h, re.S):
        m = re.search(r'<h2[^>]*>.*?<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', blk, re.S)
        if not m:
            continue
        p = re.search(r"<p[^>]*>(.*?)</p>", blk, re.S)
        out.append({"title": _clean(m.group(2)),
                    "url": html.unescape(m.group(1)),
                    "snippet": _clean(p.group(1))[:400] if p else ""})
        if len(out) >= limit:
            break
    return out


_DISPATCH = {"tavily": search_tavily, "bocha": search_bocha,
             "zhipu": search_zhipu, "bing": search_bing}


def search(q, engine="tavily", limit=5, key=""):
    """统一入口。返回 (items, error)。"""
    fn = _DISPATCH.get(engine or "tavily", search_tavily)
    try:
        return fn(q, limit=limit, key=key), ""
    except Exception as e:
        return [], "%s: %s" % (engine, str(e)[:160])
