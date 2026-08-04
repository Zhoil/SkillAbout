#!/usr/bin/env python3
"""Serve generated previews locally with caching disabled."""

from __future__ import annotations

import argparse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path


class PreviewHandler(SimpleHTTPRequestHandler):
    server_version = "AIHotspotPreview/1.0"

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def do_GET(self) -> None:
        if self.path.split("?", 1)[0] == "/__health__":
            payload = json.dumps({"directory": str(Path(self.directory).resolve())}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        super().do_GET()

    def log_message(self, _format: str, *args: object) -> None:
        return


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--port", required=True, type=int)
    args = parser.parse_args()
    directory = args.directory.expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    handler = lambda *handler_args, **kwargs: PreviewHandler(
        *handler_args, directory=str(directory), **kwargs
    )
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
