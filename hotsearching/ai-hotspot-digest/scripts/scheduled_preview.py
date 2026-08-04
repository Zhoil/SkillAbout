#!/usr/bin/env python3
"""Generate digest previews once or on a daily schedule. Never sends messages.

When schedule.fetch_last30days is true, each run first calls fetch_hotspots.py
to refresh the last30days JSON file using the search parameters in the digest
config, then builds the digest from the freshly fetched result.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any
import webbrowser


GMT_PLUS_8 = timezone(timedelta(hours=8))


def load_schedule(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    if config.get("timezone") != "GMT+8":
        raise ValueError("schedule.timezone must be GMT+8")
    try:
        datetime.strptime(config["time"], "%H:%M:%S")
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("schedule.time must use HH:MM:SS") from exc
    for key in ("digest_config", "last30days_file", "annotations_file", "output_dir"):
        if not isinstance(config.get(key), str) or not config[key].strip():
            raise ValueError(f"schedule.{key} must be a non-empty path")
    # Validate fetch_last30days dependencies when the option is enabled.
    if config.get("fetch_last30days"):
        skill_dir = config.get("last30days_skill_dir", "")
        if not isinstance(skill_dir, str) or not skill_dir.strip():
            raise ValueError(
                "schedule.last30days_skill_dir must be set when fetch_last30days is true"
            )
    return config


def next_run(now: datetime, schedule_time: str) -> datetime:
    local_now = now.astimezone(GMT_PLUS_8)
    parsed = datetime.strptime(schedule_time, "%H:%M:%S").time()
    candidate = datetime.combine(local_now.date(), parsed, tzinfo=GMT_PLUS_8)
    return candidate if candidate > local_now else candidate + timedelta(days=1)


def resolve_path(value: str, schedule_file: Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (schedule_file.parent.parent / path).resolve()


def fetch_hotspots(
    schedule_file: Path,
    config: dict[str, Any],
    output_path: Path,
) -> None:
    """Refresh the last30days JSON file via fetch_hotspots.py."""
    skill_dir = resolve_path(config["last30days_skill_dir"], schedule_file)
    digest_config = resolve_path(config["digest_config"], schedule_file)
    fetcher = Path(__file__).with_name("fetch_hotspots.py")
    command = [
        sys.executable,
        str(fetcher),
        "--config", str(digest_config),
        "--skill-dir", str(skill_dir),
        "--output", str(output_path),
    ]
    subprocess.run(command, check=True)


def generate_preview(schedule_file: Path, config: dict[str, Any], now: datetime | None = None) -> Path:
    current = (now or datetime.now(timezone.utc)).astimezone(GMT_PLUS_8)
    output_dir = resolve_path(config["output_dir"], schedule_file)
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / current.strftime("digest-%Y%m%d-%H%M%S.txt")

    last30days_path = resolve_path(config["last30days_file"], schedule_file)

    # Optionally refresh the last30days JSON before building the digest.
    if config.get("fetch_last30days"):
        fetch_hotspots(schedule_file, config, last30days_path)

    builder = Path(__file__).with_name("build_digest.py")
    command = [
        sys.executable,
        str(builder),
        "--config", str(resolve_path(config["digest_config"], schedule_file)),
        "--last30days-file", str(last30days_path),
        "--annotations-file", str(resolve_path(config["annotations_file"], schedule_file)),
        "--output", str(output),
        "--no-open-dashboard",
    ]
    subprocess.run(command, check=True)
    latest = output_dir / "latest.txt"
    latest_tmp = output_dir / ".latest.txt.tmp"
    latest_tmp.write_bytes(output.read_bytes())
    latest_tmp.replace(latest)
    dashboard = output.with_suffix(".html")
    if dashboard.exists():
        latest_dashboard = output_dir / "latest.html"
        latest_dashboard_tmp = output_dir / ".latest.html.tmp"
        latest_dashboard_tmp.write_bytes(dashboard.read_bytes())
        latest_dashboard_tmp.replace(latest_dashboard)
    return output


def send_reminder(preview: Path) -> str:
    return f"预览已生成，请确认内容后发送：{preview}"


def open_preview_dashboard(preview: Path) -> bool:
    dashboard = preview.with_suffix(".html")
    try:
        return webbrowser.open(dashboard.resolve().as_uri(), new=2)
    except (OSError, webbrowser.Error):
        return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate digest previews on a schedule. Never sends messages."
    )
    parser.add_argument("--schedule", required=True, type=Path)
    parser.add_argument("--run-once", action="store_true",
                        help="Generate one preview immediately and exit.")
    args = parser.parse_args()
    try:
        config = load_schedule(args.schedule)
        if args.run_once:
            preview = generate_preview(args.schedule.resolve(), config)
            opened = open_preview_dashboard(preview)
            print(preview)
            if not opened:
                print(f"dashboard generated but browser did not open: {preview.with_suffix('.html')}", file=sys.stderr)
            return 0
        if not config.get("enabled", False):
            raise ValueError("schedule.enabled is false")
        while True:
            run_at = next_run(datetime.now(timezone.utc), config["time"])
            print(f"next preview: {run_at.strftime('%Y-%m-%d %H:%M:%S GMT+8')}", flush=True)
            time.sleep(max(0.0, (run_at - datetime.now(GMT_PLUS_8)).total_seconds()))
            try:
                preview = generate_preview(args.schedule.resolve(), config)
                print(f"generated: {preview}", flush=True)
                print(send_reminder(preview), flush=True)
            except (OSError, subprocess.CalledProcessError, ValueError) as exc:
                print(f"preview failed: {exc}", file=sys.stderr, flush=True)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
