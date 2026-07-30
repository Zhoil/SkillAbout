#!/usr/bin/env python3
"""Hot search skill CLI.

Collect GitHub star snapshots, compare them with local history, and surface
recent AI-related repositories and Hacker News stories.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from lib.card import hot_search_card
from lib.pipeline import research as research_social_hotspots
from lib.star_history import parse_weekly_leaderboard


DEFAULT_STATE_DIR = Path(os.environ.get("HOT_SEARCH_STATE_DIR", "~/.hot-search-skill")).expanduser()
DEFAULT_STATE_FILE = DEFAULT_STATE_DIR / "state.json"
DEFAULT_PUSH_CONFIG = DEFAULT_STATE_DIR / "push-config.json"
DEFAULT_LAUNCHD_LABEL = "com.hot-search-skill.daily-push"
SKILL_DIR = Path(__file__).resolve().parent.parent
DEFAULT_REPOS = [
    "openai/openai-python",
    "anthropics/anthropic-sdk-python",
    "langchain-ai/langchain",
    "microsoft/autogen",
    "crewAIInc/crewAI",
    "modelcontextprotocol/python-sdk",
]
AI_KEYWORDS = [
    "ai",
    "llm",
    "agent",
    "rag",
    "mcp",
    "generative-ai",
    "openai",
    "claude",
]


class HotSearchError(RuntimeError):
    pass


_SUPPORTED_CHANNELS = {"dingtalk", "feishu", "wecom", "slack", "telegram", "discord", "teams", "generic"}


def _load_push_config(path: Path) -> dict:
    if not path.exists():
        raise HotSearchError(
            f"push config not found: {path}; copy references/push-config.example.json and edit it"
        )
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HotSearchError(f"push config is not valid JSON: {path}: {exc}") from exc
    if not isinstance(config, dict):
        raise HotSearchError("push config must be a JSON object")
    if config.get("enabled") is not True:
        raise HotSearchError("automatic push is disabled; set enabled=true in the push config")
    channel = str(config.get("channel") or "").strip().lower()
    if not channel:
        raise HotSearchError(
            "push config requires a 'channel' field: dingtalk / feishu / wecom / slack / telegram / discord / teams / generic"
        )
    if channel not in _SUPPORTED_CHANNELS:
        raise HotSearchError(f"unsupported push channel '{channel}'; choose from: {', '.join(sorted(_SUPPORTED_CHANNELS))}")
    schedule = config.get("schedule") or {}
    hour = schedule.get("hour", 10)
    minute = schedule.get("minute", 10)
    if not isinstance(hour, int) or not 0 <= hour <= 23:
        raise HotSearchError("push config schedule.hour must be an integer from 0 to 23")
    if not isinstance(minute, int) or not 0 <= minute <= 59:
        raise HotSearchError("push config schedule.minute must be an integer from 0 to 59")
    limit = config.get("limit", 6)
    if not isinstance(limit, int) or not 1 <= limit <= 20:
        raise HotSearchError("push config limit must be an integer from 1 to 20")
    return config


def _configured_python(config: dict) -> str:
    requested = str(config.get("python") or sys.executable)
    resolved = shutil.which(requested)
    if not resolved:
        raise HotSearchError(f"configured Python executable not found: {requested}")
    return resolved


def _now_utc() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_time(value: str) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_repo(value: str) -> str:
    repo = value.strip().strip("/")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise argparse.ArgumentTypeError(f"invalid repo '{value}', expected owner/name")
    return repo


def _load_state(path: Path) -> dict:
    if not path.exists():
        return {"repos": {}, "created_at": _now_utc()}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HotSearchError(f"state file is not valid JSON: {path}") from exc


def _save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = _now_utc()
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _request_json(url: str, token: str | None = None, timeout: int = 20) -> dict:
    headers = {
        "Accept": "application/vnd.github+json, application/json",
        "User-Agent": "hot-search-skill/0.1",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise HotSearchError(f"HTTP {exc.code} for {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise HotSearchError(f"network error for {url}: {exc.reason}") from exc
    except TimeoutError as exc:
        raise HotSearchError(f"network timeout for {url}") from exc
    return json.loads(body)


def _request_text(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "hot-search-skill/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        raise HotSearchError(f"network error for {url}: {exc.reason}") from exc
    except TimeoutError as exc:
        raise HotSearchError(f"network timeout for {url}") from exc


def _github_token() -> str | None:
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")


def fetch_repo(repo: str) -> dict:
    try:
        data = _request_json(f"https://api.github.com/repos/{repo}", token=_github_token())
    except HotSearchError:
        data = _fetch_repo_via_search(repo)
    return {
        "repo": repo,
        "name": data.get("full_name", repo),
        "description": data.get("description") or "",
        "stars": int(data.get("stargazers_count") or 0),
        "forks": int(data.get("forks_count") or 0),
        "open_issues": int(data.get("open_issues_count") or 0),
        "language": data.get("language") or "",
        "pushed_at": data.get("pushed_at") or "",
        "html_url": data.get("html_url") or f"https://github.com/{repo}",
        "star_history_url": f"https://www.star-history.com/#/{repo}&Date",
        "fetched_at": _now_utc(),
    }


def _fetch_repo_via_search(repo: str) -> dict:
    params = urllib.parse.urlencode({"q": f"repo:{repo}", "per_page": 1})
    data = _request_json(f"https://api.github.com/search/repositories?{params}", token=_github_token())
    items = data.get("items") or []
    for item in items:
        if item.get("full_name", "").lower() == repo.lower():
            return item
    raise HotSearchError(f"repo not found through GitHub Search fallback: {repo}")


def snapshot(args: argparse.Namespace) -> dict:
    state = _load_state(args.state)
    repos = args.repo or DEFAULT_REPOS
    rows = []
    for repo in repos:
        current = fetch_repo(repo)
        history = state.setdefault("repos", {}).setdefault(repo, [])
        previous = history[-1] if history else None
        delta = current["stars"] - int(previous.get("stars", 0)) if previous else 0
        current["delta_since_last_snapshot"] = delta
        history.append({k: current[k] for k in ("stars", "forks", "open_issues", "pushed_at", "fetched_at")})
        rows.append(current)
    _save_state(args.state, state)
    return {"generated_at": _now_utc(), "state_file": str(args.state), "repos": rows}


def _baseline_for(history: list[dict], now: dt.datetime, hours: float) -> dict | None:
    cutoff = now - dt.timedelta(hours=hours)
    candidates = []
    for item in history:
        fetched = _parse_time(item.get("fetched_at", ""))
        if fetched and fetched <= cutoff:
            candidates.append((fetched, item))
    if candidates:
        return max(candidates, key=lambda pair: pair[0])[1]
    return None


def _hourly_rows(state: dict, repos: list[str], hours: float) -> list[dict]:
    now = dt.datetime.now(dt.UTC)
    rows = []
    for repo in repos:
        history = state.get("repos", {}).get(repo, [])
        current = history[-1] if history else None
        baseline = _baseline_for(history, now, hours)
        if not current:
            rows.append({"repo": repo, "status": "no_snapshot"})
            continue
        if not baseline or baseline is current:
            rows.append({
                "repo": repo,
                "status": "insufficient_history",
                "stars": int(current.get("stars", 0)),
                "fetched_at": current.get("fetched_at", ""),
            })
            continue
        current_time = _parse_time(current.get("fetched_at", "")) or now
        baseline_time = _parse_time(baseline.get("fetched_at", "")) or now
        elapsed_hours = max((current_time - baseline_time).total_seconds() / 3600, 1 / 3600)
        delta = int(current.get("stars", 0)) - int(baseline.get("stars", 0))
        rows.append({
            "repo": repo,
            "status": "ok",
            "stars": int(current.get("stars", 0)),
            "delta": delta,
            "elapsed_hours": round(elapsed_hours, 3),
            "stars_per_hour": round(delta / elapsed_hours, 3),
            "from": baseline.get("fetched_at", ""),
            "to": current.get("fetched_at", ""),
        })
    rows.sort(key=lambda row: (row.get("status") != "ok", -float(row.get("stars_per_hour", -1))))
    return rows


def hourly(args: argparse.Namespace) -> dict:
    state = _load_state(args.state)
    repos = args.repo or sorted(state.get("repos", {}).keys()) or DEFAULT_REPOS
    if args.refresh:
        snap_args = argparse.Namespace(state=args.state, repo=repos)
        snapshot(snap_args)
        state = _load_state(args.state)
    return {
        "generated_at": _now_utc(),
        "state_file": str(args.state),
        "hours": args.hours,
        "repos": _hourly_rows(state, repos, args.hours),
    }


def watch(args: argparse.Namespace) -> dict:
    reports = []
    interval = max(10, int(args.interval))
    for idx in range(max(1, int(args.count))):
        reports.append(snapshot(args))
        if idx < int(args.count) - 1:
            time.sleep(interval)
    return {"generated_at": _now_utc(), "runs": reports}


def github_ai_repos(limit: int) -> list[dict]:
    pushed_after = (dt.datetime.now(dt.UTC) - dt.timedelta(days=30)).date().isoformat()
    query = " ".join(AI_KEYWORDS[:4]) + f" pushed:>={pushed_after}"
    params = urllib.parse.urlencode({"q": query, "sort": "stars", "order": "desc", "per_page": limit})
    data = _request_json(f"https://api.github.com/search/repositories?{params}", token=_github_token())
    rows = []
    for item in data.get("items", [])[:limit]:
        rows.append({
            "source": "GitHub",
            "title": item.get("full_name", ""),
            "score": int(item.get("stargazers_count") or 0),
            "description": item.get("description") or "",
            "url": item.get("html_url", ""),
            "language": item.get("language") or "",
            "pushed_at": item.get("pushed_at") or "",
        })
    return rows


def hn_ai_stories(limit: int) -> list[dict]:
    since = int((dt.datetime.now(dt.UTC) - dt.timedelta(days=30)).timestamp())
    query = "AI OR LLM OR agents OR OpenAI OR Claude"
    params = urllib.parse.urlencode({
        "query": query,
        "tags": "story",
        "numericFilters": f"created_at_i>{since},points>20",
        "hitsPerPage": limit,
    })
    data = _request_json(f"https://hn.algolia.com/api/v1/search?{params}")
    rows = []
    for item in data.get("hits", [])[:limit]:
        url = item.get("url") or f"https://news.ycombinator.com/item?id={item.get('objectID')}"
        rows.append({
            "source": "Hacker News",
            "title": item.get("title", ""),
            "score": int(item.get("points") or 0),
            "description": f"{item.get('num_comments') or 0} comments",
            "url": url,
            "created_at": item.get("created_at", ""),
        })
    return rows


def star_history_signals(limit: int) -> list[dict]:
    """Read the weekly ranking from Star History's left homepage sidebar."""
    text = _request_text("https://www.star-history.com/")
    try:
        return parse_weekly_leaderboard(text, limit)["items"]
    except ValueError as exc:
        raise HotSearchError(str(exc)) from exc


