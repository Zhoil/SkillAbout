#!/usr/bin/env python3
"""Build a push-ready digest without sending it."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import html
import json
from pathlib import Path
import re
import sys
from typing import Any
from urllib.parse import urljoin
from urllib.request import Request, urlopen
import webbrowser

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


def render_dashboard(
    recent: list[dict[str, str]],
    weekly: list[dict[str, str]],
    all_time: list[dict[str, str]],
    limits: dict[str, int],
    annotations: dict[str, str],
    message: str,
    generated_at: datetime | None = None,
) -> str:
    """Render a self-contained, animated HTML dashboard for the digest."""
    current = (generated_at or datetime.now(timezone.utc)).astimezone(GMT_PLUS_8)
    recent_rows = [
        {
            "index": index,
            "title": item["title"],
            "url": item.get("url", ""),
            "description": annotations.get(item.get("url", ""), ""),
        }
        for index, item in enumerate(recent[:limits["last30days"]], 1)
    ]
    weekly_rows = [
        {
            "index": index,
            "title": item["title"],
            "url": item.get("url", ""),
            "rank": int(item.get("rank", index)),
            "trend": item["trend"],
            "stars_delta": int(item["stars_delta"]),
        }
        for index, item in enumerate(weekly[:limits["weekly"]], 1)
    ]
    all_time_rows = [
        {
            "index": index,
            "title": item["title"],
            "url": item.get("url", ""),
            "stars": int(item["stars"]),
        }
        for index, item in enumerate(all_time[:limits["all_time"]], 1)
    ]
    payload = json.dumps({
        "generated_at": current.strftime("%Y-%m-%d %H:%M:%S GMT+8"),
        "recent": recent_rows,
        "weekly": weekly_rows,
        "all_time": all_time_rows,
        "message": message,
    }, ensure_ascii=False).replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    terminal_fallback = html.escape(message)
    return f'''<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="dark">
  <title>研发热点动态情报站</title>
  <style>
    :root {{ --ink:#e9f7ff; --muted:#86a7b9; --cyan:#35f2ff; --blue:#4976ff; --violet:#ae5cff; --lime:#8cffbd; --danger:#ff708e; --panel:rgba(7,18,32,.72); --line:rgba(104,225,255,.16); }}
    * {{ box-sizing:border-box; }}
    html {{ scroll-behavior:smooth; }}
    body {{ margin:0; min-height:100vh; overflow-x:hidden; color:var(--ink); font-family:Inter,"SF Pro Display","PingFang SC",system-ui,sans-serif; background:#030812; }}
    body::before {{ content:""; position:fixed; inset:0; z-index:-3; background:radial-gradient(circle at 18% 8%,rgba(48,95,255,.25),transparent 32%),radial-gradient(circle at 82% 18%,rgba(175,69,255,.2),transparent 28%),linear-gradient(145deg,#02050b 0%,#071426 52%,#020711 100%); }}
    body::after {{ content:""; position:fixed; inset:0; z-index:-2; opacity:.24; background-image:linear-gradient(rgba(77,213,255,.09) 1px,transparent 1px),linear-gradient(90deg,rgba(77,213,255,.09) 1px,transparent 1px); background-size:42px 42px; mask-image:linear-gradient(to bottom,black,transparent 88%); animation:grid-drift 16s linear infinite; }}
    .aurora {{ position:fixed; width:42vw; height:42vw; border-radius:50%; filter:blur(90px); opacity:.12; z-index:-1; animation:float 12s ease-in-out infinite alternate; background:var(--cyan); top:-20vw; right:-10vw; }}
    .shell {{ width:min(1400px,calc(100% - 36px)); margin:auto; padding:30px 0 70px; }}
    .topbar {{ display:flex; align-items:center; justify-content:space-between; gap:20px; margin-bottom:28px; }}
    .brand {{ display:flex; align-items:center; gap:12px; letter-spacing:.14em; font-size:13px; color:#b8ddec; text-transform:uppercase; }}
    .brand-mark {{ width:34px; aspect-ratio:1; display:grid; place-items:center; border:1px solid var(--cyan); clip-path:polygon(25% 0,75% 0,100% 25%,100% 75%,75% 100%,25% 100%,0 75%,0 25%); box-shadow:0 0 24px rgba(53,242,255,.45); }}
    .live {{ display:flex; align-items:center; gap:8px; color:var(--lime); font:12px ui-monospace,SFMono-Regular,Menlo,monospace; }}
    .live::before {{ content:""; width:7px; height:7px; border-radius:50%; background:var(--lime); box-shadow:0 0 0 5px rgba(140,255,189,.08),0 0 14px var(--lime); animation:pulse 1.8s infinite; }}
    .hero {{ position:relative; display:grid; grid-template-columns:1.4fr .6fr; gap:24px; padding:clamp(28px,5vw,64px); overflow:hidden; border:1px solid var(--line); border-radius:28px; background:linear-gradient(135deg,rgba(12,31,52,.92),rgba(7,13,27,.75)); box-shadow:0 30px 80px rgba(0,0,0,.36),inset 0 1px rgba(255,255,255,.05); }}
    .hero::after {{ content:""; position:absolute; width:280px; height:280px; right:-60px; top:-100px; border:1px solid rgba(53,242,255,.22); border-radius:50%; box-shadow:0 0 0 40px rgba(53,242,255,.025),0 0 0 80px rgba(53,242,255,.02); animation:orbit 15s linear infinite; }}
    .eyebrow {{ color:var(--cyan); font:700 12px ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.18em; }}
    h1 {{ max-width:760px; margin:15px 0 18px; font-size:clamp(38px,6vw,78px); line-height:.98; letter-spacing:-.055em; }}
    h1 span {{ color:transparent; background:linear-gradient(90deg,var(--cyan),#a5b8ff 50%,#d089ff); background-clip:text; -webkit-background-clip:text; }}
    .lead {{ max-width:680px; margin:0; color:#9bb8c8; font-size:16px; line-height:1.8; }}
    .hero-side {{ align-self:end; display:grid; gap:12px; position:relative; z-index:1; }}
    .metric {{ padding:18px; border:1px solid var(--line); border-radius:16px; background:rgba(3,11,21,.45); backdrop-filter:blur(14px); }}
    .metric b {{ display:block; font:600 clamp(28px,4vw,48px) ui-monospace,SFMono-Regular,Menlo,monospace; color:#fff; }}
    .metric span {{ color:var(--muted); font-size:12px; letter-spacing:.08em; }}
    .toolbar {{ position:sticky; top:12px; z-index:10; display:flex; justify-content:space-between; gap:14px; margin:24px 0; padding:10px; border:1px solid var(--line); border-radius:16px; background:rgba(3,10,20,.78); backdrop-filter:blur(20px); }}
    .tabs {{ display:flex; gap:6px; }}
    button,.search {{ border:1px solid transparent; border-radius:10px; color:var(--muted); background:transparent; font:inherit; font-size:13px; font-weight:600; }}
    button {{ padding:10px 15px; cursor:pointer; transition:.25s ease; }}
    button:hover,button.active {{ color:#fff; border-color:rgba(53,242,255,.22); background:rgba(53,242,255,.1); }}
    .search {{ min-width:240px; padding:10px 14px; outline:0; border-color:var(--line); }}
    .search:focus {{ border-color:var(--cyan); box-shadow:0 0 0 3px rgba(53,242,255,.08); }}
    .view[hidden] {{ display:none; }}
    .section {{ margin-top:34px; }}
    .section-head {{ display:flex; align-items:end; justify-content:space-between; gap:18px; margin-bottom:16px; }}
    .section h2 {{ margin:0; font-size:clamp(22px,3vw,34px); letter-spacing:-.03em; }}
    .section-kicker {{ color:var(--cyan); font:11px ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.14em; }}
    .cards {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:14px; }}
    .card {{ position:relative; padding:22px; overflow:hidden; border:1px solid var(--line); border-radius:18px; background:linear-gradient(145deg,rgba(12,28,45,.78),rgba(5,12,23,.7)); transition:transform .3s ease,border-color .3s ease,box-shadow .3s ease; animation:reveal .65s both; animation-delay:calc(var(--i) * 55ms); }}
    .card::before {{ content:""; position:absolute; inset:0; pointer-events:none; opacity:0; background:radial-gradient(350px circle at var(--x,50%) var(--y,50%),rgba(53,242,255,.12),transparent 55%); transition:opacity .25s; }}
    .card:hover {{ transform:translateY(-4px); border-color:rgba(53,242,255,.48); box-shadow:0 22px 48px rgba(0,0,0,.28); }}
    .card:hover::before {{ opacity:1; }}
    .card-top {{ display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom:13px; }}
    .index {{ color:var(--cyan); font:700 12px ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.1em; }}
    .tag {{ padding:5px 9px; border:1px solid rgba(73,118,255,.25); border-radius:99px; color:#a9baff; background:rgba(73,118,255,.09); font:11px ui-monospace,SFMono-Regular,Menlo,monospace; }}
    .card h3 {{ margin:0 0 11px; font-size:18px; line-height:1.4; overflow-wrap:anywhere; }}
    .card p {{ min-height:48px; margin:0 0 18px; color:#91aebe; line-height:1.65; font-size:14px; }}
    .card a {{ position:relative; z-index:1; color:var(--cyan); text-decoration:none; font-size:12px; overflow-wrap:anywhere; }}
    .rank-list {{ display:grid; gap:9px; }}
    .rank {{ display:grid; grid-template-columns:55px minmax(180px,1fr) minmax(110px,2fr) 120px; align-items:center; gap:14px; padding:15px 18px; border:1px solid var(--line); border-radius:14px; background:rgba(7,18,32,.65); transition:.25s; animation:reveal .55s both; animation-delay:calc(var(--i) * 45ms); }}
    .rank:hover {{ transform:translateX(5px); border-color:rgba(53,242,255,.38); }}
    .rank-no {{ font:700 20px ui-monospace,SFMono-Regular,Menlo,monospace; color:#668da3; }}
    .rank-name {{ min-width:0; color:#e9f7ff; text-decoration:none; font-weight:650; overflow:hidden; text-overflow:ellipsis; }}
    .bar {{ height:5px; overflow:hidden; border-radius:99px; background:rgba(255,255,255,.06); }}
    .bar i {{ display:block; height:100%; width:var(--w); border-radius:inherit; transform-origin:left; background:linear-gradient(90deg,var(--blue),var(--cyan)); box-shadow:0 0 16px var(--cyan); animation:grow 1s .3s both; }}
    .delta {{ text-align:right; color:var(--lime); font:700 13px ui-monospace,SFMono-Regular,Menlo,monospace; }}
    .trend-down {{ color:var(--danger); }} .trend-flat {{ color:#9aaab4; }}
    .terminal {{ position:relative; min-height:560px; margin-top:24px; padding:48px 24px 24px; overflow:auto; border:1px solid rgba(53,242,255,.2); border-radius:18px; background:rgba(1,7,13,.92); box-shadow:inset 0 0 50px rgba(53,242,255,.025); }}
    .terminal::before {{ content:"●  ●  ●     DIGEST://LOCAL/PREVIEW"; position:absolute; top:0; left:0; right:0; padding:14px 18px; border-bottom:1px solid var(--line); color:#50697a; font:11px ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.12em; }}
    pre {{ margin:0; white-space:pre-wrap; word-break:break-word; color:#b9d8e7; font:13px/1.78 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }}
    .empty {{ color:var(--muted); padding:24px; border:1px dashed var(--line); border-radius:14px; }}
    footer {{ display:flex; justify-content:space-between; gap:18px; margin-top:42px; padding-top:20px; border-top:1px solid var(--line); color:#527185; font:11px ui-monospace,SFMono-Regular,Menlo,monospace; }}
    @keyframes pulse {{ 50% {{ opacity:.45; transform:scale(.8); }} }}
    @keyframes grid-drift {{ to {{ background-position:42px 42px; }} }}
    @keyframes float {{ to {{ transform:translate(-18vw,14vw) scale(1.2); }} }}
    @keyframes orbit {{ to {{ transform:rotate(360deg); }} }}
    @keyframes reveal {{ from {{ opacity:0; transform:translateY(14px); }} }}
    @keyframes grow {{ from {{ transform:scaleX(0); }} }}
    @media (max-width:820px) {{ .hero {{ grid-template-columns:1fr; }} .hero-side {{ grid-template-columns:repeat(3,1fr); }} .metric {{ padding:12px; }} .cards {{ grid-template-columns:1fr; }} .rank {{ grid-template-columns:42px 1fr 90px; }} .bar {{ display:none; }} .toolbar {{ align-items:stretch; flex-direction:column; }} .search {{ width:100%; min-width:0; }} }}
    @media (max-width:520px) {{ .shell {{ width:min(100% - 20px,1400px); padding-top:18px; }} .hero {{ padding:28px 20px; border-radius:20px; }} .hero-side {{ grid-template-columns:1fr; }} .metric b {{ font-size:28px; }} .topbar .live span {{ display:none; }} .tabs button {{ padding:9px 10px; }} .rank {{ padding:13px 12px; gap:8px; }} footer {{ flex-direction:column; }} }}
    @media (prefers-reduced-motion:reduce) {{ *,*::before,*::after {{ animation:none!important; scroll-behavior:auto!important; transition:none!important; }} }}
  </style>
</head>
<body>
  <div class="aurora"></div>
  <main class="shell">
    <header class="topbar"><div class="brand"><div class="brand-mark">AI</div>Signals Observatory</div><div class="live"><span>LIVE DATA · <b id="clock"></b></span></div></header>
    <section class="hero">
      <div><div class="eyebrow">DAILY INTELLIGENCE / GMT+8</div><h1>研发热点与<br><span>开源趋势脉冲</span></h1><p class="lead">聚合近 30 天研发工具、AI 技能与开源项目动量。浏览动态看板，或切换终端视图读取可复制的完整原始内容。</p></div>
      <div class="hero-side"><div class="metric"><b id="recent-count">0</b><span>HOT SIGNALS</span></div><div class="metric"><b id="weekly-count">0</b><span>WEEKLY MOVERS</span></div><div class="metric"><b id="star-sum">0</b><span>WEEKLY STAR GAIN</span></div></div>
    </section>
    <div class="toolbar"><div class="tabs"><button class="active" data-view="dashboard">动态看板</button><button data-view="terminal">终端内容</button><button id="copy">复制文本</button></div><input id="search" class="search" type="search" placeholder="搜索仓库、工具或描述…" aria-label="搜索内容"></div>
    <div id="dashboard" class="view">
      <section class="section"><div class="section-head"><div><div class="section-kicker">01 / FRESH SIGNALS</div><h2>近 30 天热点</h2></div></div><div id="recent" class="cards"></div></section>
      <section class="section"><div class="section-head"><div><div class="section-kicker">02 / VELOCITY INDEX</div><h2>Weekly 动量榜</h2></div></div><div id="weekly" class="rank-list"></div></section>
      <section id="all-time-section" class="section"><div class="section-head"><div><div class="section-kicker">03 / LONG-RANGE SIGNAL</div><h2>All-time 星标榜</h2></div></div><div id="all-time" class="rank-list"></div></section>
    </div>
    <div id="terminal" class="view" hidden><div class="terminal"><pre id="terminal-text"></pre></div></div>
    <footer><span id="generated"></span><span>STATIC · PRIVATE · ZERO DEPENDENCIES</span></footer>
  </main>
  <noscript><div class="shell"><div class="terminal"><pre>{terminal_fallback}</pre></div></div></noscript>
  <script id="digest-data" type="application/json">{payload}</script>
  <script>
    const data=JSON.parse(document.querySelector('#digest-data').textContent);
    const $=s=>document.querySelector(s), fmt=n=>new Intl.NumberFormat('zh-CN').format(n);
    const safeUrl=value=>{{ try {{ const u=new URL(value); return ['http:','https:'].includes(u.protocol)?u.href:'#'; }} catch {{ return '#'; }} }};
    const text=(tag,value,cls)=>{{ const el=document.createElement(tag); if(cls) el.className=cls; el.textContent=value; return el; }};
    function renderRecent(rows) {{ const host=$('#recent'); host.replaceChildren(); rows.forEach((row,i)=>{{ const card=text('article','', 'card'); card.style.setProperty('--i',i); card.dataset.search=(row.title+' '+row.description).toLowerCase(); const top=text('div','', 'card-top'); top.append(text('span',String(row.index).padStart(2,'0'),'index'),text('span','HOT / 30D','tag')); card.append(top,text('h3',row.title),text('p',row.description)); const link=text('a','访问来源 ↗'); link.href=safeUrl(row.url); link.target='_blank'; link.rel='noreferrer'; card.append(link); host.append(card); }}); if(!rows.length) host.append(text('div','暂无热点数据','empty')); }}
    function rankRow(row,i,type,max) {{ const el=text('div','', 'rank'); el.style.setProperty('--i',i); el.dataset.search=row.title.toLowerCase(); el.append(text('span',String(row.index).padStart(2,'0'),'rank-no')); const link=text('a',row.title,'rank-name'); link.href=safeUrl(row.url); link.target='_blank'; link.rel='noreferrer'; el.append(link); const bar=text('span','', 'bar'); const fill=document.createElement('i'); const value=type==='weekly'?row.stars_delta:row.stars; fill.style.setProperty('--w',Math.max(4,value/max*100)+'%'); bar.append(fill); el.append(bar); const trend=type==='weekly'?row.trend+' +'+fmt(row.stars_delta):fmt(row.stars)+' 🌟'; const cls=row.trend==='▼'?'delta trend-down':row.trend==='–'?'delta trend-flat':'delta'; el.append(text('span',trend,cls)); return el; }}
    function renderRanks(selector,rows,type) {{ const host=$(selector); host.replaceChildren(); const max=Math.max(1,...rows.map(r=>type==='weekly'?r.stars_delta:r.stars)); rows.forEach((row,i)=>host.append(rankRow(row,i,type,max))); if(!rows.length) host.append(text('div','暂无榜单数据','empty')); }}
    renderRecent(data.recent); renderRanks('#weekly',data.weekly,'weekly'); renderRanks('#all-time',data.all_time,'all-time');
    if(!data.all_time.length) $('#all-time-section').hidden=true;
    $('#recent-count').textContent=fmt(data.recent.length); $('#weekly-count').textContent=fmt(data.weekly.length); $('#star-sum').textContent=fmt(data.weekly.reduce((n,r)=>n+r.stars_delta,0)); $('#generated').textContent='GENERATED · '+data.generated_at; $('#terminal-text').textContent=data.message;
    document.querySelectorAll('[data-view]').forEach(btn=>btn.addEventListener('click',()=>{{ document.querySelectorAll('[data-view]').forEach(x=>x.classList.toggle('active',x===btn)); document.querySelectorAll('.view').forEach(x=>x.hidden=x.id!==btn.dataset.view); $('#search').hidden=btn.dataset.view==='terminal'; }}));
    $('#copy').addEventListener('click',async e=>{{ try {{ await navigator.clipboard.writeText(data.message); e.currentTarget.textContent='已复制 ✓'; setTimeout(()=>e.currentTarget.textContent='复制文本',1400); }} catch {{ e.currentTarget.textContent='复制失败'; }} }});
    $('#search').addEventListener('input',e=>{{ const q=e.target.value.trim().toLowerCase(); document.querySelectorAll('[data-search]').forEach(el=>el.hidden=q&&!el.dataset.search.includes(q)); }});
    document.addEventListener('pointermove',e=>document.querySelectorAll('.card').forEach(card=>{{ const r=card.getBoundingClientRect(); card.style.setProperty('--x',e.clientX-r.left+'px'); card.style.setProperty('--y',e.clientY-r.top+'px'); }}));
    const updateClock=()=>$('#clock').textContent=new Date().toLocaleTimeString('zh-CN',{{hour12:false}}); updateClock(); setInterval(updateClock,1000);
  </script>
</body>
</html>'''


def open_dashboard(path: Path) -> bool:
    """Open a generated dashboard in the system's default browser."""
    return webbrowser.open(path.resolve().as_uri(), new=2)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--last30days-file", required=True, type=Path)
    parser.add_argument("--annotations-file", type=Path)
    parser.add_argument("--star-history-html", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--no-open-dashboard",
        action="store_true",
        help="Generate the HTML dashboard without opening a browser window.",
    )
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
        generated_at = datetime.now(timezone.utc)
        message = render_message(sections, generated_at)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(message, encoding="utf-8")

        dashboard_path = args.output.with_suffix(".html")
        dashboard_path.write_text(
            render_dashboard(
                recent,
                weekly,
                all_time,
                limits,
                annotations,
                message,
                generated_at,
            ),
            encoding="utf-8",
        )

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
            "dashboard": str(dashboard_path.resolve()),
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
        if not args.no_open_dashboard:
            try:
                result["dashboard_opened"] = open_dashboard(dashboard_path)
            except (OSError, webbrowser.Error) as exc:
                result["dashboard_opened"] = False
                result["dashboard_open_error"] = str(exc)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
