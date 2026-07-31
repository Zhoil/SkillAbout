#!/usr/bin/env python3
"""Send a pre-built digest file via a configurable push adapter.

Supported adapters (set push.adapter in config):
  shell          — run an arbitrary shell command; message passed via stdin
  wecom_bot      — 企业微信群机器人 webhook (markdown or text)
  slack_webhook  — Slack incoming webhook
  bark           — Bark iOS push (https://bark.day.app)
  feishu_bot     — 飞书群机器人 webhook
  dingtalk_bot   — 钉钉群机器人 webhook (text)

The adapter reads push.target from the config for its parameters.
Call this script only after the user has confirmed the full message preview.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen


# ---------------------------------------------------------------------------
# Adapter implementations
# ---------------------------------------------------------------------------

def _post_json(url: str, payload: dict[str, Any], timeout: int = 30) -> str:
    body = json.dumps(payload, ensure_ascii=False).encode()
    req = Request(url, data=body, headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def adapter_shell(message: str, target: dict[str, Any]) -> None:
    """Pass message via stdin to an arbitrary shell command."""
    command = target.get("command")
    if not command:
        raise ValueError("push.target.command is required for adapter=shell")
    args = target.get("args", [])
    if not isinstance(args, list):
        raise ValueError("push.target.args must be a list")
    result = subprocess.run(
        [command, *args],
        input=message,
        text=True,
        capture_output=True,
    )
    if result.stdout:
        print(result.stdout, end="")
    if result.returncode != 0:
        raise RuntimeError(
            f"shell command exited {result.returncode}: {result.stderr.strip()}"
        )


def adapter_wecom_bot(message: str, target: dict[str, Any]) -> None:
    """企业微信群机器人 webhook。"""
    webhook_url = target.get("webhook_url")
    if not webhook_url:
        raise ValueError("push.target.webhook_url is required for adapter=wecom_bot")
    msg_type = target.get("msg_type", "text")
    if msg_type == "markdown":
        payload: dict[str, Any] = {"msgtype": "markdown", "markdown": {"content": message}}
    else:
        payload = {"msgtype": "text", "text": {"content": message}}
    resp = _post_json(webhook_url, payload)
    data = json.loads(resp)
    if data.get("errcode", 0) != 0:
        raise RuntimeError(f"wecom_bot error {data.get('errcode')}: {data.get('errmsg')}")


def adapter_slack_webhook(message: str, target: dict[str, Any]) -> None:
    """Slack incoming webhook."""
    webhook_url = target.get("webhook_url")
    if not webhook_url:
        raise ValueError("push.target.webhook_url is required for adapter=slack_webhook")
    resp = _post_json(webhook_url, {"text": message})
    if resp.strip() != "ok":
        raise RuntimeError(f"slack_webhook unexpected response: {resp!r}")


def adapter_bark(message: str, target: dict[str, Any]) -> None:
    """Bark iOS push notification (https://bark.day.app)."""
    url = target.get("url")
    if not url:
        raise ValueError("push.target.url is required for adapter=bark (e.g. https://api.day.app/<key>/)")
    title = target.get("title", "AI Hotspot Digest")
    payload = {"title": title, "body": message}
    if target.get("group"):
        payload["group"] = target["group"]
    resp = _post_json(url.rstrip("/") + "/push", payload)
    data = json.loads(resp)
    if data.get("code") != 200:
        raise RuntimeError(f"bark error: {data.get('message', resp)}")


def adapter_feishu_bot(message: str, target: dict[str, Any]) -> None:
    """飞书群机器人 webhook。"""
    webhook_url = target.get("webhook_url")
    if not webhook_url:
        raise ValueError("push.target.webhook_url is required for adapter=feishu_bot")
    payload: dict[str, Any] = {
        "msg_type": "text",
        "content": {"text": message},
    }
    resp = _post_json(webhook_url, payload)
    data = json.loads(resp)
    if data.get("code", 0) != 0:
        raise RuntimeError(f"feishu_bot error {data.get('code')}: {data.get('msg')}")


def adapter_dingtalk_bot(message: str, target: dict[str, Any]) -> None:
    """钉钉群机器人 webhook。"""
    webhook_url = target.get("webhook_url")
    if not webhook_url:
        raise ValueError("push.target.webhook_url is required for adapter=dingtalk_bot")
    payload: dict[str, Any] = {
        "msgtype": "text",
        "text": {"content": message},
    }
    at = target.get("at", {})
    if at:
        payload["at"] = at
    resp = _post_json(webhook_url, payload)
    data = json.loads(resp)
    if data.get("errcode", 0) != 0:
        raise RuntimeError(f"dingtalk_bot error {data.get('errcode')}: {data.get('errmsg')}")


ADAPTERS = {
    "shell": adapter_shell,
    "wecom_bot": adapter_wecom_bot,
    "slack_webhook": adapter_slack_webhook,
    "bark": adapter_bark,
    "feishu_bot": adapter_feishu_bot,
    "dingtalk_bot": adapter_dingtalk_bot,
}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Send a digest file via the adapter configured in push.adapter."
    )
    parser.add_argument("--config", required=True, type=Path,
                        help="Path to digest config JSON (must contain push.adapter and push.target).")
    parser.add_argument("--message-file", required=True, type=Path,
                        help="Path to the pre-built digest text file.")
    args = parser.parse_args()

    try:
        config: dict[str, Any] = json.loads(args.config.read_text(encoding="utf-8"))
        push = config.get("push", {})
        adapter_name: str = push.get("adapter", "")
        target: dict[str, Any] = push.get("target", {})

        if not adapter_name:
            raise ValueError("push.adapter is required in config")
        if adapter_name not in ADAPTERS:
            raise ValueError(
                f"Unknown adapter {adapter_name!r}. "
                f"Available: {', '.join(sorted(ADAPTERS))}"
            )

        message = args.message_file.read_text(encoding="utf-8")
        if not message.strip():
            raise ValueError(f"Message file is empty: {args.message_file}")

        ADAPTERS[adapter_name](message, target)
        print(json.dumps({"status": "sent", "adapter": adapter_name}, ensure_ascii=False))
        return 0

    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
