from __future__ import annotations

import datetime as dt
import hashlib
import math
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from .http import SourceError
from .models import SocialItem, SourceOutcome
from .sources import COLLECTORS


SOURCE_QUALITY = {"Reddit": 1.0, "Hacker News": 1.0, "Bluesky": 0.85, "GitHub": 0.9}


def _tokens(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9][a-z0-9.+-]{2,}", text.lower()) if token not in {"the", "and", "with", "from", "this", "that"}}


def _cluster(items: list[SocialItem]) -> None:
    representatives: list[tuple[str, set[str]]] = []
    for item in items:
        tokens = _tokens(item.title)
        matched = ""
        for cluster_id, known in representatives:
            overlap = len(tokens & known) / max(1, len(tokens | known))
            if overlap >= 0.38:
                matched = cluster_id
                known.update(tokens)
                break
        if not matched:
            matched = hashlib.sha1(" ".join(sorted(tokens)).encode()).hexdigest()[:10]
            representatives.append((matched, tokens))
        item.cluster_id = matched


def _score(items: list[SocialItem], now: dt.datetime) -> None:
    cluster_sources: dict[str, set[str]] = {}
    for item in items:
        cluster_sources.setdefault(item.cluster_id, set()).add(item.source)
    for item in items:
        engagement = sum(max(0, value) for value in item.engagement.values())
        try:
            published = dt.datetime.fromisoformat(item.published_at.replace("Z", "+00:00"))
            age_hours = max(0.0, (now - published).total_seconds() / 3600)
        except (ValueError, TypeError):
            age_hours = 24 * 30
        freshness = math.exp(-age_hours / (24 * 7))
        diversity = len(cluster_sources.get(item.cluster_id, set())) - 1
        item.score = round(
            math.log1p(engagement) * 12
            + freshness * 30
            + SOURCE_QUALITY.get(item.source, 0.7) * 10
            + diversity * 12,
            2,
        )


def research(topic: str, days: int, limit: int, sources: list[str] | None = None) -> dict:
    now = dt.datetime.now(dt.UTC)
    since = now - dt.timedelta(days=max(1, days))
    selected = sources or list(COLLECTORS)
    items: list[SocialItem] = []
    outcomes: list[SourceOutcome] = []
    with ThreadPoolExecutor(max_workers=min(6, len(selected))) as executor:
        futures = {
            executor.submit(COLLECTORS[name], topic, since, limit * 2): name
            for name in selected if name in COLLECTORS
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                source_items = future.result()
                items.extend(source_items)
                outcomes.append(SourceOutcome(name, "ok" if source_items else "no-results", len(source_items)))
            except SourceError as exc:
                outcomes.append(SourceOutcome(name, exc.state, detail=str(exc)))
            except Exception as exc:
                outcomes.append(SourceOutcome(name, "error", detail=str(exc)))
    _cluster(items)
    _score(items, now)
    items.sort(key=lambda item: (-item.score, item.source, item.title))
    per_source: dict[str, int] = {}
    diversified = []
    for item in items:
        if per_source.get(item.source, 0) >= limit:
            continue
        per_source[item.source] = per_source.get(item.source, 0) + 1
        diversified.append(item)
    return {
        "generated_at": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "topic": topic,
        "window_days": days,
        "items": [item.to_dict() for item in diversified[: limit * len(selected)]],
        "source_status": [outcome.to_dict() for outcome in sorted(outcomes, key=lambda row: row.source)],
    }
