from __future__ import annotations

import json
import time
import urllib.error
import urllib.request


class SourceError(RuntimeError):
    def __init__(self, message: str, state: str = "error") -> None:
        super().__init__(message)
        self.state = state


def _request(url: str, *, headers: dict[str, str] | None = None, timeout: int = 20) -> bytes:
    request_headers = {
        "Accept": "application/json",
        "User-Agent": "hot-search-skill/0.2 (+social-trends)",
        **(headers or {}),
    }
    request = urllib.request.Request(url, headers=request_headers)
    for attempt in range(2):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            state = "rate-limited" if exc.code in (403, 429) else "error"
            raise SourceError(f"HTTP {exc.code}: {url}", state) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if attempt == 0:
                time.sleep(0.4)
                continue
            raise SourceError(f"network error: {exc}", "unreachable") from exc
    raise SourceError(f"network error: {url}", "unreachable")


def get_json(url: str, *, headers: dict[str, str] | None = None, timeout: int = 20) -> dict:
    try:
        return json.loads(_request(url, headers=headers, timeout=timeout).decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise SourceError(f"invalid JSON: {url}", "schema-drift") from exc


def get_text(url: str, *, headers: dict[str, str] | None = None, timeout: int = 20) -> str:
    return _request(url, headers=headers, timeout=timeout).decode("utf-8", errors="replace")
