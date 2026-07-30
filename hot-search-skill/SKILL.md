---
name: hot-search-skill
description: 监测 GitHub 仓库 Star 变化，并研究最近数天全球社媒中的 AI 热点、社区讨论和项目动量。覆盖 Reddit、Bluesky、Hacker News、GitHub 与 Star History，支持来源健康状态、跨源排序、Markdown/JSON/HTML 可视化报告，以及通过钉钉/飞书/企业微信/Slack/Telegram/Discord/Teams 推送。用户要求"GitHub star 监测""AI 社媒热点""全球 AI 热点""热门 AI 项目""热点日报/周报"或"发送热点报告到群"时使用。
---

# Hot Search Skill

将任务分成两个独立信号面：

- **Star monitoring**：保存指定 GitHub 仓库快照，计算真实时间窗口内的增量。
- **Social pulse**：研究全球公开社媒中的近期 AI 讨论，统一标准化、去重、评分并报告来源覆盖。

默认只读。任何推送都必须先展示完整消息正文，再等待明确确认。

## Workflow

1. 判断用户要 Star 监控、社媒热点，还是组合报告。
2. 运行确定性脚本采集，不凭空补齐失败来源。
3. 检查 `source_status`；区分 `no-results` 与网络失败、限流、协议变化。
4. 综合热度、时效性、跨源印证和项目动量，输出可点击来源。
5. 若需推送，先展示渠道、完整正文，等待确认后执行。

## Commands

GitHub Star 快照及小时变化：

```bash
python3 scripts/hot_search.py snapshot --repo owner/name
python3 scripts/hot_search.py hourly --repo owner/name --hours 1 --refresh
python3 scripts/hot_search.py watch --repo owner/name --interval 300 --count 12
```

全球 AI 社媒研究：

```bash
python3 scripts/social_hotspots.py "artificial intelligence AI agents" --days 7 --limit 10
python3 scripts/social_hotspots.py "AI coding agents" --format json
python3 scripts/social_hotspots.py "AI video" --format html --output /tmp/ai-pulse.html
```

兼容旧入口：

```bash
python3 scripts/hot_search.py ai --days 7 --limit 10
python3 scripts/hot_search.py report --repo owner/name --limit 10
python3 scripts/hot_search.py message --limit 6 --push-payload
```

## Social Research Contract

社媒报告参考 `/last30days` 的组织原则，但保持本技能轻量、自包含：

- 每个来源适配器输出统一 `SocialItem`。
- 管线并发采集，按标题相似度聚类并去重。
- 排名综合原生互动量、发布时间、来源质量和跨源印证。
- 对单一作者和单一来源限制占比，避免榜单被一个渠道垄断。
- `source_status` 是覆盖结论的唯一依据；失败来源不能描述为"没有讨论"。
- 正文必须包含原始链接和原生互动指标，不把 GitHub Star 当作社媒观点。

当前公开来源：Reddit、Bluesky、Hacker News、GitHub。平台能力和扩展策略见 [references/source-strategy.md](references/source-strategy.md)，集成的近 30 天研究约束见 [references/LAST30DAYS.md](references/LAST30DAYS.md)。

## Visual Output

`social_hotspots.py --format html` 生成响应式可视化热点面板，适合浏览器查看或后续发布。HTML 是本地文件，不自动上传公开网络。

## 消息推送

支持钉钉、飞书、企业微信、Slack、Telegram、Discord、Microsoft Teams 以及任意 HTTP webhook。  
凭证通过 `.env` 或环境变量配置，详见 [references/notify.md](references/notify.md)。

生成报告文本：

```bash
python3 scripts/hot_search.py message --limit 6
```

一步预览并推送（以飞书为例）：

```bash
# 预览（不发送）
python3 scripts/hot_search.py poll-push --channel feishu --once --dry-run

# 用户确认后正式发送
python3 scripts/hot_search.py poll-push --channel feishu --once --confirm-send
```

## Notification Checkpoint

发送前必须展示并等待确认：

- 目标渠道（`--channel`）。
- 完整消息正文。
- 若消息含 @提及，列出所有被@对象。

写操作超时后不得自动重试，需用户在目标软件中核实是否已收到。

## 每日自动推送

复制 [references/push-config.example.json](references/push-config.example.json) 到 `~/.hot-search-skill/push-config.json`，  
填写 `channel` 和对应渠道的 webhook / token，将 `enabled` 改为 `true` 后：

```bash
python3 scripts/hot_search.py daily-push --dry-run
python3 scripts/hot_search.py schedule install
python3 scripts/hot_search.py schedule status
```

默认按系统本地时区每天 `10:10` 抓取热点并推送。`enabled=true` 和配置中的明确渠道构成无人值守发送授权；修改 `schedule.hour`、`schedule.minute`、`limit`、`include_ai_hotspots`、`channel` 即可自定义，不再依赖对话确认。

## State And Failure Handling

- Star 快照默认保存到 `~/.hot-search-skill/state.json`；可用 `HOT_SEARCH_STATE_DIR` 或 `--state` 覆盖。
- GitHub 403/限流：配置 `GITHUB_TOKEN` 或 `GH_TOKEN`。
- `insufficient_history`：历史不足，不估算小时增量。
- 来源 `unreachable/rate-limited/schema-drift/error`：保留成功来源并明确标注部分覆盖。
- 推送写结果未知：要求用户在目标软件核验，不自动重发。

## 格式规范

  🟦【Hot Search | GitHub Star & AI】
  🕒 更新时间：2026-07-30 05:02:13 UTC

  🟩 GitHub Star 变化 TOP 6 - 过去一周的变化

  1.  –  mattpocock/skills  +51
     No rank change
     https://github.com/mattpocock/skills
  2.  ▲  diegosouzapw/OmniRoute  +42
     Up 1
     https://github.com/diegosouzapw/OmniRoute

  🟥 全球社媒 AI 热点 TOP 3

  1. OpenAI, Google, and Anthropic Absent from Nvidia-Led Open Secure AI Alliance
     热度：48.53 · points 3
     https://example.com/article

  📌 注：过去一周变化需持续快照；Star History 增量来自公开榜单。
