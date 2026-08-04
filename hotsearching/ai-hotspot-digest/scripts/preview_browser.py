"""Open generated dashboards through a local no-cache preview server."""

from __future__ import annotations

import json
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.parse import quote
from urllib.request import urlopen
import webbrowser
import zlib


HOST = "127.0.0.1"


def server_directory(port: int) -> Path | None:
    try:
        with urlopen(f"http://{HOST}:{port}/__health__", timeout=0.3) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return Path(payload["directory"]).resolve()
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return None


def port_available(port: int) -> bool:
    with socket.socket() as probe:
        try:
            probe.bind((HOST, port))
            return True
        except OSError:
            return False


def ensure_preview_server(directory: Path) -> str | None:
    resolved = directory.resolve()
    first_port = 8400 + zlib.crc32(str(resolved).encode("utf-8")) % 400
    server_script = Path(__file__).with_name("preview_server.py")
    for offset in range(20):
        port = first_port + offset
        if server_directory(port) == resolved:
            return f"http://{HOST}:{port}"
        if not port_available(port):
            continue
        subprocess.Popen(
            [sys.executable, str(server_script), "--directory", str(resolved), "--port", str(port)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        for _ in range(30):
            if server_directory(port) == resolved:
                return f"http://{HOST}:{port}"
            time.sleep(0.05)
    return None


def dashboard_url(path: Path) -> str:
    resolved = path.resolve()
    base_url = ensure_preview_server(resolved.parent)
    if base_url:
        return f"{base_url}/{quote(resolved.name)}"
    return resolved.as_uri()


def open_dashboard(path: Path) -> bool:
    try:
        return webbrowser.open(dashboard_url(path), new=2)
    except (OSError, webbrowser.Error):
        return False
