#!/usr/bin/env python3
"""hot-search-skill 通用消息推送 CLI

支持市面上主流桌面聊天软件的 webhook / bot 推送：
  - DingTalk   钉钉自定义机器人（webhook + 签名）
  - Feishu     飞书自定义机器人（webhook）
  - WeCom      企业微信群机器人（webhook）
  - Slack      Slack Incoming Webhook
  - Telegram   Telegram Bot（token + chat_id）
  - Discord    Discord Webhook
  - Teams      Microsoft Teams Incoming Webhook
  - Generic    任意 HTTP webhook（POST JSON body）

用法：
  python3 runtime/notify.py send --channel dingtalk --webhook <url> -m "内容"
  python3 runtime/notify.py send --channel feishu   --webhook <url> -m "内容"
  python3 runtime/notify.py send --channel wecom    --webhook <url> -m "内容"
  python3 runtime/notify.py send --channel slack    --webhook <url> -m "内容"
  python3 runtime/notify.py send --channel telegram --token <token> --chat-id <id> -m "内容"
  python3 runtime/notify.py send --channel discord  --webhook <url> -m "内容"
  python3 runtime/notify.py send --channel teams    --webhook <url> -m "内容"
  python3 runtime/notify.py send --channel generic  --webhook <url> [-m "内容"] [--payload-json '{}']
  python3 runtime/notify.py send --message-file <path> --channel feishu --webhook <url>

也可以通过环境变量传递凭证（见 .env.example），避免在命令行暴露 URL/token。
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


# ---------------------------------------------------------------------------
# 环境变量读取（凭证优先从环境变量注入，命令行参数可覆盖）
# ---------------------------------------------------------------------------

_SKILL_ENV = Path(__file__).resolve().parent.parent / ".env"


def _load_env() -> None:
    for env_path in [_SKILL_ENV]:
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


_load_env()


def _env(key: str, cli_val: str | None = None) -> str | None:
    return cli_val or os.environ.get(key) or None


# ---------------------------------------------------------------------------
# HTTP 发送工具
# ---------------------------------------------------------------------------

def _http_post(url: str, payload: dict, headers: dict | None = None, timeout: int = 30) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req_headers = {"Content-Type": "application/json; charset=utf-8"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, data=data, headers=req_headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"network error: {exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError(f"request timed out ({timeout}s)") from exc
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {"raw": body}


# ---------------------------------------------------------------------------
# 各渠道适配器
# ---------------------------------------------------------------------------

def _send_dingtalk(webhook: str, secret: str | None, message: str, timeout: int) -> dict:
    """钉钉自定义机器人（加签安全验证）。

    webhook 格式：https://oapi.dingtalk.com/robot/send?access_token=<token>
    若配置了签名（secret），则追加 timestamp + sign 查询参数。
    消息类型：text（兼容性最好）。
    """
    url = webhook
    if secret:
        ts = str(int(time.time() * 1000))
        sign_str = f"{ts}\n{secret}"
        sign = base64.b64encode(
            hmac.new(secret.encode("utf-8"), sign_str.encode("utf-8"), hashlib.sha256).digest()
        ).decode("utf-8")
        url = f"{webhook}&timestamp={ts}&sign={urllib.parse.quote(sign, safe='')}"
    payload = {"msgtype": "text", "text": {"content": message}}
    result = _http_post(url, payload, timeout=timeout)
    if result.get("errcode", 0) != 0:
        raise RuntimeError(f"DingTalk error {result.get('errcode')}: {result.get('errmsg')}")
    return {"success": True, "channel": "dingtalk", "response": result}


def _send_feishu(webhook: str, message: str, timeout: int) -> dict:
    """飞书自定义机器人。

    消息类型：text。
    """
    payload = {"msg_type": "text", "content": {"text": message}}
    result = _http_post(webhook, payload, timeout=timeout)
    if result.get("code", 0) != 0:
        raise RuntimeError(f"Feishu error {result.get('code')}: {result.get('msg')}")
    return {"success": True, "channel": "feishu", "response": result}


def _send_wecom(webhook: str, message: str, timeout: int) -> dict:
    """企业微信群机器人。

    消息类型：text。
    """
    payload = {"msgtype": "text", "text": {"content": message}}
    result = _http_post(webhook, payload, timeout=timeout)
    if result.get("errcode", 0) != 0:
        raise RuntimeError(f"WeCom error {result.get('errcode')}: {result.get('errmsg')}")
    return {"success": True, "channel": "wecom", "response": result}


def _send_slack(webhook: str, message: str, timeout: int) -> dict:
    """Slack Incoming Webhook。"""
    payload = {"text": message}
    result = _http_post(webhook, payload, timeout=timeout)
    # Slack webhook 成功返回纯文本 "ok"
    if isinstance(result, dict) and result.get("raw") == "ok":
        return {"success": True, "channel": "slack"}
    if isinstance(result, dict) and result.get("error"):
        raise RuntimeError(f"Slack error: {result['error']}")
    return {"success": True, "channel": "slack", "response": result}


def _send_telegram(token: str, chat_id: str, message: str, timeout: int) -> dict:
    """Telegram Bot API（sendMessage）。"""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}
    result = _http_post(url, payload, timeout=timeout)
    if not result.get("ok"):
        raise RuntimeError(f"Telegram error: {result.get('description') or result}")
    return {"success": True, "channel": "telegram", "message_id": result.get("result", {}).get("message_id")}


def _send_discord(webhook: str, message: str, timeout: int) -> dict:
    """Discord Webhook。消息超 2000 字符自动截断（Discord 限制）。"""
    if len(message) > 2000:
        message = message[:1990] + "\n…（已截断）"
    payload = {"content": message}
    result = _http_post(webhook, payload, timeout=timeout)
    # Discord 成功返回 HTTP 204，urllib 读不到 body，raw 为空
    return {"success": True, "channel": "discord", "response": result}


def _send_teams(webhook: str, message: str, timeout: int) -> dict:
    """Microsoft Teams Incoming Webhook（legacy connector card）。"""
    payload = {"text": message}
    result = _http_post(webhook, payload, timeout=timeout)
    # Teams 成功返回纯文本 "1"
    if isinstance(result, dict) and result.get("raw") in ("1", ""):
        return {"success": True, "channel": "teams"}
    return {"success": True, "channel": "teams", "response": result}


def _send_generic(webhook: str, message: str | None, payload_json: str | None, timeout: int) -> dict:
    """通用 HTTP webhook：POST JSON。

    payload_json 优先；没有则用 {"text": message}。
    """
    if payload_json:
        try:
            payload = json.loads(payload_json)
        except json.JSONDecodeError as exc:
            raise ValueError(f"--payload-json 不是合法 JSON: {exc}") from exc
    elif message:
        payload = {"text": message}
    else:
        raise ValueError("generic 渠道需提供 --payload-json 或 -m")
    result = _http_post(webhook, payload, timeout=timeout)
    return {"success": True, "channel": "generic", "response": result}


# ---------------------------------------------------------------------------
# 消息内容读取
# ---------------------------------------------------------------------------

def _read_message(args: argparse.Namespace) -> str | None:
    if getattr(args, "message", None):
        return args.message
    if getattr(args, "message_file", None):
        path = Path(args.message_file)
        if not path.exists():
            raise FileNotFoundError(f"message file not found: {path}")
        return path.read_text(encoding="utf-8").rstrip("\n")
    if getattr(args, "stdin", False):
        return sys.stdin.read().rstrip("\n")
    return None


# ---------------------------------------------------------------------------
# 主处理
# ---------------------------------------------------------------------------

def h_send(args: argparse.Namespace) -> dict:
    channel = args.channel.lower()
    timeout = int(getattr(args, "timeout", 30))
    message = _read_message(args)

    if channel == "dingtalk":
        webhook = _env("NOTIFY_DINGTALK_WEBHOOK", getattr(args, "webhook", None))
        secret = _env("NOTIFY_DINGTALK_SECRET", getattr(args, "secret", None))
        if not webhook:
            raise ValueError("钉钉需要 --webhook 或环境变量 NOTIFY_DINGTALK_WEBHOOK")
        if not message:
            raise ValueError("需要提供 -m / --message-file / --stdin")
        return _send_dingtalk(webhook, secret, message, timeout)

    elif channel == "feishu":
        webhook = _env("NOTIFY_FEISHU_WEBHOOK", getattr(args, "webhook", None))
        if not webhook:
            raise ValueError("飞书需要 --webhook 或环境变量 NOTIFY_FEISHU_WEBHOOK")
        if not message:
            raise ValueError("需要提供 -m / --message-file / --stdin")
        return _send_feishu(webhook, message, timeout)

    elif channel == "wecom":
        webhook = _env("NOTIFY_WECOM_WEBHOOK", getattr(args, "webhook", None))
        if not webhook:
            raise ValueError("企业微信需要 --webhook 或环境变量 NOTIFY_WECOM_WEBHOOK")
        if not message:
            raise ValueError("需要提供 -m / --message-file / --stdin")
        return _send_wecom(webhook, message, timeout)

    elif channel == "slack":
        webhook = _env("NOTIFY_SLACK_WEBHOOK", getattr(args, "webhook", None))
        if not webhook:
            raise ValueError("Slack 需要 --webhook 或环境变量 NOTIFY_SLACK_WEBHOOK")
        if not message:
            raise ValueError("需要提供 -m / --message-file / --stdin")
        return _send_slack(webhook, message, timeout)

    elif channel == "telegram":
        token = _env("NOTIFY_TELEGRAM_TOKEN", getattr(args, "token", None))
        chat_id = _env("NOTIFY_TELEGRAM_CHAT_ID", getattr(args, "chat_id", None))
        if not token or not chat_id:
            raise ValueError("Telegram 需要 --token / --chat-id 或对应环境变量")
        if not message:
            raise ValueError("需要提供 -m / --message-file / --stdin")
        return _send_telegram(token, chat_id, message, timeout)

    elif channel == "discord":
        webhook = _env("NOTIFY_DISCORD_WEBHOOK", getattr(args, "webhook", None))
        if not webhook:
            raise ValueError("Discord 需要 --webhook 或环境变量 NOTIFY_DISCORD_WEBHOOK")
        if not message:
            raise ValueError("需要提供 -m / --message-file / --stdin")
        return _send_discord(webhook, message, timeout)

    elif channel == "teams":
        webhook = _env("NOTIFY_TEAMS_WEBHOOK", getattr(args, "webhook", None))
        if not webhook:
            raise ValueError("Teams 需要 --webhook 或环境变量 NOTIFY_TEAMS_WEBHOOK")
        if not message:
            raise ValueError("需要提供 -m / --message-file / --stdin")
        return _send_teams(webhook, message, timeout)

    elif channel == "generic":
        webhook = _env("NOTIFY_GENERIC_WEBHOOK", getattr(args, "webhook", None))
        if not webhook:
            raise ValueError("generic 需要 --webhook 或环境变量 NOTIFY_GENERIC_WEBHOOK")
        payload_json = getattr(args, "payload_json", None)
        return _send_generic(webhook, message, payload_json, timeout)

    else:
        raise ValueError(
            f"未知渠道 '{args.channel}'。支持：dingtalk / feishu / wecom / slack / telegram / discord / teams / generic"
        )


SUPPORTED_CHANNELS = ["dingtalk", "feishu", "wecom", "slack", "telegram", "discord", "teams", "generic"]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="hot-search-skill 通用推送 CLI（支持钉钉/飞书/企微/Slack/Telegram/Discord/Teams）"
    )
    sub = p.add_subparsers(dest="action", required=True)

    sp = sub.add_parser("send", help="推送一条消息")
    sp.add_argument(
        "--channel", required=True, choices=SUPPORTED_CHANNELS,
        help="推送渠道"
    )
    # 消息内容三选一
    content_group = sp.add_mutually_exclusive_group()
    content_group.add_argument("-m", "--message", default=None, help="消息文本")
    content_group.add_argument("--message-file", dest="message_file", default=None, help="从文件读取消息")
    content_group.add_argument("--stdin", action="store_true", help="从标准输入读取消息")
    # webhook 类渠道
    sp.add_argument("--webhook", default=None, help="Webhook URL（也可用环境变量）")
    # 钉钉签名
    sp.add_argument("--secret", default=None, help="钉钉加签 secret（也可用 NOTIFY_DINGTALK_SECRET）")
    # Telegram 专属
    sp.add_argument("--token", default=None, help="Telegram Bot token（也可用 NOTIFY_TELEGRAM_TOKEN）")
    sp.add_argument("--chat-id", dest="chat_id", default=None, help="Telegram chat_id（也可用 NOTIFY_TELEGRAM_CHAT_ID）")
    # generic 专属
    sp.add_argument("--payload-json", dest="payload_json", default=None, help="generic 渠道：自定义 JSON payload")
    sp.add_argument("--timeout", type=int, default=30, help="HTTP 超时秒数（默认 30）")
    sp.set_defaults(_handler=h_send)

    return p


def main() -> None:
    args = build_parser().parse_args()
    try:
        result = args._handler(args)
    except Exception as exc:
        print(
            json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False, indent=2),
            file=sys.stderr,
        )
        sys.exit(1)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
