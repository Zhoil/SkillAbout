#!/usr/bin/env python3
"""Keyless discovery for public Chinese article and blog platforms.

The adapters deliberately use pages that can be opened without an account.
Sogou's public WeChat article index is queried directly; all platforms also
use a keyless web-search fallback with strict article URL allow-lists.  A
failure on one platform or search backend never aborts the whole digest.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
import html
import json
import re
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen


DEFAULT_PLATFORMS = ("weixin", "toutiao", "juejin", "csdn", "zhihu")
DEFAULT_QUERIES = (
    "人工智能 前沿技术 研究进展",
    "AI 工程 研发实践 新技术",
    "AI Agent Skill 技能 总结 推荐",
    "大模型 开发工具 技术博客",
)

PLATFORMS: dict[str, dict[str, Any]] = {
    "weixin": {
        "label": "微信公众号",
        "domains": ("mp.weixin.qq.com", "weixin.sogou.com"),
        "path": re.compile(r"/(?:s|link)(?:/|\?|$)"),
    },
    "toutiao": {
        "label": "今日头条",
        "domains": ("www.toutiao.com", "toutiao.com"),
        "path": re.compile(r"/(?:article|w)/"),
    },
    "juejin": {
        "label": "稀土掘金",
        "domains": ("juejin.cn",),
        "path": re.compile(r"/post/"),
    },
    "csdn": {
        "label": "CSDN",
        "domains": ("blog.csdn.net",),
        "path": re.compile(r"/article/details/"),
    },
    "zhihu": {
        "label": "知乎专栏",
        "domains": ("zhuanlan.zhihu.com",),
        "path": re.compile(r"/p/"),
    },
}

_TAG_RE = re.compile(r"<[^>]+>")
_BLOCK_RE = re.compile(r"<(?:script|style)\b[^>]*>.*?</(?:script|style)>", re.I | re.S)
_DDG_RESULT_RE = re.compile(
    r'class="result__a"[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>',
    re.I | re.S,
)
_DDG_SNIPPET_RE = re.compile(r'class="result__snippet"[^>]*>(?P<text>.*?)</a>', re.I | re.S)
_BING_BLOCK_RE = re.compile(r'<li\b[^>]*class="[^"]*b_algo[^"]*"[^>]*>(?P<body>.*?)</li>', re.I | re.S)
_BING_TITLE_RE = re.compile(
    r'<h2\b[^>]*>\s*<a\b[^>]*href="(?P<href>https?://[^"]+)"[^>]*>(?P<title>.*?)</a>',
    re.I | re.S,
)
_BING_SNIPPET_RE = re.compile(r'<p\b[^>]*>(?P<text>.*?)</p>', re.I | re.S)
_WECHAT_RESULT_RE = re.compile(
    r'<h3[^>]*>\s*<a[^>]+href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>.*?'
    r'<p[^>]+class="[^"]*txt-info[^"]*"[^>]*>(?P<summary>.*?)</p>',
    re.I | re.S,
)


def _clean(value: str) -> str:
    value = _BLOCK_RE.sub(" ", value or "")
    value = _TAG_RE.sub(" ", value)
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def _get_text(url: str, timeout: int = 15) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 Chrome/126 Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace")
    except OSError:
        return ""


def _unwrap_ddg(url: str) -> str:
    if "uddg=" not in url:
        return f"https:{url}" if url.startswith("//") else url
    try:
        parsed = urlparse(f"https:{url}" if url.startswith("//") else url)
        return parse_qs(parsed.query).get("uddg", [url])[0]
    except (ValueError, KeyError):
        return url


def _article_url(platform: str, url: str) -> bool:
    spec = PLATFORMS[platform]
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    domain = parsed.netloc.lower().split(":", 1)[0]
    if not any(domain == allowed or domain.endswith(f".{allowed}") for allowed in spec["domains"]):
        return False
    return bool(spec["path"].search(parsed.path + ("?" if parsed.query else "")))


def _publication_date(value: str, today: date | None = None) -> date | None:
    """Extract common Chinese/ISO dates from public search snippets."""
    current = today or date.today()
    clean = _clean(value)
    relative = re.search(r"(\d+)\s*(小时|天|周|个月|月)(?:之前|前)", clean)
    if relative:
        amount = int(relative.group(1))
        unit = relative.group(2)
        days = amount / 24 if unit == "小时" else amount * {"天": 1, "周": 7, "个月": 30, "月": 30}[unit]
        return current - timedelta(days=int(days))
    chinese = re.search(r"(20\d{2})年(\d{1,2})月(\d{1,2})日", clean)
    if chinese:
        try:
            return date(*(int(part) for part in chinese.groups()))
        except ValueError:
            return None
    iso = re.search(r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})", clean)
    if iso:
        try:
            return date(*(int(part) for part in iso.groups()))
        except ValueError:
            return None
    return None


def _result(platform: str, title: str, url: str, summary: str, rank: int) -> dict[str, Any]:
    published = _publication_date(summary)
    return {
        "candidate_id": f"cn-{platform}-{rank}",
        "title": _clean(title),
        "source": platform,
        "source_label": PLATFORMS[platform]["label"],
        "url": html.unescape(url),
        "published_at": published.isoformat() if published else None,
        "summary": _clean(summary)[:500],
        "engagement": {},
        "relevance_score": max(0.58, 0.76 - rank * 0.015),
        "cluster": None,
        "access": "public-no-login",
    }


def _search_wechat(query: str, limit: int) -> list[dict[str, Any]]:
    url = "https://weixin.sogou.com/weixin?" + urlencode({"type": 2, "query": query})
    page = _get_text(url)
    results: list[dict[str, Any]] = []
    for match in _WECHAT_RESULT_RE.finditer(page):
        target = html.unescape(match.group("href"))
        if target.startswith("/"):
            target = "https://weixin.sogou.com" + target
        if not _article_url("weixin", target):
            continue
        results.append(_result("weixin", match.group("title"), target, match.group("summary"), len(results)))
        if len(results) >= limit:
            break
    return results


def _search_ddg(platform: str, query: str, limit: int, since: date | None = None) -> list[dict[str, Any]]:
    domains = " OR ".join(f"site:{domain}" for domain in PLATFORMS[platform]["domains"])
    freshness = f" after:{since.isoformat()}" if since else ""
    url = "https://html.duckduckgo.com/html/?" + urlencode({"q": f"{query} ({domains}){freshness}"})
    page = _get_text(url)
    matches = list(_DDG_RESULT_RE.finditer(page))
    results: list[dict[str, Any]] = []
    for index, match in enumerate(matches):
        target = _unwrap_ddg(html.unescape(match.group("href")))
        if not _article_url(platform, target):
            continue
        boundary = matches[index + 1].start() if index + 1 < len(matches) else len(page)
        snippet_match = _DDG_SNIPPET_RE.search(page[match.end():boundary])
        summary = snippet_match.group("text") if snippet_match else ""
        published = _publication_date(summary)
        if since and published and published < since:
            continue
        results.append(_result(platform, match.group("title"), target, summary, len(results)))
        if len(results) >= limit:
            break
    return results


def _search_bing(platform: str, query: str, limit: int, since: date | None = None) -> list[dict[str, Any]]:
    """China-accessible fallback when DDG/Startpage challenge or time out."""
    site_queries = " OR ".join(f"site:{domain}" for domain in PLATFORMS[platform]["domains"])
    freshness = f" after:{since.isoformat()}" if since else ""
    url = "https://cn.bing.com/search?" + urlencode({"q": f"{query} ({site_queries}){freshness}"})
    page = _get_text(url)
    results: list[dict[str, Any]] = []
    for block_match in _BING_BLOCK_RE.finditer(page):
        body = block_match.group("body")
        title_match = _BING_TITLE_RE.search(body)
        if not title_match:
            continue
        target = html.unescape(title_match.group("href"))
        if not _article_url(platform, target):
            continue
        snippet_match = _BING_SNIPPET_RE.search(body[title_match.end():])
        summary = snippet_match.group("text") if snippet_match else ""
        published = _publication_date(summary)
        if since and published and published < since:
            continue
        results.append(_result(platform, title_match.group("title"), target, summary, len(results)))
        if len(results) >= limit:
            break
    return results


def search_platform(
    platform: str,
    query: str,
    limit: int = 5,
    since: date | None = None,
) -> list[dict[str, Any]]:
    """Search one public platform; direct adapters are supplemented by DDG."""
    if platform not in PLATFORMS:
        return []
    results = _search_wechat(query, limit) if platform == "weixin" else []
    seen = {item["url"] for item in results}
    web_results = _search_ddg(platform, query, limit, since)
    if not web_results:
        web_results = _search_bing(platform, query, limit, since)
    for item in web_results:
        if item["url"] not in seen:
            results.append(item)
            seen.add(item["url"])
        if len(results) >= limit:
            break
    return results


def search_domestic_platforms(
    topic: str,
    *,
    platforms: list[str] | tuple[str, ...] = DEFAULT_PLATFORMS,
    queries: list[str] | tuple[str, ...] = DEFAULT_QUERIES,
    per_platform: int = 6,
    days: int = 30,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Fan out public Chinese searches and return merged results + statuses."""
    selected = [name.strip().lower() for name in platforms if name.strip().lower() in PLATFORMS]
    search_queries = [topic] + [query for query in queries if query and query != topic]
    since = datetime.now(timezone.utc).date() - timedelta(days=max(1, days))
    items: list[dict[str, Any]] = []
    status = {name: "no-results" for name in selected}
    max_workers = min(10, max(1, len(selected) * 2))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(search_platform, platform, query, per_platform, since): platform
            for platform in selected
            for query in search_queries
        }
        for future in as_completed(futures):
            platform = futures[future]
            try:
                found = future.result()
            except Exception:
                status[platform] = "partial"
                continue
            if found:
                status[platform] = "ok"
                items.extend(found)
    return items, status


if __name__ == "__main__":
    found, outcomes = search_domestic_platforms("AI Agent Skill")
    print(json.dumps({"generated_at": datetime.now(timezone.utc).isoformat(), "source_status": outcomes, "results": found}, ensure_ascii=False, indent=2))
