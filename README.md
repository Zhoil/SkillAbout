# Hot Search Skill

Hot Search Skill 是一个轻量、可审计的热点监测工具，用于抓取 GitHub 项目动量、Star History Weekly 榜单以及近期 AI 社区讨论，并输出 Markdown、JSON、HTML 或适合消息推送的文本报告。

它适合以下场景：

- 每日跟踪 Star History Weekly 榜单及排名变化。
- 监测指定 GitHub 仓库的 Star 快照和时间窗口增量。
- 汇总 Reddit、Bluesky、Hacker News、GitHub 等公开来源的 AI 热点。
- 生成可在浏览器查看的 HTML 热点面板。
- 按自定义渠道（企业微信机器人、Slack、飞书、钉钉、Bark、任意 shell 命令等）定时推送报告。

## 主要能力

### Star History Weekly 榜单

工具每天读取 Star History 首页公开的 Weekly 榜单，保留：

- 当前名次与升降变化。
- 过去一周 Star 增量。
- GitHub 仓库链接。
- Star History 项目链接。
- 每个榜单仓库的中文一句话介绍；可通过中文映射文件持续维护，新仓库自动使用中文事实性降级说明。

页面结构变化或网络失败会明确报告，不会从页面其他区域猜测或拼接榜单。

### GitHub Star 监控

通过本地快照计算真实时间窗口内的 Star 变化：

```bash
python3 hotsearching/last30days/scripts/last30days.py snapshot --repo owner/name
```

### AI 社区热点

调用 `hotsearching/last30days` Skill，汇总来自 Reddit、Bluesky、Hacker News 和 GitHub 的 AI 热点，输出 Markdown、JSON 或 HTML。

## 环境要求

- Python 3.11 或更高版本。
- 公开热点和 Star 监控仅使用 Python 标准库。
- 消息推送为可选能力，标准库即可覆盖大多数 webhook 适配器；`shell` 适配器支持调用任意外部命令。

## AI 热点摘要与推送

`hotsearching/ai-hotspot-digest` 将 last30days 热点与 Star History 榜单合并，生成固定格式的摘要文本，再通过可配置的推送渠道发送。

### 快速开始

```bash
# 1. 复制配置模板
cp hotsearching/ai-hotspot-digest/references/config.example.json \
   ~/.ai-hotspot-digest/config.json

# 2. 编辑配置，选择推送适配器
# 支持：shell / wecom_bot / slack_webhook / bark / feishu_bot / dingtalk_bot

# 3. 生成预览（不发送）
python3 hotsearching/ai-hotspot-digest/scripts/build_digest.py \
  --config ~/.ai-hotspot-digest/config.json \
  --last30days-file /tmp/ai-hotspots.json \
  --annotations-file /tmp/annotations.json \
  --output /tmp/digest-preview.txt

# 4. 确认预览内容后发送
python3 hotsearching/ai-hotspot-digest/scripts/push_digest.py \
  --config ~/.ai-hotspot-digest/config.json \
  --message-file /tmp/digest-preview.txt
```

生成预览后会通过仅监听 `127.0.0.1` 的本地服务，在默认浏览器中打开 HTML 动态看板。定时预览模式下，点击“实时刷新”会触发一次新的热点采集与摘要生成，并整批替换已展示热点；页面每 30 秒静默检查 `latest.html`，数据变化时只更新看板内容。刷新接口使用随机令牌且仅监听回环地址。自动化场景可增加 `--no-open-dashboard`。

### 推送适配器配置

在 `config.json` 的 `push` 字段中指定适配器和目标：

**企业微信群机器人**

```json
"push": {
  "adapter": "wecom_bot",
  "target": {
    "webhook_url": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=YOUR_KEY",
    "msg_type": "text"
  }
}
```

**Slack Incoming Webhook**

```json
"push": {
  "adapter": "slack_webhook",
  "target": {
    "webhook_url": "https://hooks.slack.com/services/YOUR/WEBHOOK/URL"
  }
}
```

**飞书群机器人**

```json
"push": {
  "adapter": "feishu_bot",
  "target": {
    "webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/YOUR_KEY"
  }
}
```

**钉钉群机器人**

```json
"push": {
  "adapter": "dingtalk_bot",
  "target": {
    "webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=YOUR_TOKEN"
  }
}
```

**Bark（iOS 推送）**

```json
"push": {
  "adapter": "bark",
  "target": {
    "url": "https://api.day.app/YOUR_KEY/",
    "title": "AI 热点日报"
  }
}
```

**任意 shell 命令**

```json
"push": {
  "adapter": "shell",
  "target": {
    "command": "/usr/local/bin/my-notify",
    "args": ["--channel", "ai-digest"]
  }
}
```

消息内容通过 stdin 传入命令。

### 自定义 last30days 搜索关键词和方向

在 `config.json` 的 `last30days` 字段中配置采集参数：

```json
"last30days": {
  "topic": "AI frontier research developer tools agent skills engineering practices",
  "domestic_topic": "人工智能 前沿技术 AI研发 Agent Skill 大模型工程",
  "days": 14,
  "depth": "deep",
  "search": "reddit,x,youtube,tiktok,instagram,hackernews,polymarket,github,arxiv,grounding",
  "subreddits": "LocalLLaMA,MachineLearning",
  "dedicated_subreddits": "ollama",
  "x_handle": "ollama",
  "domestic": {
    "enabled": true,
    "platforms": "weixin,toutiao,juejin,csdn,zhihu",
    "per_platform": 6
  },
  "quality": {
    "min_score": 0.48,
    "per_source_limit": 10,
    "max_results": 80
  }
}
```

