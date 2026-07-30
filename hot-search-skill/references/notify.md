# Notify — 通用消息推送参考

本技能通过 `runtime/notify.py` 向任意支持 webhook / bot API 的桌面聊天软件推送报告。  
支持渠道：**钉钉 / 飞书 / 企业微信 / Slack / Telegram / Discord / Microsoft Teams / 通用 webhook**。

---

## 快速上手

### 1. 配置凭证（推荐环境变量）

复制 `.env.example` 为 `.env`，填入目标渠道的 webhook URL 或 token：

```env
NOTIFY_FEISHU_WEBHOOK=https://open.feishu.cn/open-apis/bot/v2/hook/YOUR_TOKEN
NOTIFY_DINGTALK_WEBHOOK=https://oapi.dingtalk.com/robot/send?access_token=YOUR_TOKEN
NOTIFY_DINGTALK_SECRET=your-signing-secret          # 若钉钉机器人开启加签
NOTIFY_WECOM_WEBHOOK=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=YOUR_KEY
NOTIFY_SLACK_WEBHOOK=https://hooks.slack.com/services/T00/B00/YOUR_TOKEN
NOTIFY_TELEGRAM_TOKEN=YOUR_BOT_TOKEN
NOTIFY_TELEGRAM_CHAT_ID=-100YOUR_CHAT_ID
NOTIFY_DISCORD_WEBHOOK=https://discord.com/api/webhooks/YOUR_ID/YOUR_TOKEN
NOTIFY_TEAMS_WEBHOOK=https://YOUR_TENANT.webhook.office.com/webhookb2/YOUR_PATH
NOTIFY_GENERIC_WEBHOOK=https://your-app.example.com/hooks/inbound
```

### 2. 发送一条消息

```bash
# 飞书
python3 runtime/notify.py send --channel feishu -m "消息内容"

# 钉钉（无加签）
python3 runtime/notify.py send --channel dingtalk -m "消息内容"

# 钉钉（加签，secret 也可通过 NOTIFY_DINGTALK_SECRET 传入）
python3 runtime/notify.py send --channel dingtalk --secret <secret> -m "消息内容"

# 企业微信
python3 runtime/notify.py send --channel wecom -m "消息内容"

# Slack
python3 runtime/notify.py send --channel slack -m "消息内容"

# Telegram
python3 runtime/notify.py send --channel telegram -m "消息内容"

# Discord
python3 runtime/notify.py send --channel discord -m "消息内容"

# Teams
python3 runtime/notify.py send --channel teams -m "消息内容"

# 通用 webhook（自定义 JSON body）
python3 runtime/notify.py send --channel generic --payload-json '{"text":"消息内容"}'
```

消息内容三选一：`-m "..."` / `--message-file <path>` / `--stdin`。

命令行 `--webhook` 参数可覆盖对应环境变量，适合临时测试。

---

## 发送热点报告

先生成报告文本，再推送：

```bash
python3 scripts/hot_search.py message --limit 6 > /tmp/report.txt
python3 runtime/notify.py send --channel feishu --message-file /tmp/report.txt
```

或使用 `poll-push` 一步完成（需 `--dry-run` 预览，再加 `--confirm-send` 正式发送）：

```bash
# 预览（不发送）
python3 scripts/hot_search.py poll-push --channel feishu --once --dry-run

# 正式发送（用户确认后）
python3 scripts/hot_search.py poll-push --channel feishu --once --confirm-send
```

---

## 每日自动推送

复制 `references/push-config.example.json` 到 `~/.hot-search-skill/push-config.json`，  
填写 `channel` 和对应渠道配置，将 `enabled` 改为 `true`，然后：

```bash
# 预览
python3 scripts/hot_search.py daily-push --dry-run

# 安装 macOS 定时任务（每天 10:10）
python3 scripts/hot_search.py schedule install
python3 scripts/hot_search.py schedule status
```

---

## 写操作安全约定

- 任何真实发送都必须先展示完整消息正文再等待确认（`--dry-run` 预览）。
- 写操作超时后不自动重试，需用户到目标软件核实是否已收到。
- 不要在命令行或日志中输出 webhook URL、token、secret 等凭证。

---

## 错误处理

`notify.py` 所有错误以 JSON 格式输出到 stderr：

```json
{"success": false, "error": "HTTP 400: ..."}
```

| 现象 | 排查方向 |
|---|---|
| HTTP 4xx | webhook URL 或 token 有误，检查 `.env` |
| network error | 检查网络、代理设置 |
| timed out | 目标服务无响应，可适当增大 `--timeout` |
| 钉钉 `errcode 310000` | 消息内容触发关键词限制 |
| Telegram `Unauthorized` | Bot token 无效或未启用 |
| Discord 204 无报错 | 正常，Discord 成功不返回 body |
