#!/usr/bin/env python3
"""Build a push-ready digest without sending it."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any
from urllib.parse import urljoin
from urllib.request import Request, urlopen


STAR_HISTORY_URL = "https://www.star-history.com/"


def positive_limit(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"limits.{name} must be a non-negative integer")
    return value


def load_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    limits = config.get("limits", {})
    for name in ("last30days", "weekly", "all_time"):
        positive_limit(limits.get(name), name)
    return config


def strings_from_json(value: Any) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    seen: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            title = next((node.get(k) for k in ("title", "name", "topic", "headline") if isinstance(node.get(k), str)), None)
            url = next((node.get(k) for k in ("url", "link", "permalink", "html_url") if isinstance(node.get(k), str)), "")
            if title and title not in seen:
                seen.add(title)
                found.append({"title": title.strip(), "url": url.strip()})
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)
    return found


def parse_last30days(path: Path) -> list[dict[str, str]]:
    text = path.read_text(encoding="utf-8")
    try:
        document = json.loads(text)
        # The stable agent profile exposes ranked, link-bearing rows here.
        # Prefer them over cluster summaries, which intentionally omit URLs.
        results = document.get("results") if isinstance(document, dict) else None
        parsed = []
        if isinstance(results, list):
            for row in results:
                if not isinstance(row, dict):
                    continue
                title, url = row.get("title"), row.get("url")
                if isinstance(title, str) and isinstance(url, str) and url:
                    parsed.append({"title": title.strip(), "url": url.strip()})
        if not parsed:
            parsed = strings_from_json(document)
        if parsed:
            return parsed
    except json.JSONDecodeError:
        pass
    items: list[dict[str, str]] = []
    seen: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        match = re.match(r"(?:\d+[.)]|[-*])\s+(?:\*\*)?(.+?)(?:\*\*)?(?:\s+-\s+|$)", line)
        if not match:
            continue
        title = re.sub(r"\[([^]]+)]\([^)]+\)", r"\1", match.group(1)).strip(" *")
        link = re.search(r"\[[^]]+]\((https?://[^)]+)\)", line)
        if title and title not in seen:
            seen.add(title)
            items.append({"title": title, "url": link.group(1) if link else ""})
    if not items:
        raise ValueError("No ranked items found in last30days artifact")
    return items


def fetch_html() -> str:
    request = Request(STAR_HISTORY_URL, headers={"User-Agent": "Mozilla/5.0 ai-hotspot-digest/1.0"})
    with urlopen(request, timeout=30) as response:
        page = response.read().decode("utf-8", "replace")
    # Weekly is server-rendered. The All-time tab's ranked JSON is bundled in
    # the current SPA asset, so include that asset in the parse corpus.
    asset = re.search(r'<script[^>]+src="([^"]*index-[^"]+\.js)"', page, re.I)
    if asset:
        asset_request = Request(urljoin(STAR_HISTORY_URL, asset.group(1)), headers=request.headers)
        with urlopen(asset_request, timeout=60) as response:
            page += "\n" + response.read().decode("utf-8", "replace")
    return page


def parse_star_history(page: str) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    # Weekly is rendered between the two tab labels as relative repo links.
    # All-time is serialized in the SPA bundle as name/stars_total/rank rows.
    normalized = page.replace("&amp;", "&")
    weekly_pos = re.search(r"weekly", normalized, re.I)
    all_pos = re.search(r"all[\s_-]*time", normalized, re.I)
    if not weekly_pos or not all_pos:
        raise ValueError("Star History Weekly or All-time heading not found")
    weekly_chunk = normalized[weekly_pos.start():]
    next_list_end = weekly_chunk.find("</ol>")
    if next_list_end >= 0:
        weekly_chunk = weekly_chunk[:next_list_end]
    weekly = []
    seen: set[str] = set()
    for row in re.findall(r'<li class="relative group">.*?</li>', weekly_chunk, re.S):
        repo_match = re.search(r'href="/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)"', row)
        rank_match = re.search(r'<span class="text-xs text-gray-400[^>]*">(\d+)</span>', row)
        delta_match = re.search(r'title="(Up|Down)\s+(\d+)"', row, re.I)
        stars_match = re.search(r'<span class="text-xs shrink-0 accent-text">\+(\d[\d,]*)</span>', row)
        if not repo_match or not rank_match or not stars_match:
            continue
        repo = repo_match.group(1)
        if repo in seen:
            continue
        seen.add(repo)
        trend = "–"
        if delta_match:
            trend = "▲" if delta_match.group(1).lower() == "up" else "▼"
        weekly.append({
            "title": repo,
            "url": f"https://github.com/{repo}",
            "rank": rank_match.group(1),
            "trend": trend,
            "stars_delta": stars_match.group(1).replace(",", ""),
        })
    ranked = re.findall(
        r'\{"name":"([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)","stars_total":(\d+),"rank":(\d+)\}',
        normalized,
    )
    ranked.sort(key=lambda row: int(row[2]))
    all_time = [
        {"title": repo, "url": f"https://github.com/{repo}", "stars": stars}
        for repo, stars, _rank in ranked
    ]
    if not weekly or not all_time:
        raise ValueError("Star History leaderboard metrics could not be parsed")
    return weekly, all_time


def load_annotations(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("annotations file must be a JSON object keyed by source URL")
    return {str(key): str(value).strip() for key, value in document.items() if str(value).strip()}


def render_recent(items: list[dict[str, str]], limit: int, annotations: dict[str, str]) -> str:
    lines = ["【近30天 AI 热点】"]
    for index, item in enumerate(items[:limit], 1):
        source_url = item.get("url", "")
        description = annotations.get(source_url, "")
        if not description:
            raise ValueError(f"Missing Chinese description for hotspot URL: {source_url}")
        lines.extend([
            f"{index}. {item['title']} ：{description}",
            f"   🌐 {source_url}",
        ])
    return "\n".join(lines) if limit and items else ""


def render_weekly(items: list[dict[str, str]], limit: int) -> str:
    lines = ["【Star History Weekly】"]
    for index, item in enumerate(items[:limit], 1):
        lines.extend([
            f"{index}. {item['trend']} {item['title']} +{int(item['stars_delta']):,}",
            f"   🔎 {item['url']}",
        ])
    return "\n".join(lines) if limit and items else ""


def render_all_time(items: list[dict[str, str]], limit: int) -> str:
    lines = ["【Star History All-time】"]
    for index, item in enumerate(items[:limit], 1):
        lines.extend([
            f"{index}. ：{item['title']} ：{int(item['stars']):,} 🌟",
            f"   🔎 {item['url']}",
        ])
    return "\n".join(lines) if limit and items else ""


GMT_PLUS_8 = timezone(timedelta(hours=8))


def format_gmt8_timestamp(value: datetime | None = None) -> str:
    current = value or datetime.now(timezone.utc)
    return current.astimezone(GMT_PLUS_8).strftime("🕒 %Y-%m-%d %H:%M:%S GMT+8")


def render_message(sections: list[str], generated_at: datetime | None = None) -> str:
    return format_gmt8_timestamp(generated_at) + "\n\nAI 热点与开源趋势汇总\n\n" + "\n\n".join(sections)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--last30days-file", required=True, type=Path)
    parser.add_argument("--annotations-file", type=Path)
    parser.add_argument("--star-history-html", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        config = load_config(args.config)
        limits = config["limits"]
        recent = parse_last30days(args.last30days_file)
        annotations = load_annotations(args.annotations_file)
        page = args.star_history_html.read_text(encoding="utf-8") if args.star_history_html else fetch_html()
        weekly, all_time = parse_star_history(page)
        sections = [section for section in (
            render_recent(recent, limits["last30days"], annotations),
            render_weekly(weekly, limits["weekly"]),
            render_all_time(all_time, limits["all_time"]),
        ) if section]
        if not sections:
            raise ValueError("All configured limits are zero; message would be empty")
        message = render_message(sections)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(message, encoding="utf-8")
        print(json.dumps({
            "output": str(args.output.resolve()),
            "counts": {
                "last30days": min(len(recent), limits["last30days"]),
                "weekly": min(len(weekly), limits["weekly"]),
                "all_time": min(len(all_time), limits["all_time"]),
            },
        }, ensure_ascii=False))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
