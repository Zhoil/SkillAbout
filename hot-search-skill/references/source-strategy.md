# Source Strategy

## Pipeline Shape

The social research pipeline follows the useful boundaries from `/last30days`:

1. Source adapters fetch native records.
2. `SocialItem` normalizes identity, title, body, URL, author, time, and engagement.
3. Concurrent collection records one `SourceOutcome` per adapter.
4. Similar titles form lightweight cross-source clusters.
5. Ranking combines log-scaled engagement, freshness decay, source quality, and cross-source diversity.
6. Renderers produce JSON, Markdown, text cards, or standalone HTML from the same report.

Do not merge fetching and presentation. A new source belongs in `scripts/lib/sources.py` and must return `SocialItem` objects.

## Current Sources

| Source | Signal | Access | Limits |
|---|---|---|---|
| Reddit | community posts, votes, comments | public search JSON | may block or rate-limit datacenter traffic |
| Bluesky | public posts, likes, reposts, replies | public AppView API | search relevance and global coverage vary |
| Hacker News | technical discussion, points, comments | public Algolia API | technology-heavy audience |
| GitHub | repository popularity and recent project activity | REST search API | not a proxy for social sentiment; low anonymous rate limit |
| Star History | homepage left-side Weekly leaderboard | public server-rendered HTML | unstable HTML, not a supported API |

Star History ranking rows must come only from the left-side Weekly `<ol>` after the `coding-ai-leaderboard` marker. Preserve current rank, movement symbol and detail (`Up N`, `Down N`, `New to top 20`, or unchanged), weekly star delta, GitHub URL, and Star History URL. Do not fall back to arbitrary repository names elsewhere on the page.

## Optional Future Sources

X, YouTube comments, TikTok, Instagram, Threads, and LinkedIn generally need credentials, cookies, paid APIs, or platform-specific collectors. Add them only when configuration, terms, failure states, and native engagement fields are explicit. Never silently replace a missing social platform with generic web results.

## Coverage Semantics

- `ok`: source returned usable items.
- `no-results`: request succeeded and returned no usable items.
- `rate-limited`: platform rejected request volume or anonymous access.
- `unreachable`: DNS, network, or timeout failure.
- `schema-drift`: response could not be parsed as expected.
- `error`: other adapter failure.

Only `no-results` supports saying the source was quiet. All other failure states mean coverage is partial.

## Ranking Interpretation

Scores rank items within the current run; they are not comparable across dates or topics. Preserve native metrics beside the score. Cross-source confirmation increases confidence, but a high-engagement single-source item can still rank when clearly labeled.
