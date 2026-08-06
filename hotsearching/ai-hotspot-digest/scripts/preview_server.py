#!/usr/bin/env python3
"""Serve generated previews locally with caching disabled."""

from __future__ import annotations

import argparse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import threading
from typing import Any


REFRESH_MANIFEST = ".preview-refresh.json"


class RefreshController:
    def __init__(self, directory: Path):
        self.preview_directory = directory
        self.refresh_lock = threading.Lock()
        self.refresh_state: dict[str, str] = {"state": "idle"}

    def manifest(self) -> dict[str, Any] | None:
        path = self.preview_directory / REFRESH_MANIFEST
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def authorized(self, token: str) -> bool:
        manifest = self.manifest()
        return bool(manifest and token and token == manifest.get("token"))

    def start_refresh(self) -> bool:
        with self.refresh_lock:
            if self.refresh_state.get("state") == "running":
                return False
            self.refresh_state = {"state": "running"}
        threading.Thread(target=self._run_refresh, daemon=True).start()
        return True

    def _run_refresh(self) -> None:
        manifest = self.manifest() or {}
        schedule_file = Path(str(manifest.get("schedule_file") or "")).expanduser()
        script = Path(__file__).with_name("scheduled_preview.py")
        if not schedule_file.is_file():
            with self.refresh_lock:
                self.refresh_state = {"state": "failed", "error": "refresh schedule is unavailable"}
            return
        command = [
            sys.executable,
            str(script),
            "--schedule", str(schedule_file.resolve()),
            "--run-once",
            "--no-open-dashboard",
        ]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=900)
            if result.returncode:
                error = (result.stderr or result.stdout or "refresh command failed").strip()[-1000:]
                state = {"state": "failed", "error": error}
            else:
                state = {"state": "complete"}
        except (OSError, subprocess.TimeoutExpired) as exc:
            state = {"state": "failed", "error": str(exc)}
        with self.refresh_lock:
            self.refresh_state = state


class PreviewServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], handler: Any, directory: Path):
        super().__init__(server_address, handler)
        self.preview_directory = directory
        self.refresh = RefreshController(directory)


class PreviewHandler(SimpleHTTPRequestHandler):
    server_version = "AIHotspotPreview/1.0"

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def do_GET(self) -> None:
        route = self.path.split("?", 1)[0]
        if route == "/__health__":
            payload = json.dumps({
                "directory": str(Path(self.directory).resolve()),
                "version": 2,
                "refresh": True,
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if any(part.startswith(".") for part in route.split("/") if part):
            self.send_error(404)
            return
        if route == "/__refresh_status__":
            server = self.server
            token = self.headers.get("X-Refresh-Token", "")
            if not isinstance(server, PreviewServer) or not server.refresh.authorized(token):
                self.send_error(403)
                return
            with server.refresh.refresh_lock:
                payload = json.dumps(server.refresh.refresh_state, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        super().do_GET()

    def do_POST(self) -> None:
        if self.path.split("?", 1)[0] != "/__refresh__":
            self.send_error(404)
            return
        server = self.server
        token = self.headers.get("X-Refresh-Token", "")
        if not isinstance(server, PreviewServer) or not server.refresh.authorized(token):
            self.send_error(403)
            return
        started = server.refresh.start_refresh()
        payload = json.dumps({"state": "running", "started": started}).encode("utf-8")
        self.send_response(202)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

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
    server = PreviewServer(("127.0.0.1", args.port), handler, directory)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
