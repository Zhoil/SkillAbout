#!/usr/bin/env python3
"""Invoke the last30days engine and save its JSON output to a file.

Reads search configuration from the digest config's ``last30days`` section so
topic, days, depth, source list, and subreddit hints are all driven by one
config file rather than hard-coded in shell scripts or SKILL.md.

Usage::

    python3 scripts/fetch_hotspots.py \\
        --config <digest-config.json> \\
        --skill-dir <path-to-last30days-dir> \\
        --output <output.json>

The ``last30days`` section of the config supports:

    topic               Search topic / keyword string (default: "R&D tools developer skills AI agent")
    days                Lookback window in days (default: 30)
    depth               Retrieval depth: default | quick | deep (default: default)
    search              Comma-separated source names (default: engine default)
    subreddits          Comma-separated subreddit names without r/ prefix
    dedicated_subreddits  Comma-separated entity-home subreddits (full pull, no relevance floor)
    x_handle            X/Twitter handle for targeted search
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


DEFAULT_TOPIC = "R&D tools developer skills AI agent"
DEFAULT_DAYS = 30
DEFAULT_DEPTH = "default"


def load_last30days_config(config_path: Path) -> dict[str, Any]:
    """Read the ``last30days`` section from the digest config."""
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    section = raw.get("last30days", {})
    if not isinstance(section, dict):
        raise ValueError("config.last30days must be a JSON object")
    return section


def build_command(
    skill_dir: Path,
    output_path: Path,
    section: dict[str, Any],
) -> list[str]:
    """Build the last30days CLI command from the config section."""
    topic = str(section.get("topic") or "").strip() or DEFAULT_TOPIC
    days = section.get("days")
    try:
        days = int(days) if days is not None else DEFAULT_DAYS
        if days <= 0:
            raise ValueError("days must be positive")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"last30days.days: {exc}") from exc

    depth_raw = str(section.get("depth") or "").strip().lower() or DEFAULT_DEPTH
    if depth_raw not in ("default", "quick", "deep"):
        raise ValueError(
            f"last30days.depth must be one of default/quick/deep, got {depth_raw!r}"
        )

    engine = skill_dir / "scripts" / "last30days.py"
    if not engine.exists():
        raise FileNotFoundError(
            f"last30days engine not found at {engine}. "
            "Set last30days_skill_dir to the directory containing scripts/last30days.py."
        )

    cmd = [
        sys.executable,
        str(engine),
        topic,
        "--emit=json",
        f"--days={days}",
        f"--save-dir={output_path.parent}",
    ]

    if depth_raw == "quick":
        cmd.append("--quick")
    elif depth_raw == "deep":
        cmd.append("--deep")

    search = str(section.get("search") or "").strip()
    if search:
        cmd.append(f"--search={search}")

    subreddits = str(section.get("subreddits") or "").strip()
    if subreddits:
        cmd.append(f"--subreddits={subreddits}")

    dedicated = str(section.get("dedicated_subreddits") or "").strip()
    if dedicated:
        cmd.append(f"--dedicated-subreddits={dedicated}")

    x_handle = str(section.get("x_handle") or "").strip().lstrip("@")
    if x_handle:
        cmd.append(f"--x-handle={x_handle}")

    return cmd


def run_fetch(
    skill_dir: Path,
    output_path: Path,
    section: dict[str, Any],
    *,
    timeout: int = 600,
) -> None:
    """Run the last30days engine and write the JSON result to ``output_path``."""
    cmd = build_command(skill_dir, output_path, section)
    # last30days writes JSON on stdout when --emit=json is set; we capture it
    # and write to the requested output path ourselves so the file location is
    # deterministic regardless of engine save conventions.
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        stderr_tail = result.stderr.strip()[-2000:] if result.stderr else ""
        raise RuntimeError(
            f"last30days exited {result.returncode}.\n{stderr_tail}"
        )
    stdout = result.stdout.strip()
    if not stdout:
        raise RuntimeError("last30days produced no output on stdout")
    # Validate that the output is parseable JSON before writing.
    try:
        json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"last30days stdout is not valid JSON: {exc}\n"
            f"First 500 chars: {stdout[:500]}"
        ) from exc
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(stdout, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch last30days hotspots and write JSON to --output."
    )
    parser.add_argument(
        "--config", required=True, type=Path,
        help="Digest config JSON (reads last30days section for search parameters).",
    )
    parser.add_argument(
        "--skill-dir", required=True, type=Path,
        help="Directory containing the last30days skill (must have scripts/last30days.py).",
    )
    parser.add_argument(
        "--output", required=True, type=Path,
        help="Destination JSON file for the last30days result.",
    )
    parser.add_argument(
        "--timeout", type=int, default=600,
        help="Timeout in seconds for the last30days engine (default: 600).",
    )
    args = parser.parse_args()

    try:
        section = load_last30days_config(args.config)
        run_fetch(
            skill_dir=args.skill_dir.expanduser().resolve(),
            output_path=args.output.expanduser().resolve(),
            section=section,
            timeout=args.timeout,
        )
        topic = str(section.get("topic") or "").strip() or DEFAULT_TOPIC
        print(json.dumps({
            "output": str(args.output.expanduser().resolve()),
            "topic": topic,
            "days": section.get("days") or DEFAULT_DAYS,
        }, ensure_ascii=False))
        return 0
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
