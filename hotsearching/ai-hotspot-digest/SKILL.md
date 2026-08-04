---
name: ai-hotspot-digest
description: 汇总 last30days 近 30 天研发工具与技能热点及中文描述，以及 Star History Weekly 排名趋势、Star 增量和 All-time 总 Star，按固定格式生成消息预览；用户明确确认后通过可自定义的推送渠道发送。用户要求研发工具热点日报、Star History 榜单汇总或推送到消息渠道时使用。
---

# AI Hotspot Digest

按以下顺序执行，不复制或修改依赖 Skill 的内部逻辑。

## 1. 收集近 30 天热点

**搜索参数优先从工作配置的 `last30days` 段读取**，以下为各字段说明：

| 字段 | 说明 | 默认值 |
|---|---|---|
| `topic` | 搜索关键词/主题字符串 | `R&D tools developer skills AI agent` |
| `days` | 回溯天数 | `30` |
| `depth` | 检索深度：`default` / `quick` / `deep` | `default` |
| `search` | 逗号分隔来源名称（留空使用 last30days 默认） | 空 |
| `subreddits` | 逗号分隔的 subreddit 名（不含 `r/`） | 空 |
| `dedicated_subreddits` | 实体专属 subreddit，全量拉取，不过相关性门槛 | 空 |
| `x_handle` | X/Twitter 账号针对性搜索 | 空 |

完整读取并调用同级 `../last30days/SKILL.md`，使用上述参数作为调用入参。优先请求其稳定 JSON agent 输出；若宿主只能得到 Markdown，将 Markdown 保存为文件也可。

如需在脚本/定时任务中自动采集，可直接调用 `scripts/fetch_hotspots.py`（见第 5 步）。

把产物路径传给汇总脚本的 `--last30days-file`。不得从 `last30days` 目录移动文件或修改其脚本。

读取最终选中的热点，为每条来源 URL 编写一句不超过 60 个汉字的中文描述，保存为 JSON 对象。键必须是原始来源 URL，值必须说明事件、核心变化或影响；格式参考 `references/annotations.example.json`。不得翻译或改写标题来冒充描述。

## 2. 生成汇总预览

复制 `references/config.example.json` 为一个工作配置，填写推送目标。三个数量互相独立：

- `limits.last30days`：近 30 天研发工具与技能热点条数。
- `limits.weekly`：Star History Weekly 条数（默认 20）。
- `limits.all_time`：Star History All-time 条数。

热点默认启用 7 天展示冷却期。脚本按来源 URL 记录展示日期：同一天重复生成保持内容稳定；从第二天起过滤冷却期内已展示的热点，优先让位给其他候选；满 7 天后恢复展示资格。状态默认原子保存到预览输出目录的 `.hotspot-cooldown.json`，也可通过 `cooldown.days` 和 `cooldown.state_file` 调整。候选不足时不得提前复用冷却内容，可以少于配置条数。

运行：

```bash
python3 scripts/build_digest.py \
  --config <配置文件> \
  --last30days-file <last30days产物> \
  --annotations-file <中文描述JSON> \
  --output <消息预览文件>
```

生成完成后会启动仅监听 `127.0.0.1` 的无缓存预览服务，并使用系统默认浏览器打开同名 HTML 动态看板。看板提供“实时刷新”按钮，并每 30 秒静默检查一次最新生成结果；发现变化时仅热更新数据区域，不整页刷新。自动化或无桌面环境可增加 `--no-open-dashboard`，仅生成文件而不启动服务或打开浏览器。

脚本从 `https://www.star-history.com/` 提取 Weekly 的趋势和 Star 变化，并从 All-time 提取 Star 总数。Weekly 固定写成 `序号. 趋势符号 仓库名 +变化量`，例如 `2. ▲ owner/repo +12`；All-time 固定写成 `序号. ：仓库名 ：总数 🌟`。每个仓库下一行使用 `💡 中文一句话介绍`，再下一行使用 `🔎 URL`。简介优先读取 `references/repository-descriptions.zh-CN.json`；新仓库可通过 `--repository-descriptions-file` 提供中文映射。未配置时只输出基于仓库归属的中文事实性说明，不直接展示英文 description，也不臆造功能。趋势使用 `–`（持平）、`▲`（上升）、`▼`（下降），不得输出"排名""趋势""Star 变化""数据源"等标签或尾行。若任一榜单无法识别，停止发送并报告错误，不得用臆测数据补齐。需要离线验证时可用 `--star-history-html <HTML文件>`。

**对比图表生成：** 当 `limits.weekly > 0` 且安装了 matplotlib 时，脚本会自动在消息预览文件同目录下生成一张 PNG 对比图（文件名与预览文件相同，后缀改为 `.png`），内容包含：
- 上半部分：前后两期排名对比折线图，蓝色圆点线表示上期排名，橙色方点线表示本期排名，Y 轴倒置（排名 1 在最上方），每个数据点标注趋势符号和 Star 变化量。
- 下半部分：数据附表，包含序号、仓库名、趋势（上升/下降/持平）、上期排名、本期排名、Star 变化量。

