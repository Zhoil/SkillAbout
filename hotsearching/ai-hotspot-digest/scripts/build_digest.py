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

try:
    import matplotlib
    matplotlib.use("Agg")  # non-interactive backend
    import matplotlib.pyplot as plt
    import matplotlib.font_manager as fm
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False


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
    lines = ["【近30天 研发工具与技能热点】"]
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


def _setup_chinese_font() -> str | None:
    """Find a CJK-capable font for matplotlib. Returns None if not found."""
    if not HAS_MATPLOTLIB:
        return None
    # Prefer fonts with good Simplified Chinese coverage
    preferred_fonts = [
        "PingFang SC", "Heiti SC", "STHeiti", "Songti SC",
        "Noto Sans CJK SC", "Source Han Sans SC", "WenQuanYi Micro Hei",
        "Microsoft YaHei", "SimHei", "SimSun",
        "Noto Sans CJK", "Source Han Sans",
        "Hiragino Sans", "Hiragino Sans GB",
    ]
    available_fonts = {font.name for font in fm.fontManager.ttflist}
    for font_name in preferred_fonts:
        if font_name in available_fonts:
            return font_name
    # Fallback: find any font with CJK in the name
    cjk_keywords = ("CJK", "Hei", "Song", "Ming", "PingFang", "Hiragino",
                    "Microsoft YaHei", "WenQuanYi", "Source Han")
    for font in fm.fontManager.ttflist:
        if any(kw in font.name for kw in cjk_keywords):
            return font.name
    return None


