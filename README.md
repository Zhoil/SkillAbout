# Hot Search Skill

Hot Search Skill 是一个轻量、可审计的热点监测工具，用于抓取 GitHub 项目动量、Star History Weekly 榜单以及近期 AI 社区讨论，并输出 Markdown、JSON、HTML 或适合消息推送的文本报告。

它适合以下场景：

- 每日跟踪 Star History Weekly 榜单及排名变化。
- 监测指定 GitHub 仓库的 Star 快照和时间窗口增量。
- 汇总 Reddit、Bluesky、Hacker News、GitHub 等公开来源的 AI 热点。
- 生成可在浏览器查看的 HTML 热点面板。
- 在 macOS 上每天定时抓取并推送报告。

## 主要能力

### Star History Weekly 榜单

工具每天读取 Star History 首页公开的 Weekly 榜单，保留：

- 当前名次与升降变化。
- 过去一周 Star 增量。
- GitHub 仓库链接。
- Star History 项目链接。

页面结构变化或网络失败会明确报告，不会从页面其他区域猜测或拼接榜单。

### GitHub Star 监控

通过本地快照计算真实时间窗口内的 Star 变化：

```bash
python3 hot-search-skill/scripts/hot_search.py snapshot --repo owner/name
python3 hot-search-skill/scripts/hot_search.py hourly --repo owner/name --hours 1 --refresh
python3 hot-search-skill/scripts/hot_search.py watch --repo owner/name --interval 300 --count 12
```

默认状态保存在 `~/.hot-search-skill/state.json`，可通过 `HOT_SEARCH_STATE_DIR` 或 `--state` 修改。

### AI 社区热点

```bash
python3 hot-search-skill/scripts/social_hotspots.py \
  "artificial intelligence AI agents" --days 7 --limit 10

python3 hot-search-skill/scripts/social_hotspots.py \
  "AI coding agents" --format json

python3 hot-search-skill/scripts/social_hotspots.py \
  "AI video" --format html --output /tmp/ai-pulse.html
```

当前公开来源包括 Reddit、Bluesky、Hacker News 和 GitHub。排序综合原生互动量、时效性、来源质量及跨源印证，并限制单一作者或来源占比。

来源状态区分 `ok`、`no-results`、`rate-limited`、`unreachable`、`schema-drift` 和 `error`。只有 `no-results` 表示请求成功但没有匹配内容，其他失败状态都代表覆盖不完整。

## 环境要求

- Python 3.11 或更高版本。
- 公开热点和 Star 监控仅使用 Python 标准库。
- 京 ME 推送为可选能力，需要额外依赖和可用的登录环境。

安装可选推送依赖：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r hot-search-skill/requirements.txt
```

如需要浏览器登录，可能还需要 Playwright：

```bash
.venv/bin/python -m pip install playwright
.venv/bin/python -m playwright install chromium
```

## 生成报告

组合报告与消息预览：

```bash
python3 hot-search-skill/scripts/hot_search.py report --limit 10
python3 hot-search-skill/scripts/hot_search.py message --limit 6 --push-payload
```

消息使用纯文本和完整链接，避免依赖未注册的客户端卡片模板。超长内容按完整行截断，不会截断 URL。

## 自定义推送配置

示例配置位于 [`hot-search-skill/references/push-config.example.json`](hot-search-skill/references/push-config.example.json)。复制到用户目录：

```bash
mkdir -p ~/.hot-search-skill
cp hot-search-skill/references/push-config.example.json \
  ~/.hot-search-skill/push-config.json
```

编辑私有配置：

```json
{
  "enabled": true,
  "target": {
    "type": "group",
    "id": "your-group-id"
  },
  "schedule": {
    "hour": 10,
    "minute": 10
  },
  "limit": 6,
  "include_ai_hotspots": true,
  "send_timeout": 90
}
```

`target.type` 支持 `group` 和 `user`。如需指定虚拟环境解释器，可增加：

```json
"python": "/absolute/path/to/.venv/bin/python"
```

不要把包含真实群号、用户 ID、Cookie 或凭据的私有配置提交到仓库。

## 测试推送

先进行只读预览：

```bash
python3 hot-search-skill/scripts/hot_search.py daily-push --dry-run
```

预览会展示目标、完整正文和发送命令，但不会产生消息。确认目标和正文后，可执行一次配置驱动的发送：

```bash
python3 hot-search-skill/scripts/hot_search.py daily-push
```

如果配置位于其他位置：

```bash
python3 hot-search-skill/scripts/hot_search.py daily-push \
  --config /path/to/private-push-config.json --dry-run
```

写操作超时或返回状态未知时不要立即重试，应先在目标客户端确认是否已经收到，避免重复消息。

## 每日 10:10 自动运行

macOS 使用 `launchd` 安装每日任务。默认按系统本地时区读取配置中的 `schedule.hour` 和 `schedule.minute`：

```bash
python3 hot-search-skill/scripts/hot_search.py schedule install
python3 hot-search-skill/scripts/hot_search.py schedule status
```

默认配置即每天 `10:10` 执行。日志保存在：

```text
~/.hot-search-skill/daily-push.log
~/.hot-search-skill/daily-push.error.log
```

卸载任务：

```bash
python3 hot-search-skill/scripts/hot_search.py schedule uninstall
```

当前自动调度仅支持 macOS。Linux 用户可以通过 cron 调用同一个 `daily-push` 命令，例如：

```cron
10 10 * * * cd /path/to/hot-search-skill && /usr/bin/python3 hot-search-skill/scripts/hot_search.py daily-push
```

## 京 ME 推送说明

京 ME 是可选的内部适配能力，不影响公开来源采集和本地报告生成。使用前应确认：

1. `runtime/dispatch.py whoami` 能正确返回发送者。
2. 目标用户或群 ID 唯一且经过核对。
3. 完整正文和所有提及已经预览。
4. 用户明确授权真实发送。

常用只读检查：

```bash
.venv/bin/python hot-search-skill/runtime/dispatch.py whoami
.venv/bin/python hot-search-skill/runtime/dispatch.py search-group --q "group name"
```

不要输出或提交 `me_token`、`appSecret`、`accessToken` 等凭据。

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

### 定时任务没有执行

```bash
python3 hot-search-skill/scripts/hot_search.py schedule status
tail -n 100 ~/.hot-search-skill/daily-push.error.log
```

确认配置已设置 `enabled: true`，并尽量在配置中使用 Python 解释器的绝对路径，因为 `launchd` 的环境变量和交互式终端不同。

### 推送状态未知

先到目标客户端确认消息是否存在。只有确认没有发送成功后才能再次执行，禁止无条件自动重试。

## 项目结构

```text
hot-search-skill/
├── SKILL.md                 # Agent 使用说明
├── scripts/
│   ├── hot_search.py        # Star、报告、推送与调度入口
│   ├── social_hotspots.py   # 社区热点 CLI
│   └── lib/                 # 来源、排序、解析与渲染
├── runtime/                 # 可选消息推送运行时
├── references/
│   ├── LAST30DAYS.md        # 近 30 天研究约束
│   ├── source-strategy.md   # 来源和覆盖语义
│   └── jingme-notify.md     # 京 ME 安全发送规范
└── requirements.txt
```

## 设计原则

- 确定性采集优先，不凭空补齐失败来源。
- 事实、社区观点和项目指标分开表达。
- 原始链接和原生互动指标保持可追溯。
- 自动推送只读取已落盘且通过校验的配置。
- 外部写操作失败或状态未知时不自动重试。

更多 Agent 工作流和输出格式见 [`hot-search-skill/SKILL.md`](hot-search-skill/SKILL.md)。