def ai(args: argparse.Namespace) -> dict:
    report = research_social_hotspots(
        "artificial intelligence AI agents",
        days=getattr(args, "days", 7),
        limit=args.limit,
    )
    items = []
    for item in report["items"]:
        engagement = item.get("engagement") or {}
        metrics = " · ".join(f"{key} {value:,}" for key, value in engagement.items() if value)
        items.append({
            **item,
            "description": metrics or item.get("body", "")[:180],
        })
    errors = [
        f"{row['source']}: {row['state']} ({row.get('detail') or 'no detail'})"
        for row in report["source_status"]
        if row["state"] not in ("ok", "no-results")
    ]
    return {
        "generated_at": report["generated_at"],
        "topic": report["topic"],
        "window_days": report["window_days"],
        "items": items,
        "source_status": report["source_status"],
        "errors": errors,
    }


def _ai_news_items(limit: int) -> list[dict]:
    args = argparse.Namespace(limit=limit, days=7)
    return [item for item in ai(args)["items"] if item.get("source") != "GitHub"][:limit]


def report(args: argparse.Namespace) -> dict:
    out = {"generated_at": _now_utc()}
    if args.repo:
        out["stars"] = snapshot(args)
    out["ai_hotspots"] = ai(args)
    return out


def _split_items(data: dict) -> tuple[list[dict], list[dict], list[dict]]:
    hotspots = data.get("ai_hotspots") or data
    items = hotspots.get("items", [])
    github = [item for item in items if item.get("source") == "GitHub"]
    hn = [item for item in items if item.get("source") not in ("GitHub", "Star History")]
    star_history = [item for item in items if item.get("source") == "Star History"]
    return github, hn, star_history