def render_weekly_chart(
    items: list[dict[str, str]],
    limit: int,
    output_path: Path,
) -> Path | None:
    """Generate a before/after comparison chart for the top-N weekly repos.

    The chart shows:
      - X-axis: repo names (shortened)
      - Two lines with different colors: "previous rank" and "current rank"
      - Different colored lines and nodes for before/after
      - An accompanying data table below the chart

    Returns the chart file path, or None if matplotlib is unavailable.
    """
    if not HAS_MATPLOTLIB or not items:
        return None

    limited = items[:limit]
    if not limited:
        return None

    # Set up CJK font
    cjk_font = _setup_chinese_font()
    if cjk_font:
        plt.rcParams["font.sans-serif"] = [cjk_font] + plt.rcParams.get("font.sans-serif", [])
    plt.rcParams["axes.unicode_minus"] = False

    repos = [item["title"].split("/")[-1] if "/" in item["title"] else item["title"]
             for item in limited]
    current_ranks = list(range(1, len(repos) + 1))
    stars_deltas = [int(item["stars_delta"]) for item in limited]
    trends = [item["trend"] for item in limited]

    # Compute "previous" rank based on trend and stars_delta.
    # ▲ means rank improved (previous rank was higher/worse),
    # ▼ means rank dropped (previous rank was lower/better),
    # – means unchanged.
    previous_ranks = []
    for rank, trend in zip(current_ranks, trends):
        if trend == "▲":
            previous_ranks.append(rank + 1)
        elif trend == "▼":
            previous_ranks.append(max(1, rank - 1))
        else:
            previous_ranks.append(rank)

    fig, (ax_chart, ax_table) = plt.subplots(
        2, 1, figsize=(max(12, len(repos) * 0.8), 10),
        gridspec_kw={"height_ratios": [3, 2]},
    )

    # --- Chart ---
    x_positions = list(range(len(repos)))

    # Previous rank line (blue)
    ax_chart.plot(
        x_positions, previous_ranks,
        color="#4A90D9", marker="o", markersize=8, linewidth=2.2,
        label="上期排名 (Previous Rank)", zorder=3,
    )
    # Current rank line (orange)
    ax_chart.plot(
        x_positions, current_ranks,
        color="#E8734A", marker="s", markersize=8, linewidth=2.2,
        label="本期排名 (Current Rank)", zorder=4,
    )

    # Annotate deltas on the chart
    for i, (x, delta, trend) in enumerate(zip(x_positions, stars_deltas, trends)):
        symbol = "▲" if trend == "▲" else ("▼" if trend == "▼" else "–")
        color = "#2E8B57" if trend == "▲" else ("#DC143C" if trend == "▼" else "#888888")
        ax_chart.annotate(
            f"{symbol}+{delta:,}",
            (x, current_ranks[i]),
            textcoords="offset points",
            xytext=(0, -16),
            ha="center", fontsize=7.5, color=color, fontweight="bold",
        )

    ax_chart.set_xticks(x_positions)
    ax_chart.set_xticklabels(repos, rotation=45, ha="right", fontsize=8)
    ax_chart.invert_yaxis()  # Rank 1 at the top
    ax_chart.set_ylabel("排名 (Rank)", fontsize=11)
    ax_chart.set_title("Star History Weekly 前后对比图", fontsize=14, fontweight="bold", pad=12)
    ax_chart.legend(loc="lower right", fontsize=10, framealpha=0.9)
    ax_chart.grid(axis="y", linestyle="--", alpha=0.5)
    ax_chart.set_axisbelow(True)

    # --- Data Table ---
    ax_table.axis("off")
    col_labels = ["序号", "仓库 (Repo)", "趋势", "上期排名", "本期排名", "Star 变化"]
    cell_text = []
    for i, item in enumerate(limited):
        trend_symbol = item["trend"]
        if trend_symbol == "▲":
            trend_text = "▲ 上升"
        elif trend_symbol == "▼":
            trend_text = "▼ 下降"
        else:
            trend_text = "– 持平"
        cell_text.append([
            str(i + 1),
            item["title"],
            trend_text,
            str(previous_ranks[i]),
            str(current_ranks[i]),
            f"+{int(item['stars_delta']):,}",
        ])

    table = ax_table.table(
        cellText=cell_text,
        colLabels=col_labels,
        cellLoc="center",
        loc="center",
        colWidths=[0.06, 0.30, 0.10, 0.12, 0.12, 0.14],
    )
    table.auto_set_font_size(False)
    table.set_fontsize(8)
    table.scale(1, 1.4)

    # Style header row
    for j in range(len(col_labels)):
        cell = table[0, j]
        cell.set_facecolor("#4A90D9")
        cell.set_text_props(color="white", fontweight="bold")

    # Alternate row colors
    for i in range(1, len(cell_text) + 1):
        for j in range(len(col_labels)):
            cell = table[i, j]
            if i % 2 == 0:
                cell.set_facecolor("#F0F4FA")
            else:
                cell.set_facecolor("#FFFFFF")

    plt.tight_layout()
    chart_path = output_path.with_suffix(".png")
    fig.savefig(str(chart_path), dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return chart_path


def render_weekly_table(items: list[dict[str, str]], limit: int) -> str:
    """Render a text-based data table for the top-N weekly changes."""
    limited = items[:limit]
    if not limited:
        return ""

    # Compute previous ranks (same logic as chart)
    current_ranks = list(range(1, len(limited) + 1))
    previous_ranks = []
    for rank, item in zip(current_ranks, limited):
        trend = item["trend"]
        if trend == "▲":
            previous_ranks.append(rank + 1)
        elif trend == "▼":
            previous_ranks.append(max(1, rank - 1))
        else:
            previous_ranks.append(rank)

    lines = ["【Weekly 变化对比表】"]
    # Header
    lines.append(f"{'序号':>4}  {'仓库':<30}  {'趋势':>4}  {'上期排名':>8}  {'本期排名':>8}  {'Star变化':>10}")
    lines.append("─" * 75)

    for i, item in enumerate(limited):
        trend = item["trend"]
        repo_name = item["title"]
        if len(repo_name) > 28:
            repo_name = repo_name[:25] + "..."
        lines.append(
            f"{i + 1:>4}  {repo_name:<30}  {trend:>4}  {previous_ranks[i]:>8}  "
            f"{current_ranks[i]:>8}  +{int(item['stars_delta']):>9,}"
        )

    return "\n".join(lines)


GMT_PLUS_8 = timezone(timedelta(hours=8))


def format_gmt8_timestamp(value: datetime | None = None) -> str:
    current = value or datetime.now(timezone.utc)
    return current.astimezone(GMT_PLUS_8).strftime("🕒 %Y-%m-%d %H:%M:%S GMT+8")


def render_message(sections: list[str], generated_at: datetime | None = None) -> str:
    return format_gmt8_timestamp(generated_at) + "\n\n研发工具与技能热点及开源趋势汇总\n\n" + "\n\n".join(sections)


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

        # Build sections for the text message
        sections = [section for section in (
            render_recent(recent, limits["last30days"], annotations),
            render_weekly(weekly, limits["weekly"]),
            render_weekly_table(weekly, limits["weekly"]),
            render_all_time(all_time, limits["all_time"]),
        ) if section]
        if not sections:
            raise ValueError("All configured limits are zero; message would be empty")
        message = render_message(sections)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(message, encoding="utf-8")

        # Generate the comparison chart image (optional, requires matplotlib)
        chart_path: Path | None = None
        chart_error: str | None = None
        if limits["weekly"] > 0 and weekly:
            try:
                chart_path = render_weekly_chart(weekly, limits["weekly"], args.output)
            except Exception as exc:
                chart_error = str(exc)

        result: dict[str, Any] = {
            "output": str(args.output.resolve()),
            "counts": {
                "last30days": min(len(recent), limits["last30days"]),
                "weekly": min(len(weekly), limits["weekly"]),
                "all_time": min(len(all_time), limits["all_time"]),
            },
        }
        if chart_path:
            result["chart"] = str(chart_path.resolve())
        if chart_error:
            result["chart_error"] = chart_error
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
