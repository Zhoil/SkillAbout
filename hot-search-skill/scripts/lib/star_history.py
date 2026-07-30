from __future__ import annotations

import html
import re


MOVEMENT_LABELS = {
    "▲": "up",
    "▼": "down",
    "N": "new",
    "–": "unchanged",
    "-": "unchanged",
}


def parse_weekly_leaderboard(page: str, limit: int) -> dict:
    marker = page.find('href="/coding-ai-leaderboard"')
    if marker < 0:
        raise ValueError("Star History weekly leaderboard marker not found")
    start = page.find("<ol", marker)
    end = page.find("</ol>", start)
    if start < 0 or end < 0:
        raise ValueError("Star History weekly leaderboard list not found")

    rows = []
    for block in re.findall(r"<li\b[\s\S]*?</li>", page[start:end], flags=re.I):
        link = re.search(r'<a\s+href="/([^"?#]+/[^"?#]+)"', block, flags=re.I)
        rank = re.search(r'text-right">\s*(\d+)\s*</span>', block, flags=re.I)
        delta = re.search(r'accent-text">\s*\+(\d[\d,]*)\s*</span>', block, flags=re.I)
        if not link or not rank or not delta:
            continue
        route_repo = html.unescape(link.group(1))
        tooltip = re.search(
            r'z-10">\s*([^<\s]+/[^<\s]+)<!--\s*-->[\s\S]*?\+(\d[\d,]*)\s*</span>',
            block,
            flags=re.I,
        )
        repo = html.unescape(tooltip.group(1)) if tooltip else route_repo
        movement_match = re.search(r'title="([^"]+)">\s*([▲▼N–-])\s*</span>', block)
        if movement_match:
            movement_detail = html.unescape(movement_match.group(1))
            movement = movement_match.group(2)
        else:
            symbol = re.search(r'text-gray-300">\s*([–-])\s*</span>', block)
            movement = symbol.group(1) if symbol else "–"
            movement_detail = "No rank change"
        rows.append({
            "source": "Star History",
            "rank": int(rank.group(1)),
            "movement": movement,
            "movement_type": MOVEMENT_LABELS.get(movement, "unknown"),
            "movement_detail": movement_detail,
            "title": repo,
            "repo": repo,
            "score": int(delta.group(1).replace(",", "")),
            "delta": int(delta.group(1).replace(",", "")),
            "description": f"Weekly rank #{rank.group(1)} · {movement_detail} · +{delta.group(1)} stars",
            "url": f"https://github.com/{repo}",
            "github_url": f"https://github.com/{repo}",
            "star_history_url": f"https://www.star-history.com/{route_repo}",
        })
        if len(rows) >= limit:
            break
    if not rows:
        raise ValueError("Star History weekly leaderboard contained no parseable rows")

    updated = re.search(r"Updated\s*<!--\s*-->\s*([^<]+)</p>", page[end:], flags=re.I)
    return {
        "period": "weekly",
        "updated_range": html.unescape(updated.group(1).strip()) if updated else "",
        "items": rows,
    }