def format_message_text(data: dict, max_items: int = 10) -> str:
    generated = data.get("generated_at", _now_utc()).replace("T", " ").replace("Z", " UTC")
    github, hn, star_history = _split_items(data)
    star_growth = data.get("star_growth") or {}
    if star_growth.get("items"):
        star_history = star_growth["items"]
    ai_news = data.get("ai_news") or {}
    if ai_news.get("items"):
        hn = ai_news["items"]
    lines = [
        "🟦【Hot Search | GitHub Star & AI】",
        f"🕒 更新时间：{generated}",
        "",
    ]
    if star_history:
        lines.extend([f"🟩 GitHub Star 变化 TOP {min(max_items, len(star_history))} - 过去一周的变化", ""])
        for idx, item in enumerate(star_history[:max_items], 1):
            delta = item.get("delta") or item.get("score") or 0
            rank = item.get("rank") or idx
            movement = item.get("movement") or "–"
            detail = item.get("movement_detail") or "No rank change"
            url = item.get("github_url") or item.get("url", "")
            lines.append(f"{idx}. #{rank} {movement}  {item['title']}  +{delta}")
            lines.append(f"   {detail}")
            if url:
                lines.append(f"   {url}")
            if idx < min(max_items, len(star_history)):
                lines.append("")
        lines.append("")
    if github:
        lines.extend(["🟨 GitHub AI 热门仓库", ""])
        for idx, item in enumerate(github[:max_items], 1):
            lang = f" · {item['language']}" if item.get("language") else ""
            url = item.get("url", "")
            lines.append(f"{idx}. {item['title']}  ★ {item['score']:,}{lang}")
            if url:
                lines.append(f"   {url}")
            if idx < min(max_items, len(github)):
                lines.append("")
        lines.append("")
    if hn:
        lines.extend([f"🟥 全球社媒 AI 热点 TOP {min(max_items, len(hn))}", ""])
        for idx, item in enumerate(hn[:max_items], 1):
            url = item.get("url", "")
            lines.append(f"{idx}. {item['title']}")
            metadata = f"   热度：{item['score']}"
            if item.get("description"):
                metadata += f" · {item['description']}"
            lines.append(metadata)
            if url:
                lines.append(f"   {url}")
            if idx < min(max_items, len(hn)):
                lines.append("")
        lines.append("")
    errors = (data.get("ai_hotspots") or data).get("errors") or []
    if errors:
        lines.append("⚠️ 部分来源异常：" + "；".join(errors[:2]))
    lines.append("📌 注：小时增量需持续快照；Star History 增量来自公开榜单。")
    message = "\n".join(lines).strip()
    if len(message) <= 1900:
        return message

    # Do not slice through a URL; clients otherwise render a broken link.
    suffix = "\n\n📎 内容较长，已按完整行截断。"
    kept = []
    for line in lines:
        candidate = "\n".join([*kept, line]).strip()
        if len(candidate) + len(suffix) > 1900:
            break
        kept.append(line)
    return "\n".join(kept).rstrip() + suffix


