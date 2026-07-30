#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from lib.pipeline import research
from lib.render import text, markdown, visual_html


def main() -> int:
    parser = argparse.ArgumentParser(description="Research global AI conversation across public social sources.")
    parser.add_argument("topic", nargs="?", default="artificial intelligence AI agents")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--sources", default="reddit,bluesky,hackernews,github")
    parser.add_argument("--format", choices=["json", "markdown", "text", "html"], default="markdown",
                        help="text: compact card format suitable for any chat push channel")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = research(args.topic, args.days, args.limit, [part.strip() for part in args.sources.split(",") if part.strip()])
    if args.format == "json":
        rendered = json.dumps(report, ensure_ascii=False, indent=2)
    elif args.format == "text":
        rendered = text(report, args.limit)
    elif args.format == "html":
        rendered = visual_html(report, args.limit)
    else:
        rendered = markdown(report, args.limit)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(json.dumps({"output": str(args.output), "items": len(report["items"]), "source_status": report["source_status"]}, ensure_ascii=False, indent=2))
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
