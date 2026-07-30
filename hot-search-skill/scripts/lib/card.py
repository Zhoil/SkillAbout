from __future__ import annotations


NATIVE_ID = "templateMsgCard"


def hot_search_card(data: dict, limit: int = 6) -> dict:
    generated = data.get("generated_at", "").replace("T", " ").replace("Z", " UTC")
    star_items = (data.get("star_growth") or {}).get("items") or []
    social_items = (data.get("ai_news") or {}).get("items") or []
    elements = [
        {"tag": "me_md", "content": "**GitHub Star History · Weekly Growth**", "font_size": "default"},
    ]
    for item in star_items[:limit]:
        move = item.get("movement") or "–"
        bullet = f"#{item.get('rank', '-')} {move}"
        detail = item.get("movement_detail") or "No rank change"
        text = f"[{item['title']}]({item['github_url']})  **+{item.get('delta', 0)}** stars\n{detail}"
        elements.append({
            "tag": "list_item",
            "bullet": {"type": "primary" if move in ("▲", "N") else "secondary", "content": bullet},
            "text": {"content": text, "font_size": "small"},
        })
    elements.extend([
        {"tag": "hr"},
        {"tag": "me_md", "content": "**Global AI Social Pulse**", "font_size": "default"},
    ])
    for index, item in enumerate(social_items[:limit], 1):
        source = item.get("source", "Social")
        title = item.get("title", "")
        url = item.get("url", "")
        score = item.get("score", 0)
        elements.append({
            "tag": "me_md",
            "content": f"**{index}. [{source}]** [{title}]({url})\nHeat {score}",
            "font_size": "small",
        })
    errors = (data.get("ai_hotspots") or {}).get("errors") or []
    if errors:
        elements.append({"tag": "notice", "title": {"content": "Partial coverage: " + "; ".join(errors[:2])}})
    elements.append({
        "tag": "me_md",
        "content": "Star ranking: [Star History Weekly](https://www.star-history.com/) · Repository names open GitHub.",
        "font_size": "small",
        "color": "#6A6A6A",
    })
    return {
        "native_id": NATIVE_ID,
        "data": {
            "header": {
                "theme": "blue",
                "title": {"content": "Hot Search · GitHub & AI"},
                "subtitle": {"content": generated},
                "label": {"content": "LIVE", "theme": "red"},
            },
            "elements": elements,
        },
    }
