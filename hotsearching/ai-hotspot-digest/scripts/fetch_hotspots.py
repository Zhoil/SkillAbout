#!/usr/bin/env python3
"""Invoke the last30days engine and save its JSON output to a file.

Reads search configuration from the digest config's ``last30days`` section so
topic, days, depth, source list, and subreddit hints are all driven by one
config file rather than hard-coded in shell scripts or SKILL.md.

Usage::

    python3 scripts/fetch_hotspots.py \\
        --config <digest-config.json> \\
        --skill-dir <path-to-last30days-dir> \\
        --output <output.json>

The ``last30days`` section of the config supports:

    topic               Search topic / keyword string (quality-focused AI/R&D default)
    days                Lookback window in days (default: 30)
    depth               Retrieval depth: default | quick | deep (default: default)
    search              Comma-separated source names (default: engine default)
    subreddits          Comma-separated subreddit names without r/ prefix
    dedicated_subreddits  Comma-separated entity-home subreddits (full pull, no relevance floor)
    x_handle            X/Twitter handle for targeted search
    domestic           Public Chinese platform search configuration
    quality            Quality threshold, keywords, and source-diversity limits
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from domestic_search import DEFAULT_PLATFORMS, DEFAULT_QUERIES, search_domestic_platforms


DEFAULT_TOPIC = "AI frontier research developer tools agent skills engineering practices"
DEFAULT_DAYS = 30
DEFAULT_DEPTH = "default"
DEFAULT_QUALITY_KEYWORDS = (
    "ai", "人工智能", "大模型", "llm", "agent", "智能体", "skill", "技能",
    "前沿", "研究", "论文", "技术", "工程", "研发", "开源", "框架", "模型",
    "实践", "复盘", "原理", "架构", "评测", "benchmark", "tutorial", "workflow",
)
DEFAULT_BLOCKED_KEYWORDS = (
    "招聘", "培训班", "招生", "优惠券", "限时优惠", "网赚", "副业", "代写",
    "震惊", "必看", "速领", "彩票", "娱乐八卦",
)
TRACKING_QUERY_KEYS = {
    "from", "source", "spm", "utm_source", "utm_medium", "utm_campaign",
    "utm_term", "utm_content", "share_token", "timestamp",
}


def load_last30days_config(config_path: Path) -> dict[str, Any]:
    """Read the ``last30days`` section from the digest config."""
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    section = raw.get("last30days", {})
    if not isinstance(section, dict):
        raise ValueError("config.last30days must be a JSON object")
    return section


def build_command(
    skill_dir: Path,
    output_path: Path,
    section: dict[str, Any],
) -> list[str]:
    """Build the last30days CLI command from the config section."""
    topic = str(section.get("topic") or "").strip() or DEFAULT_TOPIC
    days = section.get("days")
    try:
        days = int(days) if days is not None else DEFAULT_DAYS
        if days <= 0:
            raise ValueError("days must be positive")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"last30days.days: {exc}") from exc

    depth_raw = str(section.get("depth") or "").strip().lower() or DEFAULT_DEPTH
    if depth_raw not in ("default", "quick", "deep"):
        raise ValueError(
            f"last30days.depth must be one of default/quick/deep, got {depth_raw!r}"
        )

    engine = skill_dir / "scripts" / "last30days.py"
    if not engine.exists():
        raise FileNotFoundError(
            f"last30days engine not found at {engine}. "
            "Set last30days_skill_dir to the directory containing scripts/last30days.py."
        )

    cmd = [
        sys.executable,
        str(engine),
        topic,
        "--emit=json",
        f"--days={days}",
        f"--save-dir={output_path.parent}",
    ]

    if section.get("quality_plan", True):
        cmd.append("--plan=" + json.dumps(build_quality_plan(topic, section), ensure_ascii=False))

    if depth_raw == "quick":
        cmd.append("--quick")
    elif depth_raw == "deep":
        cmd.append("--deep")

    search = str(section.get("search") or "").strip()
    if search:
        cmd.append(f"--search={search}")

    subreddits = str(section.get("subreddits") or "").strip()
    if subreddits:
        cmd.append(f"--subreddits={subreddits}")

    dedicated = str(section.get("dedicated_subreddits") or "").strip()
    if dedicated:
        cmd.append(f"--dedicated-subreddits={dedicated}")

    x_handle = str(section.get("x_handle") or "").strip().lstrip("@")
    if x_handle:
        cmd.append(f"--x-handle={x_handle}")

    return cmd


def build_quality_plan(topic: str, section: dict[str, Any]) -> dict[str, Any]:
    """Create a repeatable, quality-oriented multi-angle plan for every run."""
    configured = section.get("query_groups")
    groups = configured if isinstance(configured, list) and configured else [
        {"label": "frontier", "query": f"{topic} frontier research new technology", "question": "哪些 AI 前沿研究和新技术近期真正取得了进展？"},
        {"label": "engineering", "query": f"{topic} engineering practice architecture", "question": "有哪些可复用的 AI 工程实践、架构和技术复盘？"},
        {"label": "skills", "query": f"{topic} agent skill workflow tutorial", "question": "有哪些高质量的 Agent Skill、工作流总结与工具推荐？"},
        {"label": "development", "query": f"{topic} model release open source evaluation", "question": "AI 模型、开源项目和开发工具近期有哪些重要发展？"},
    ]
    requested = [part.strip().lower() for part in str(section.get("search") or "").split(",") if part.strip()]
    sources = requested or [
        "reddit", "x", "youtube", "tiktok", "instagram", "hackernews",
        "polymarket", "github", "arxiv", "grounding",
    ]
    subqueries = []
    for index, group in enumerate(groups[:4]):
        if not isinstance(group, dict):
            continue
        query = str(group.get("query") or "").strip()
        if not query:
            continue
        subqueries.append({
            "label": str(group.get("label") or f"angle-{index + 1}"),
            "search_query": query,
            "ranking_query": str(group.get("question") or "筛选有事实、有技术细节、可复用且非营销的文章和讨论。"),
            "sources": sources,
            "weight": 1.0 if index == 0 else 0.82 - index * 0.08,
        })
    return {
        "intent": "concept",
        "freshness_mode": "balanced_recent",
        "cluster_mode": "story",
        "subqueries": subqueries,
    }


def canonical_url(value: str) -> str:
    """Normalize URLs for cross-platform deduplication without losing identity."""
    try:
        parts = urlsplit(value.strip())
    except ValueError:
        return value.strip()
    query = [
        (key, item)
        for key, item in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in TRACKING_QUERY_KEYS and not key.lower().startswith("utm_")
    ]
    path = re.sub(r"/{2,}", "/", parts.path).rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))


def _tokens(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip().lower() for item in value if str(item).strip()]
    return [part.strip().lower() for part in str(value or "").split(",") if part.strip()]


def quality_score(row: dict[str, Any], section: dict[str, Any]) -> float:
    """Score substance and relevance while penalising promotional/clickbait rows."""
    quality = section.get("quality") if isinstance(section.get("quality"), dict) else {}
    preferred = _tokens(quality.get("keywords")) or list(DEFAULT_QUALITY_KEYWORDS)
    blocked = _tokens(quality.get("blocked_keywords")) or list(DEFAULT_BLOCKED_KEYWORDS)
    title = str(row.get("title") or "").strip()
    summary = str(row.get("summary") or "").strip()
    text = f"{title} {summary}".lower()
    upstream = row.get("relevance_score", 0.5)
    try:
        score = max(0.0, min(1.0, float(upstream))) * 0.58
    except (TypeError, ValueError):
        score = 0.29
    hits = sum(1 for keyword in preferred if keyword in text)
    score += min(0.28, hits * 0.045)
    if len(title) >= 12:
        score += 0.04
    if len(summary) >= 80:
        score += 0.09
    elif len(summary) >= 30:
        score += 0.05
    if re.search(r"研究|论文|技术|原理|架构|源码|实践|复盘|评测|教程|指南|发布|开源|benchmark|architecture|research", text, re.I):
        score += 0.08
    if any(keyword in text for keyword in blocked):
        score -= 0.38
    if re.search(r"[!！?？]{2,}|点击(?:领取|查看)|扫码|加群|关注后", text):
        score -= 0.22
    url = str(row.get("url") or "")
    if not url.startswith(("http://", "https://")):
        score -= 0.5
    if re.search(r"/(?:login|signin|search|topic|tag)(?:/|\?|$)", url, re.I):
        score -= 0.28
    if row.get("access") == "public-no-login":
        score += 0.04
    return round(max(0.0, min(1.0, score)), 4)


def merge_and_rank_results(
    document: dict[str, Any],
    domestic: list[dict[str, Any]],
    domestic_status: dict[str, str],
    section: dict[str, Any],
) -> dict[str, Any]:
    """Merge, canonicalize, quality-filter and source-balance hotspot rows."""
    quality = section.get("quality") if isinstance(section.get("quality"), dict) else {}
    min_score = float(quality.get("min_score", 0.48))
    per_source = int(quality.get("per_source_limit", 10))
    max_results = int(quality.get("max_results", 80))
    if not 0 <= min_score <= 1:
        raise ValueError("last30days.quality.min_score must be between 0 and 1")
    if per_source < 1:
        raise ValueError("last30days.quality.per_source_limit must be positive")
    if max_results < 1:
        raise ValueError("last30days.quality.max_results must be positive")
    candidates = list(document.get("results") or []) + domestic
    ranked: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    for raw in candidates:
        if not isinstance(raw, dict):
            continue
        title = re.sub(r"\s+", " ", str(raw.get("title") or "")).strip()
        url = canonical_url(str(raw.get("url") or ""))
        title_key = re.sub(r"[^\w\u4e00-\u9fff]+", "", title).lower()
        if not title or not url or url in seen_urls or (title_key and title_key in seen_titles):
            continue
        row = dict(raw)
        original_url = str(raw.get("url") or "").strip()
        row["title"], row["url"] = title, url
        if original_url != url:
            row["original_url"] = original_url
        row["quality_score"] = quality_score(row, section)
        if row["quality_score"] < min_score:
            continue
        seen_urls.add(url)
        if title_key:
            seen_titles.add(title_key)
        ranked.append(row)
    ranked.sort(key=lambda row: (float(row.get("quality_score", 0)), float(row.get("relevance_score", 0))), reverse=True)
    balanced: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for row in ranked:
        source = str(row.get("source") or urlsplit(row["url"]).netloc or "web").lower()
        if counts.get(source, 0) >= per_source:
            continue
        counts[source] = counts.get(source, 0) + 1
        balanced.append(row)
        if len(balanced) >= max_results:
            break
    document["results"] = balanced
    statuses = document.get("source_status")
    if not isinstance(statuses, dict):
        statuses = {}
    statuses.update(domestic_status)
    document["source_status"] = statuses
    document["quality"] = {
        "input_count": len(candidates),
        "selected_count": len(balanced),
        "min_score": min_score,
        "per_source_limit": per_source,
    }
    return document


def run_fetch(
    skill_dir: Path,
    output_path: Path,
    section: dict[str, Any],
    *,
    timeout: int = 600,
) -> None:
    """Run the last30days engine and write the JSON result to ``output_path``."""
    cmd = build_command(skill_dir, output_path, section)
    # last30days writes JSON on stdout when --emit=json is set; we capture it
    # and write to the requested output path ourselves so the file location is
    # deterministic regardless of engine save conventions.
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        stderr_tail = result.stderr.strip()[-2000:] if result.stderr else ""
        raise RuntimeError(
            f"last30days exited {result.returncode}.\n{stderr_tail}"
        )
    stdout = result.stdout.strip()
    if not stdout:
        raise RuntimeError("last30days produced no output on stdout")
    # Validate that the output is parseable JSON before augmenting it.
    try:
        document = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"last30days stdout is not valid JSON: {exc}\n"
            f"First 500 chars: {stdout[:500]}"
        ) from exc
    if not isinstance(document, dict):
        raise RuntimeError("last30days JSON root must be an object")
    domestic_config = section.get("domestic") if isinstance(section.get("domestic"), dict) else {}
    domestic: list[dict[str, Any]] = []
    domestic_status: dict[str, str] = {}
    if domestic_config.get("enabled", True):
        platforms = _tokens(domestic_config.get("platforms")) or list(DEFAULT_PLATFORMS)
        configured_queries = domestic_config.get("queries")
        queries = (
            [str(item).strip() for item in configured_queries if str(item).strip()]
            if isinstance(configured_queries, list)
            else list(DEFAULT_QUERIES)
        )
        domestic, domestic_status = search_domestic_platforms(
            str(section.get("domestic_topic") or section.get("topic") or DEFAULT_TOPIC),
            platforms=platforms,
            queries=queries,
            per_platform=int(domestic_config.get("per_platform", 6)),
            days=int(section.get("days") or DEFAULT_DAYS),
        )
    document = merge_and_rank_results(document, domestic, domestic_status, section)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(f".{output_path.name}.tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output_path)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch last30days hotspots and write JSON to --output."
    )
    parser.add_argument(
        "--config", required=True, type=Path,
        help="Digest config JSON (reads last30days section for search parameters).",
    )
    parser.add_argument(
        "--skill-dir", required=True, type=Path,
        help="Directory containing the last30days skill (must have scripts/last30days.py).",
    )
    parser.add_argument(
        "--output", required=True, type=Path,
        help="Destination JSON file for the last30days result.",
    )
    parser.add_argument(
        "--timeout", type=int, default=600,
        help="Timeout in seconds for the last30days engine (default: 600).",
    )
    args = parser.parse_args()

    try:
        section = load_last30days_config(args.config)
        run_fetch(
            skill_dir=args.skill_dir.expanduser().resolve(),
            output_path=args.output.expanduser().resolve(),
            section=section,
            timeout=args.timeout,
        )
        topic = str(section.get("topic") or "").strip() or DEFAULT_TOPIC
        print(json.dumps({
            "output": str(args.output.expanduser().resolve()),
            "topic": topic,
            "days": section.get("days") or DEFAULT_DAYS,
            "sources": json.loads(args.output.expanduser().resolve().read_text(encoding="utf-8")).get("source_status", {}),
        }, ensure_ascii=False))
        return 0
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