def push_payload(limit: int) -> dict:
    errors = []
    try:
        star_growth = star_history_signals(limit)
    except HotSearchError as exc:
        star_growth = []
        errors.append(str(exc))
    try:
        ai_news_items = _ai_news_items(limit)
    except HotSearchError as exc:
        ai_news_items = []
        errors.append(str(exc))
    return {
        "generated_at": _now_utc(),
        "star_growth": {"items": star_growth, "errors": []},
        "ai_news": {"items": ai_news_items, "errors": []},
        "ai_hotspots": {"items": [], "errors": errors},
    }


def message(args: argparse.Namespace) -> dict:
    data = push_payload(args.limit) if args.push_payload else report(args)
    return {
        "generated_at": _now_utc(),
        "style": "text_cards",
        "message": format_message_text(data, max_items=args.limit),
        "data": data if args.include_data else None,
    }


def card(args: argparse.Namespace) -> dict:
    data = push_payload(args.limit)
    payload = hot_search_card(data, args.limit)
    return {
        "generated_at": _now_utc(),
        "style": "card_payload",
        "native_id": payload["native_id"],
        "card": payload["data"],
        "data": data if args.include_data else None,
    }


def _run_notify_send(args: argparse.Namespace, body: str) -> dict:
    """通过 runtime/notify.py 向目标渠道推送消息。"""
    notify = SKILL_DIR / "runtime" / "notify.py"
    python_bin = args.python or sys.executable
    channel = getattr(args, "channel", None) or os.environ.get("NOTIFY_CHANNEL", "")
    if not channel:
        raise HotSearchError(
            "需要指定推送渠道：--channel dingtalk|feishu|wecom|slack|telegram|discord|teams|generic"
        )
    cmd = [python_bin, str(notify), "send", "--channel", channel, "-m", body,
           "--timeout", str(int(getattr(args, "send_timeout", 30)))]
    # 可选：webhook / token 通过命令行透传（若已配置环境变量则无需传）
    if getattr(args, "webhook", None):
        cmd.extend(["--webhook", args.webhook])
    if getattr(args, "secret", None):
        cmd.extend(["--secret", args.secret])
    if getattr(args, "token", None):
        cmd.extend(["--token", args.token])
    if getattr(args, "chat_id", None):
        cmd.extend(["--chat-id", args.chat_id])
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=int(getattr(args, "send_timeout", 30)) + 10)
    except subprocess.TimeoutExpired as exc:
        raise HotSearchError(f"notify send timed out after {getattr(args, 'send_timeout', 30)}s") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise HotSearchError(f"notify send failed: {detail[:600]}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"raw": result.stdout.strip()}


def poll_push(args: argparse.Namespace) -> dict:
    if not args.dry_run and not args.confirm_send:
        raise HotSearchError(
            "poll-push 会真实推送消息；请先用 --dry-run 预览，确认后加 --confirm-send 正式发送"
        )
    runs = []
    interval = max(60, int(args.interval))
    count = 1 if args.once else max(1, int(args.count))
    for idx in range(count):
        data = push_payload(args.limit)
        body = format_message_text(data, max_items=args.limit)
        record = {
            "run": idx + 1,
            "generated_at": data["generated_at"],
            "message": body,
            "sent": False,
        }
        if args.dry_run:
            record["command_preview"] = _send_preview(args)
        else:
            record["send_result"] = _run_notify_send(args, body)
            record["sent"] = True
        runs.append(record)
        if idx < count - 1:
            time.sleep(interval)
    return {"generated_at": _now_utc(), "interval": interval, "runs": runs}


def _send_preview(args: argparse.Namespace) -> str:
    channel = getattr(args, "channel", None) or os.environ.get("NOTIFY_CHANNEL", "<channel>")
    return (
        f"{args.python or sys.executable} {SKILL_DIR / 'runtime' / 'notify.py'} "
        f"send --channel {channel} -m <generated-message>"
    )


def daily_push(args: argparse.Namespace) -> dict:
    """Run one unattended push using a previously reviewed config file."""
    config = _load_push_config(args.config)
    channel = config["channel"]
    limit = config.get("limit", 6)
    notify_cfg = (config.get("notify") or {}).get(channel) or {}
    if config.get("include_ai_hotspots", True):
        data = push_payload(limit)
    else:
        errors = []
        try:
            rows = star_history_signals(limit)
        except HotSearchError as exc:
            rows = []
            errors.append(str(exc))
        data = {
            "generated_at": _now_utc(),
            "star_growth": {"items": rows, "errors": errors},
            "ai_news": {"items": [], "errors": []},
            "ai_hotspots": {"items": [], "errors": errors},
        }
    body = format_message_text(data, max_items=limit)
    send_args = argparse.Namespace(
        channel=channel,
        webhook=notify_cfg.get("webhook"),
        secret=notify_cfg.get("secret"),
        token=notify_cfg.get("token"),
        chat_id=notify_cfg.get("chat_id"),
        python=_configured_python(config),
        send_timeout=int(config.get("send_timeout", 30)),
    )
    record = {
        "generated_at": data["generated_at"],
        "config": str(args.config),
        "channel": channel,
        "message": body,
        "sent": False,
    }
    if args.dry_run:
        record["command_preview"] = _send_preview(send_args)
        return record
    record["send_result"] = _run_notify_send(send_args, body)
    record["sent"] = True
    return record


def _launchd_plist_path(label: str) -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"


def schedule(args: argparse.Namespace) -> dict:
    """Install, remove, or inspect the macOS daily launchd job."""
    if sys.platform != "darwin":
        raise HotSearchError("schedule currently supports macOS launchd only")
    label = args.label
    plist_path = _launchd_plist_path(label)
    if args.action == "status":
        return {"label": label, "plist": str(plist_path), "installed": plist_path.exists()}
    if args.action == "uninstall":
        if plist_path.exists():
            subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True, text=True)
            plist_path.unlink()
        return {"label": label, "plist": str(plist_path), "installed": False}

    config = _load_push_config(args.config)
    run_at = config.get("schedule") or {}
    log_dir = DEFAULT_STATE_DIR
    log_dir.mkdir(parents=True, exist_ok=True)
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "Label": label,
        "ProgramArguments": [
            _configured_python(config),
            str(Path(__file__).resolve()),
            "daily-push",
            "--config",
            str(args.config.resolve()),
        ],
        "StartCalendarInterval": {
            "Hour": int(run_at.get("hour", 10)),
            "Minute": int(run_at.get("minute", 10)),
        },
        "RunAtLoad": False,
        "StandardOutPath": str(log_dir / "daily-push.log"),
        "StandardErrorPath": str(log_dir / "daily-push.error.log"),
    }
    with plist_path.open("wb") as handle:
        plistlib.dump(payload, handle, sort_keys=False)
    subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True, text=True)
    loaded = subprocess.run(["launchctl", "load", str(plist_path)], capture_output=True, text=True)
    if loaded.returncode != 0:
        raise HotSearchError(f"launchctl load failed: {(loaded.stderr or loaded.stdout).strip()}")
    return {
        "label": label,
        "plist": str(plist_path),
        "installed": True,
        "daily_at": f"{payload['StartCalendarInterval']['Hour']:02d}:{payload['StartCalendarInterval']['Minute']:02d}",
        "timezone": "system local timezone",
    }


