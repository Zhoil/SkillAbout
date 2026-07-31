#!/usr/bin/env python3
"""Generate digest previews once or on a daily schedule. Never sends messages."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any


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
    return config


def next_run(now: datetime, schedule_time: str) -> datetime:
    local_now = now.astimezone(GMT_PLUS_8)
    parsed = datetime.strptime(schedule_time, "%H:%M:%S").time()
    candidate = datetime.combine(local_now.date(), parsed, tzinfo=GMT_PLUS_8)
    return candidate if candidate > local_now else candidate + timedelta(days=1)


def resolve_path(value: str, schedule_file: Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (schedule_file.parent.parent / path).resolve()


def generate_preview(schedule_file: Path, config: dict[str, Any], now: datetime | None = None) -> Path:
    current = (now or datetime.now(timezone.utc)).astimezone(GMT_PLUS_8)
    output_dir = resolve_path(config["output_dir"], schedule_file)
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / current.strftime("digest-%Y%m%d-%H%M%S.txt")
    builder = Path(__file__).with_name("build_digest.py")
    command = [
        sys.executable,
        str(builder),
        "--config", str(resolve_path(config["digest_config"], schedule_file)),
        "--last30days-file", str(resolve_path(config["last30days_file"], schedule_file)),
        "--annotations-file", str(resolve_path(config["annotations_file"], schedule_file)),
        "--output", str(output),
    ]
    subprocess.run(command, check=True)
    latest = output_dir / "latest.txt"
    latest_tmp = output_dir / ".latest.txt.tmp"
    latest_tmp.write_bytes(output.read_bytes())
    latest_tmp.replace(latest)
    return output


def send_reminder(preview: Path) -> str:
    return f"预览已生成，请确认内容后发送：{preview}"


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
            print(generate_preview(args.schedule.resolve(), config))
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
