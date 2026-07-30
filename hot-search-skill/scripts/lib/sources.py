from __future__ import annotations

import datetime as dt
import os
import urllib.parse
import xml.etree.ElementTree as ET

from .http import SourceError, get_json, get_text
from .models import SocialItem


def _iso_from_epoch(value: int | float) -> str:
    return dt.datetime.fromtimestamp(value, tz=dt.UTC).isoformat().replace("+00:00", "Z")


def hacker_news(topic: str, since: dt.datetime, limit: int) -> list[SocialItem]:
    params = urllib.parse.urlencode({
        "query": topic,
        "tags": "story",
        "numericFilters": f"created_at_i>{int(since.timestamp())}",
        "hitsPerPage": limit,
    })
    data = get_json(f"https://hn.algolia.com/api/v1/search?{params}")
    items = []
    for row in data.get("hits") or []:
        object_id = str(row.get("objectID") or "")
        items.append(SocialItem(
            item_id=f"hn:{object_id}",
            source="Hacker News",
            title=row.get("title") or "",
            body=row.get("story_text") or "",
            url=row.get("url") or f"https://news.ycombinator.com/item?id={object_id}",
            author=row.get("author") or "",
            published_at=row.get("created_at") or "",
            engagement={"points": int(row.get("points") or 0), "comments": int(row.get("num_comments") or 0)},
        ))
    return [item for item in items if item.title]


def bluesky(topic: str, since: dt.datetime, limit: int) -> list[SocialItem]:
    params = urllib.parse.urlencode({"q": topic, "limit": min(limit, 100), "sort": "top"})
    data = get_json(f"https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts?{params}")
    items = []
    for row in data.get("posts") or []:
        record = row.get("record") or {}
        created = record.get("createdAt") or row.get("indexedAt") or ""
        try:
            created_dt = dt.datetime.fromisoformat(created.replace("Z", "+00:00"))
        except ValueError:
            continue
        if created_dt < since:
            continue
        author = row.get("author") or {}
        handle = author.get("handle") or ""
        post_id = (row.get("uri") or "").rsplit("/", 1)[-1]
        text = record.get("text") or ""
        items.append(SocialItem(
            item_id=f"bluesky:{row.get('uri') or post_id}",
            source="Bluesky",
            title=text.splitlines()[0][:180],
            body=text,
            url=f"https://bsky.app/profile/{handle}/post/{post_id}",
            author=f"@{handle}" if handle else "",
            published_at=created,
            engagement={
                "likes": int(row.get("likeCount") or 0),
                "reposts": int(row.get("repostCount") or 0),
                "replies": int(row.get("replyCount") or 0),
            },
        ))
    return [item for item in items if item.title]


def reddit(topic: str, since: dt.datetime, limit: int) -> list[SocialItem]:
    params = urllib.parse.urlencode({
        "q": topic,
        "sort": "top",
        "t": "week",
        "limit": min(limit, 100),
        "restrict_sr": "false",
    })
    try:
        data = get_json(f"https://www.reddit.com/search.json?{params}")
    except SourceError as exc:
        if exc.state != "rate-limited":
            raise
        return _reddit_rss(topic, since, limit)
    items = []
    for child in ((data.get("data") or {}).get("children") or []):
        row = child.get("data") or {}
        created = float(row.get("created_utc") or 0)
        if not created or dt.datetime.fromtimestamp(created, tz=dt.UTC) < since:
            continue
        permalink = row.get("permalink") or ""
        items.append(SocialItem(
            item_id=f"reddit:{row.get('id') or permalink}",
            source="Reddit",
            title=row.get("title") or "",
            body=row.get("selftext") or "",
            url=f"https://www.reddit.com{permalink}",
            author=f"u/{row.get('author')}" if row.get("author") else "",
            published_at=_iso_from_epoch(created),
            engagement={"upvotes": int(row.get("score") or 0), "comments": int(row.get("num_comments") or 0)},
        ))
    return [item for item in items if item.title]


def _reddit_rss(topic: str, since: dt.datetime, limit: int) -> list[SocialItem]:
    query = urllib.parse.quote_plus(topic)
    url = f"https://www.reddit.com/search.rss?q={query}&sort=relevance&t=month"
    text = get_text(url, headers={"Accept": "application/atom+xml"})
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise SourceError("Reddit RSS returned invalid XML", "schema-drift") from exc
    atom = "{http://www.w3.org/2005/Atom}"
    items = []
    for entry in root.findall(f"{atom}entry"):
        title = (entry.findtext(f"{atom}title") or "").strip()
        updated = (entry.findtext(f"{atom}updated") or "").strip()
        try:
            published = dt.datetime.fromisoformat(updated.replace("Z", "+00:00"))
        except ValueError:
            continue
        if published < since:
            continue
        link = entry.find(f"{atom}link")
        href = link.get("href", "") if link is not None else ""
        author = (entry.findtext(f"{atom}author/{atom}name") or "").strip()
        entry_id = (entry.findtext(f"{atom}id") or href).strip()
        items.append(SocialItem(
            item_id=f"reddit:{entry_id}",
            source="Reddit",
            title=title,
            body="",
            url=href,
            author=author,
            published_at=updated,
            engagement={},
        ))
        if len(items) >= limit:
            break
    return [item for item in items if item.title and item.url]


def github(topic: str, since: dt.datetime, limit: int) -> list[SocialItem]:
    query = f"{topic} pushed:>={since.date().isoformat()}"
    params = urllib.parse.urlencode({"q": query, "sort": "stars", "order": "desc", "per_page": limit})
    headers = {}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = get_json(f"https://api.github.com/search/repositories?{params}", headers=headers)
    items = []
    for row in data.get("items") or []:
        full_name = row.get("full_name") or ""
        items.append(SocialItem(
            item_id=f"github:{full_name.lower()}",
            source="GitHub",
            title=full_name,
            body=row.get("description") or "",
            url=row.get("html_url") or "",
            author=(row.get("owner") or {}).get("login") or "",
            published_at=row.get("pushed_at") or "",
            engagement={"stars": int(row.get("stargazers_count") or 0), "forks": int(row.get("forks_count") or 0)},
        ))
    return [item for item in items if item.title]


COLLECTORS = {
    "reddit": reddit,
    "bluesky": bluesky,
    "hackernews": hacker_news,
    "github": github,
}