同时消息文本中也会嵌入纯文本格式的「Weekly 变化对比表」，方便不支持图片的渠道查看。

始终采用脚本内置的固定模板，不让模型自行排版。消息第一行固定为生成时的 GMT+8 时间：`🕒 YYYY-MM-DD HH:mm:ss GMT+8`。每类字段顺序固定：

- 热点：`序号. 标题 ：中文描述` → 下一行 `🌐 URL`。
- Weekly：趋势符号 + 仓库 + 变化量 → 下一行 `💡 一句话介绍` → 下一行 `🔎 URL`。
- Weekly 对比表：纯文本表格，含上期/本期排名和 Star 变化。
- All-time：仓库 → `总数 🌟` → 下一行 `💡 一句话介绍` → 下一行 `🔎 URL`。

## 3. 解析推送目标

配置文件中 `push.target` 字段描述推送渠道和目标标识符，格式由渠道自定义（见 `references/config.example.json`）。

如需在发送前解析目标，调用对应渠道的工具或 CLI 验证目标可达性，确保唯一匹配后记录实际 ID。

## 4. 确认后发送

先向用户展示：推送渠道名称、目标标识符、预览文件中的完整消息、@列表（本 Skill 默认空）。必须获得明确确认。

确认后调用 `push_digest.py` 执行发送：

```bash
python3 scripts/push_digest.py \
  --config <配置文件> \
  --message-file <消息预览文件>
```

`push_digest.py` 根据 `push.adapter` 字段加载对应适配器（见 `references/config.example.json` 中的适配器说明），将预览文件内容发送至目标。

超时或返回状态不明时不得重试。只有 CLI 明确成功才报告已发送。仅生成预览、定时发送或未确认时不得调用写操作。

本 Skill 不执行无人值守或定时真实发送。可以由外部计划任务定时生成预览，但每次调用发送前仍须完成身份、目标、完整内容确认。

## 5. 每日定时生成预览

复制 `references/schedule.example.json` 为工作配置，设置 GMT+8 的 `time`、三个输入路径和输出目录。

**自动采集热点（推荐）：** 将 `fetch_last30days` 设为 `true`，并填写 `last30days_skill_dir`（指向含 `scripts/last30days.py` 的目录）。每次定时运行时，进程会先调用 `fetch_hotspots.py` 按配置中的 `last30days` 参数刷新热点文件，再生成摘要预览，无需手动维护输入文件。

**手动维护热点文件：** 保持 `fetch_last30days` 为 `false`（默认），由外部流程定时更新 `last30days_file`，每次执行仍会重新抓取 Star History。

先验证一次：

```bash
python3 scripts/scheduled_preview.py --schedule <定时配置> --run-once
```

`--run-once` 生成完成后会自动打开 HTML 动态看板；持续定时进程只在后台生成文件，不会每天弹出浏览器窗口。

确认预览生成成功后启动持续进程：

```bash
python3 -u scripts/scheduled_preview.py --schedule <定时配置>
```

进程每天在配置时间生成 `digest-YYYYMMDD-HHMMSS.txt`，并原子更新用途明确的 `latest.txt`。该进程只生成本地预览，绝不执行发送。需要系统重启后自动恢复时，由用户选择受管进程或操作系统任务管理器；不得未经授权安装系统级任务。

**单次手动采集：**

```bash
python3 scripts/fetch_hotspots.py \
  --config <配置文件> \
  --skill-dir <last30days目录> \
  --output <输出JSON>
```

## 质量检查

- 确认三类实际条数均不超过配置，数量为 `0` 时省略该类。
- 确认热点未重复使用 7 天冷却期内的 URL；同日重复生成结果保持稳定。
- 保留来源链接；同一类型内按原始榜单/热度顺序排列。
- 确认每条热点都有中文描述；缺失时脚本会显示固定占位语，发送前必须补齐并重新生成。
- 确认 Weekly 每条都有趋势符号和 Star 增量，All-time 每条都有 Star 总数。
- 确认 Weekly 和 All-time 的每个仓库均有一句中文简介，英文官方 description 不得直接进入最终内容。
- 确认 Weekly 对比图（PNG）已生成（需 matplotlib）；若生成失败，脚本会在 JSON 输出中报告 `chart_error`，但不影响文本消息的生成。
- 对同一输入重复运行时，标题、章节、字段顺序、缩进和数字格式必须完全一致。
- 不输出 token、cookie 或内部鉴权信息。
- 任一上游失败时保留已生成的本地预览，但不得发送不完整消息。
