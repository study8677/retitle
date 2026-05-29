<div align="center">

# 🏷️ retitle

### 让你的 AI 编程会话，始终叫它*真正*在聊的事。

Claude Code、Codex、Cursor 都只用你的**第一条消息**给会话命名——然后就再也不管了。
两小时后对话早已聊到完全不同的东西，侧边栏却还写着*「检查分支是否同步」*。
乘以五十个会话，你的历史记录就彻底没法用来找东西了。

**`retitle` 在后台静静运行，每当一个会话空闲下来，就把它的标题改成最新在做的事——三个工具通吃。**

[![CI](https://github.com/study8677/retitle/actions/workflows/ci.yml/badge.svg)](https://github.com/study8677/retitle/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org)
[![Zero dependencies](https://img.shields.io/badge/dependencies-0-brightgreen.svg)](pyproject.toml)
[![PRs welcome](https://img.shields.io/badge/PRs-welcome-orange.svg)](CONTRIBUTING.md)

[English](README.md) · **简体中文**

</div>

---

## 痛点

每个 AI 编程工具都只在开场时根据第一条提示词命名一次，然后就把标题冻在那儿：

| 工具 | 侧边栏还显示 | 这个会话其实早就在做 |
|------|-------------|----------------------|
| **Cursor** | `加一个加载动画` | *把数据库迁移到 Postgres* |
| **Codex** | `修个 README 里的错别字` | *排查不稳定的 CI 流水线* |
| **Claude Code** | `检查分支是否同步` | *实现审计日志功能* |

标题在十分钟内就成了谎言。`retitle` 让它始终如实。

<sub>（示例均为虚构——`retitle` 只在本地读取你的会话，绝不会把它们发布到任何地方。）</sub>

## 长这样

```console
$ retitle list

Claude Code
     16m  检查分支是否同步             → 实现审计日志写入
     34m  —                          → 修复仪表盘加载白屏
      2m  重构部署脚本                 · 使用中

Codex
    1.2h  搭建新服务                   → 设计会话自动改名流程
    2.1h  审查 API 改动                · 距上次改名无新内容

Cursor
     29m  加一个加载动画               → 修复登录页样式问题
    2.4h  最初的同步问题               → 定位重复报错的根因

下一轮将重命名 7 个会话（空闲 ≥ 5m，namer=heuristic）。
运行 `retitle once` 立即应用，或 `retitle install` 让它持续运行。
```

---

## 快速开始

`retitle` 是纯 Python、**零依赖**。把它作为独立 CLI 安装：

```bash
# 用 pipx（推荐）
pipx install git+https://github.com/study8677/retitle.git

# 或用 uv
uv tool install git+https://github.com/study8677/retitle.git

# 或从源码
git clone https://github.com/study8677/retitle.git && cd retitle
pip install -e .
```

然后：

```bash
retitle status         # 在这台机器上检测到了什么？
retitle list           # 预览：当前标题 → 建议标题（不写入任何东西）
retitle once           # 立即跑一轮重命名
retitle install        # 装成后台服务，永久运行（launchd / systemd）
```

就这样。装好后它每分钟醒来一次，找出已空闲 5 分钟的会话，把其中「自上次以来内容有变化」的重新命名。

---

## 工作原理

```
        ┌──────────── 每隔 poll_seconds（默认 60s） ────────────┐
        │                                                       │
   discover ──► 对每个空闲 ≥ 5m 且有新内容的会话 ──► namer ──► 写回标题
   （按工具）        │                                   │           │
   Claude Code      │ 仍在使用 → 跳过                    │           ├─ Claude Code：追加一行 `ai-title`
   Codex            │ 自上次改名无变化 → 跳过            │           ├─ Codex：      UPDATE threads SET title
   Cursor           │ 被人工改过名 → 跳过（直到          │           └─ Cursor：     更新 composerHeaders + composerData
                    │     对话出现新内容）              │
```

每个会话的判定规则刻意保守：

1. **还在用?** 空闲时间未达到阈值 → 不动它。
2. **没新东西?** 内容哈希与上次写入的标题一致 → 跳过（重复运行不花一分钱）。
3. **手动改过名?** 我们绝不覆盖人工编辑——直到你发了新消息、它再次空闲为止。
4. 否则：生成一个新标题并写入。

这让整个工具**幂等**且**可以放心地长期运行**。

---

## 支持的工具

| 工具 | 读取 | 写入 | 状态 |
|------|------|------|------|
| **Claude Code** | `~/.claude/projects/**/<id>.jsonl` | 追加一行 `ai-title`（纯追加——最安全的写法） | ✅ 稳定 |
| **Codex** | `~/.codex/state_*.sqlite` + rollout 文件 | `UPDATE threads SET title` | ✅ 稳定 |
| **Cursor** | `state.vscdb`（`composerHeaders` + `composerData`） | 同时更新两处标题字段 | ⚠️ 实验性 |

> **关于「应用开着时写入」。** Codex 和 Cursor 把数据存在正在使用的 SQLite 数据库里。
> `retitle` 写入很谨慎（读取走只读连接、写入设了 `busy_timeout`），而且只碰*空闲*会话。
> 但 Cursor 尤其会把对话缓存在内存里，所以你在磁盘上改的标题，可能在你重新打开那个对话时
> 被运行中的 Cursor 覆盖。想让 Cursor 端结果最可靠，就在 Cursor 关闭时让 `retitle` 跑。
> Claude Code 的纯追加格式没有这个顾虑。

---

## 命名后端（namer）

默认 `retitle` 用 **heuristic（启发式）** 命名：从你最近一条实质性消息提炼标题。
即时、离线、零成本、无需 API key——但它本质上只是把你打的字清洗了一下。

想要真正好看的标题，就接一个模型。在配置里设置 `namer`（或用 `--namer`）：

| `namer` | 作用 | 配置 |
|---------|------|------|
| `heuristic` | 最近一条消息，清洗后 | 无（默认） |
| `claude` | 调用 `claude` CLI | 复用你现有的 Claude Code 登录 |
| `codex` | 调用 `codex` CLI | 复用你现有的 Codex 登录 |
| `anthropic` | 直连 Anthropic API | 需要 `ANTHROPIC_API_KEY` |
| `openai` | 直连 OpenAI API | 需要 `OPENAI_API_KEY` |

```bash
retitle once --namer claude     # 用智能命名跑一轮试试
```

---

## 配置

`retitle config` 会创建并打印 `~/.config/retitle/config.toml`：

```toml
idle_seconds = 300          # 空闲 5 分钟后改名
poll_seconds = 60           # 每分钟扫一次
tools = ["claude-code", "codex", "cursor"]
namer = "heuristic"         # heuristic | claude | codex | anthropic | openai
max_age_days = 7            # 忽略超过一周未活动的会话
min_user_messages = 1       # 至少要有这么多条真实消息
dry_run = false

[anthropic]
model = "claude-haiku-4-5"

[openai]
model = "gpt-4o-mini"
```

任何字段都能在单次运行时覆盖：`retitle run --idle 600 --namer anthropic --tool cursor`。

## 命令

| 命令 | 说明 |
|------|------|
| `retitle list` | 预览所有发现的会话及其建议标题（不写入任何东西） |
| `retitle once` | 跑一轮重命名后退出 |
| `retitle run` | 在前台持续运行（可加 `--once`、`--dry-run`） |
| `retitle install` | 安装并启动后台服务（macOS 用 launchd，Linux 用 systemd） |
| `retitle uninstall` | 停止并移除后台服务 |
| `retitle status` | 显示配置、检测到的工具、守护进程状态 |
| `retitle config` | 创建 / 打印配置文件 |

---

## 隐私与安全

- **一切都留在你本机。** 用默认的 `heuristic` 命名时，没有任何数据离开你的电脑。
  只有 `anthropic`/`openai` 命名会把一小段对话摘录发给对应 API（且仅在你主动配置 key 时）；
  `claude`/`codex` 命名走的是你早已授权的工具。
- **它只改标题。** `retitle` 读取对话、写入一个标题字段 / 追加一行，
  从不编辑、删除或重排你的对话内容。
- **可逆且幂等。** 标题改坏了也只是个标题——发条消息它就会重新评估。
  内容没变时重复运行什么都不做。

## 常见问题

**会和工具自带的自动命名打架吗?**
不会。工具只命名一次就停了；`retitle` 只在会话空闲后才动手，两者不会同时写。

**会覆盖我自己设的标题吗?**
不会——除非你给那个会话发了新消息。在对话真正往前走之前，人工标题都会被尊重。

**会消耗 API token 吗?**
只有当你选了 LLM 命名后端时才会。默认的启发式是免费且离线的。

**一直开着安全吗?**
安全——这就是它的设计目标。见[工作原理](#工作原理)。唯一的注意点是「Cursor 开着时改它的数据库」（见上文）。

## 参与贡献

新增一个工具的支持只需一个文件——在 `src/retitle/adapters/` 里实现四个方法
（`available`、`discover`、`read_transcript`、`set_title`）。详见 [CONTRIBUTING.md](CONTRIBUTING.md)。

```bash
git clone https://github.com/study8677/retitle.git && cd retitle
pip install -e ".[dev]"
pytest
```

## 许可证

[MIT](LICENSE) © JingWen Fan