| 字段 | 说明 | 默认值 |
|---|---|---|
| `topic` | 搜索关键词/主题 | `AI frontier research developer tools agent skills engineering practices` |
| `days` | 回溯天数 | `30` |
| `depth` | `default` / `quick` / `deep` | `default` |
| `search` | 逗号分隔来源（留空用引擎默认） | 空 |
| `subreddits` | 额外 subreddit（不含 `r/`） | 空 |
| `dedicated_subreddits` | 实体专属 subreddit，全量拉取 | 空 |
| `x_handle` | X/Twitter 账号定向搜索 | 空 |
| `domestic` | 免登录国内平台：微信公众号、今日头条、掘金、CSDN、知乎专栏 | 开启 |
| `quality` | 质量阈值、关键词/屏蔽词、单来源上限 | 开启 |

采集器会为所有来源统一生成四类检索角度：前沿研究与新技术、AI 工程实践、Agent Skill/工作流总结、模型与开源发展。合并结果后按内容完整度、技术关键词、上游相关性和公开可访问性评分，过滤广告与标题党，规范化 URL、标题去重，并限制单一来源占比。微信公众号使用搜狗微信公开文章索引；其他国内来源只保留无需登录即可打开的文章或博客链接。国内文章若尚未配置人工注释，会使用检索结果中的中文原文摘要首句作为说明；没有可靠中文摘要的条目不会进入推送。

手动单次采集：

```bash
python3 hotsearching/ai-hotspot-digest/scripts/fetch_hotspots.py \
  --config ~/.ai-hotspot-digest/config.json \
  --skill-dir hotsearching/last30days \
  --output /tmp/ai-hotspots.json
```

### 热点展示冷却期

近 30 天热点默认按来源 URL 启用 7 天冷却：每次生成或实时刷新都会排除过去 7 天已经展示的 URL（包括当天上一批内容），优先补入其他候选。状态默认保存在预览目录的 `.hotspot-cooldown.json`；可在配置中调整：

```json
"cooldown": {
  "days": 7,
  "state_file": ""
}
```

候选不足时会减少实际展示条数，不会提前复用冷却中的热点。

### 每日定时生成预览

`schedule.json` 新增 `fetch_last30days` 选项，设为 `true` 后每次定时运行前会自动刷新热点文件，无需维护独立采集任务：

```json
{
  "enabled": true,
  "time": "10:00:00",
  "timezone": "GMT+8",
  "digest_config": "~/.ai-hotspot-digest/config.json",
  "last30days_file": "/tmp/ai-hotspots-last30days.json",
  "annotations_file": "/tmp/ai-hotspot-annotations.json",
  "output_dir": "/tmp/ai-hotspot-daily-previews",
  "fetch_last30days": true,
  "last30days_skill_dir": "/path/to/hotsearching/last30days"
}
```

```bash
# 验证一次
python3 hotsearching/ai-hotspot-digest/scripts/scheduled_preview.py \
  --schedule ~/.ai-hotspot-digest/schedule.json --run-once

# 启动持续进程（只生成预览，不发送）
python3 -u hotsearching/ai-hotspot-digest/scripts/scheduled_preview.py \
  --schedule ~/.ai-hotspot-digest/schedule.json
```

预览文件保存在配置的 `output_dir`，每次原子更新 `latest.txt` 和 `latest.html`。`--run-once` 会打开 `latest.html` 动态看板；持续进程不会自动弹出浏览器。看板的手动刷新会复用同一 schedule 配置重新采集（`fetch_last30days=true` 时）并生成新批次，旧批次不会混入新结果。

## 测试

```bash
cd hotsearching/ai-hotspot-digest/scripts
python3 -m unittest discover -p 'test_*.py'
```

## 故障排查

### Star History 无数据

- 检查是否能访问 `https://www.star-history.com/`。
- 查看错误是否为 DNS、超时或页面结构变化。
- 不要用页面其他仓库列表替代 Weekly 榜单。

### GitHub 限流

设置 GitHub Token 提高 API 配额：

```bash
export GITHUB_TOKEN="your-token"
```

也支持 `GH_TOKEN`。不要把 Token 写入示例或提交到 Git。

### 推送状态未知

先到目标渠道确认消息是否存在。只有确认没有发送成功后才能再次执行，禁止无条件自动重试。

## 项目结构

```text
hotsearching/
├── last30days/              # AI 热点采集 Skill
├── ai-hotspot-digest/       # 摘要生成与可配置推送 Skill
│   ├── SKILL.md
│   ├── scripts/
│   │   ├── build_digest.py      # 生成摘要预览
│   │   ├── push_digest.py       # 通用推送适配器
│   │   ├── scheduled_preview.py # 定时生成预览
│   │   ├── test_build_digest.py
│   │   ├── test_scheduled_preview.py
│   │   └── test_push_digest.py
│   └── references/
│       ├── config.example.json      # 推送配置模板
│       ├── annotations.example.json # 中文描述格式示例
│       └── schedule.example.json    # 定时配置模板
```

## 设计原则

- 确定性采集优先，不凭空补齐失败来源。
- 事实、社区观点和项目指标分开表达。
- 原始链接和原生互动指标保持可追溯。
- 推送渠道完全可配置，不绑定任何特定 IM 软件。
- 自动推送只读取已落盘且通过校验的配置。
- 外部写操作失败或状态未知时不自动重试。
