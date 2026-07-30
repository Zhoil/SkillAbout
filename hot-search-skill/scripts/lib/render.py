from __future__ import annotations

import html
import json


def _diverse_items(report: dict, limit: int) -> list[dict]:
    buckets: dict[str, list[dict]] = {}
    for item in report.get("items", []):
        buckets.setdefault(item["source"], []).append(item)
    selected = []
    while len(selected) < limit:
        added = False
        for source in sorted(buckets, key=lambda name: -buckets[name][0]["score"] if buckets[name] else 0):
            if buckets[source] and len(selected) < limit:
                selected.append(buckets[source].pop(0))
                added = True
        if not added:
            break
    return selected


def markdown(report: dict, limit: int) -> str:
    lines = [
        f"# Global AI Social Pulse",
        "",
        f"Topic: {report['topic']} | Window: {report['window_days']} days | Generated: {report['generated_at']}",
        "",
    ]
    for index, item in enumerate(_diverse_items(report, limit), 1):
        metrics = ", ".join(f"{key} {value:,}" for key, value in item.get("engagement", {}).items())
        lines.extend([
            f"{index}. **[{item['source']}] [{item['title']}]({item['url']})**",
            f"   Score {item['score']:.1f}" + (f" | {metrics}" if metrics else ""),
        ])
    lines.extend(["", "Source coverage:"])
    lines.extend(f"- {row['source']}: {row['state']} ({row['items_returned']})" for row in report.get("source_status", []))
    return "\n".join(lines)


def text(report: dict, limit: int) -> str:
    """Plain text cards for any chat push channel (DingTalk, Feishu, Slack, etc.)."""
    lines = ["🌐【Global AI Social Pulse】", f"🕒 {report['generated_at']} · 近 {report['window_days']} 天", ""]
    for index, item in enumerate(_diverse_items(report, limit), 1):
        metrics = " · ".join(f"{key} {value:,}" for key, value in item.get("engagement", {}).items() if value)
        lines.append(f"{index}. [{item['source']}] {item['title']}")
        lines.append(f"   热度 {item['score']:.1f}" + (f" · {metrics}" if metrics else ""))
        lines.append(f"   {item['url']}")
    coverage = " · ".join(f"{row['source']}:{row['state']}" for row in report.get("source_status", []))
    lines.extend(["", f"📡 {coverage}"])
    return "\n".join(lines)[:1950]


def visual_html(report: dict, limit: int) -> str:
    cards = []
    for index, item in enumerate(_diverse_items(report, limit), 1):
        metrics = "".join(f"<span>{html.escape(key)} <b>{value:,}</b></span>" for key, value in item.get("engagement", {}).items())
        cards.append(f'''<a class="item" href="{html.escape(item['url'])}">
          <div class="rank">{index}</div><div><div class="source">{html.escape(item['source'])}</div>
          <h2>{html.escape(item['title'])}</h2><div class="metrics"><span>heat <b>{item['score']:.1f}</b></span>{metrics}</div></div></a>''')
    statuses = "".join(f"<span class=\"status {html.escape(row['state'])}\">{html.escape(row['source'])}: {html.escape(row['state'])}</span>" for row in report.get("source_status", []))
    payload = json.dumps(report, ensure_ascii=False).replace("</", "<\\/")
    return f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Global AI Social Pulse</title><style>
:root{{--ink:#181b20;--muted:#667085;--line:#d9dde5;--paper:#f5f7fa;--accent:#d92d20;--blue:#175cd3}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:15px/1.45 system-ui,sans-serif}}
header{{background:#101828;color:white;padding:28px max(20px,calc((100% - 980px)/2)) 24px;border-bottom:5px solid #fdb022}}
h1{{font-size:30px;margin:0 0 6px;letter-spacing:0}}header p{{margin:0;color:#d0d5dd}}main{{max-width:980px;margin:0 auto;padding:22px 20px 40px}}
.coverage{{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:18px}}.status{{background:white;border:1px solid var(--line);padding:5px 9px;border-radius:4px;font-size:12px}}.status.ok{{border-color:#12b76a;color:#067647}}
.list{{display:grid;grid-template-columns:1fr 1fr;gap:10px}}.item{{display:grid;grid-template-columns:38px 1fr;gap:12px;color:inherit;text-decoration:none;background:white;border:1px solid var(--line);border-radius:6px;padding:15px;min-height:150px}}
.item:hover{{border-color:var(--blue)}}.rank{{font:700 24px/1 system-ui;color:#98a2b3}}.source{{color:var(--accent);font-size:12px;font-weight:700;text-transform:uppercase}}h2{{font-size:17px;line-height:1.35;margin:8px 0 18px;letter-spacing:0}}
.metrics{{display:flex;gap:10px;flex-wrap:wrap;color:var(--muted);font-size:12px}}.metrics b{{color:var(--ink)}}
@media(max-width:700px){{.list{{grid-template-columns:1fr}}header{{padding:22px 18px}}main{{padding:16px}}}}
</style></head><body><header><h1>Global AI Social Pulse</h1><p>{html.escape(report['topic'])} · {report['window_days']} day window · {html.escape(report['generated_at'])}</p></header>
<main><div class="coverage">{statuses}</div><div class="list">{''.join(cards)}</div></main><script type="application/json" id="report">{payload}</script></body></html>'''