def _print_markdown(data: dict) -> None:
    print(f"# Hot Search Report\n\nGenerated: {data.get('generated_at', _now_utc())}\n")
    stars = data.get("stars")
    if stars:
        print("## GitHub Star Changes")
        for row in stars["repos"]:
            delta = row.get("delta_since_last_snapshot", 0)
            sign = "+" if delta > 0 else ""
            print(f"- **{row['name']}**: {row['stars']} stars ({sign}{delta} since last snapshot), {row['language'] or 'unknown language'}")
            if "stars_per_hour" in row:
                print(f"  Hourly: {row['stars_per_hour']} stars/hour over {row['elapsed_hours']}h")
            print(f"  Chart: {row['star_history_url']}")
        print()
    if data.get("repos") and data.get("hours") is not None:
        print(f"## GitHub Hourly Star Changes ({data['hours']}h window)")
        for row in data["repos"]:
            if row.get("status") == "ok":
                print(f"- **{row['repo']}**: {row['delta']:+d} stars, {row['stars_per_hour']} stars/hour")
            else:
                print(f"- **{row['repo']}**: {row['status']}")
        print()
    hotspots = data.get("ai_hotspots") or data
    if hotspots.get("items"):
        print("## Global AI Hotspots")
        for item in hotspots["items"]:
            score = item.get("score")
            suffix = f" - score {score}" if score else ""
            print(f"- **[{item['source']}] {item['title']}**{suffix}")
            if item.get("delta"):
                print(f"  Delta: +{item['delta']}")
            if item.get("description"):
                print(f"  {item['description']}")
            if item.get("url"):
                print(f"  {item['url']}")
        print()
    if hotspots.get("errors"):
        print("## Partial Coverage")
        for error in hotspots["errors"]:
            print(f"- {error}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Monitor GitHub stars and global AI hotspots.")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_FILE, help="local snapshot state file")
    parser.add_argument("--format", choices=["json", "markdown"], default="markdown")
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add_common_options(command: argparse.ArgumentParser) -> None:
        # Suppressed defaults preserve values supplied before the subcommand.
        command.add_argument("--state", type=Path, default=argparse.SUPPRESS, help="local snapshot state file")
        command.add_argument(
            "--format",
            choices=["json", "markdown"],
            default=argparse.SUPPRESS,
            help="output format",
        )

    p_snapshot = sub.add_parser("snapshot", help="fetch repo stars once and save a local snapshot")
    add_common_options(p_snapshot)
    p_snapshot.add_argument("--repo", action="append", type=_parse_repo, help="GitHub repo owner/name; repeatable")
    p_snapshot.set_defaults(func=snapshot)

    p_watch = sub.add_parser("watch", help="repeat snapshots to detect changes")
    add_common_options(p_watch)
    p_watch.add_argument("--repo", action="append", type=_parse_repo, help="GitHub repo owner/name; repeatable")
    p_watch.add_argument("--interval", type=int, default=300, help="seconds between snapshots, minimum 10")
    p_watch.add_argument("--count", type=int, default=2, help="number of snapshots")
    p_watch.set_defaults(func=watch)

    p_hourly = sub.add_parser("hourly", help="rank tracked repos by local hourly star delta")
    add_common_options(p_hourly)
    p_hourly.add_argument("--repo", action="append", type=_parse_repo, help="GitHub repo owner/name; repeatable")
    p_hourly.add_argument("--hours", type=float, default=1.0, help="lookback window in hours")
    p_hourly.add_argument("--refresh", action="store_true", help="take a fresh snapshot before ranking")
    p_hourly.set_defaults(func=hourly)

    p_ai = sub.add_parser("ai", help="collect global AI hotspots")
    add_common_options(p_ai)
    p_ai.add_argument("--limit", type=int, default=10)
    p_ai.add_argument("--days", type=int, default=7, help="social research lookback window")
    p_ai.set_defaults(func=ai)

    p_report = sub.add_parser("report", help="produce combined repo star and AI hotspot report")
    add_common_options(p_report)
    p_report.add_argument("--repo", action="append", type=_parse_repo, help="GitHub repo owner/name; repeatable")
    p_report.add_argument("--limit", type=int, default=10)
    p_report.set_defaults(func=report)

    p_message = sub.add_parser("message", help="produce colored text cards for chat push")
    add_common_options(p_message)
    p_message.add_argument("--repo", action="append", type=_parse_repo, help="GitHub repo owner/name; repeatable")
    p_message.add_argument("--limit", type=int, default=6)
    p_message.add_argument("--include-data", action="store_true")
    p_message.add_argument("--push-payload", action="store_true", help="message uses star-growth top N plus AI-news top N")
    p_message.set_defaults(func=message)

    p_card = sub.add_parser("card", help="produce a structured hot-search card payload (JSON)")
    add_common_options(p_card)
    p_card.add_argument("--limit", type=int, default=6)
    p_card.add_argument("--include-data", action="store_true")
    p_card.set_defaults(func=card)

    p_poll_push = sub.add_parser("poll-push", help="poll and push hot-search report to a chat channel")
    add_common_options(p_poll_push)
    p_poll_push.add_argument(
        "--channel", required=True,
        choices=["dingtalk", "feishu", "wecom", "slack", "telegram", "discord", "teams", "generic"],
        help="目标推送渠道",
    )
    p_poll_push.add_argument("--webhook", default=None, help="Webhook URL（也可用环境变量）")
    p_poll_push.add_argument("--secret", default=None, help="钉钉加签 secret")
    p_poll_push.add_argument("--token", default=None, help="Telegram Bot token")
    p_poll_push.add_argument("--chat-id", dest="chat_id", default=None, help="Telegram chat_id")
    p_poll_push.add_argument("--limit", type=int, default=10, help="top N star growth and top N AI news")
    p_poll_push.add_argument("--interval", type=int, default=3600, help="seconds between pushes; minimum 60")
    p_poll_push.add_argument("--count", type=int, default=24, help="number of polling pushes")
    p_poll_push.add_argument("--once", action="store_true", help="run one push cycle")
    p_poll_push.add_argument("--dry-run", action="store_true", help="generate without sending")
    p_poll_push.add_argument("--confirm-send", action="store_true", help="required for real sends after user confirmation")
    p_poll_push.add_argument("--python", default=sys.executable, help="python executable for runtime/notify.py")
    p_poll_push.add_argument("--send-timeout", type=int, default=30)
    p_poll_push.set_defaults(func=poll_push)

    p_daily = sub.add_parser("daily-push", help="run one unattended push from a reviewed JSON config")
    add_common_options(p_daily)
    p_daily.add_argument("--config", type=Path, default=DEFAULT_PUSH_CONFIG)
    p_daily.add_argument("--dry-run", action="store_true")
    p_daily.set_defaults(func=daily_push)

    p_schedule = sub.add_parser("schedule", help="manage the macOS launchd daily push job")
    add_common_options(p_schedule)
    p_schedule.add_argument("action", choices=["install", "uninstall", "status"])
    p_schedule.add_argument("--config", type=Path, default=DEFAULT_PUSH_CONFIG)
    p_schedule.add_argument("--label", default=DEFAULT_LAUNCHD_LABEL)
    p_schedule.set_defaults(func=schedule)

    args = parser.parse_args()
    try:
        data = args.func(args)
    except HotSearchError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    if getattr(args, "cmd", "") == "message" and args.format == "markdown":
        print(data["message"])
    elif getattr(args, "cmd", "") == "card":
        print(json.dumps(data, ensure_ascii=False, indent=2))
    elif getattr(args, "cmd", "") in {"daily-push", "schedule"} or args.format == "json":
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        _print_markdown(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
