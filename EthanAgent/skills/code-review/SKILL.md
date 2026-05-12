---
name: code-review
description: 对代码做安全、正确性、性能与可维护性审查。适用于用户要求 code review、查 bug、审计 EthanAgent/工作区代码时。
metadata:
  always: false
---

# Code Review（EthanAgent 工作区）

在 **EthanAgent** 里做审查时，优先用 **`read_file`** / **`list_dir`** 看代码；需要跑命令时用 **`shell`**（受工作区限制）。不要猜测未读过的文件内容。

## 何时启用

- 用户明确要「代码审查 / code review / 看有没有 bug / 安全审计」
- 涉及改动 `agent_runner.py`、`tools/`、`cron/`、`providers/`、`session/` 等核心路径时，按本清单走一遍

## 审查清单

### 1. 安全（优先）

- [ ] **注入**：`shell` 是否拼接不可信字符串；`eval` / `exec` 动态代码；SQL/format 字符串拼接
- [ ] **密钥**：`.env`、硬编码 API key；日志是否打印敏感字段
- [ ] **路径穿越**：`read_file` / `write_file` 的路径是否校验在工作区内（Ethan 的 `safe_path`）
- [ ] **依赖**：`pip audit` 或至少关注 `requirements` / `pyproject` 里已知风险包

```text
# 在工作区根下用 shell（示例，按环境调整）
pip audit
# 或搜索敏感字面量（注意勿把真实密钥写进对话）
rg -n "api_key|API_KEY|secret|password|token" --glob "*.py" .
```

### 2. 正确性

- [ ] **异步**：`async` 里是否误用阻塞 I/O；`gather` 与异常处理
- [ ] **边界**：空列表、None、`Path` 不存在
- [ ] **资源**：文件句柄、锁、后台 task 是否泄漏
- [ ] **异常**：裸 `except`、吞错、错误信息不足以定位

### 3. 性能

- [ ] **热路径**：大循环里重复读盘 / 重复 token 估算
- [ ] **上下文**：system prompt 与 history 是否过大；`_microcompact` 与模型预算是否匹配
- [ ] **Cron / 定时**：时区、`ZoneInfo`、任务幂等

### 4. 可维护性

- [ ] **命名与模块边界**：`MemoryStore` / `ContextBuilder` / `AgentRunner` 职责是否清晰
- [ ] **重复逻辑**：工具注册、消息格式（Anthropic blocks）是否分散难改
- [ ] **注释**：仅保留非显而易见契约（避免废话）

### 5. 测试与可观测

- [ ] 是否有针对关键路径的脚本或手工检查步骤
- [ ] `loguru` 日志级别与关键分支是否足够追踪问题

## 输出格式（回复用户时用）

```markdown
## Code Review：<文件或模块名>

### 摘要
（一两句总评）

### 严重问题
1. **<标题>**（约第 X 行 / 函数 Y）：<说明>
   - 影响：<…>
   - 建议：<…>

### 改进建议
1. **<标题>**：<说明>

### 亮点
- <做得好的地方>

### 结论
- [ ] 可直接合入
- [ ] 需小改
- [ ] 需大改
```

## EthanAgent 相关注意点

- **消息格式**：助手侧为 Anthropic 风格 `tool_use` / `tool_result`；改 `AgentRunner` 时勿破坏与 `AnthropicProvider` 的转换。
- **工作目录**：`read_file` 等工具以 **进程当前工作目录** 为 workspace 根；审查 CLI 入口时确认启动目录。
- **长期记忆**：`memory/*.md` 与 `history.jsonl` 由 Consolidator / Dream 维护；审查时不要建议直接删游标文件除非用户要求。
- **Cron**：`system_event` 类任务不可随意删；`every` 与 `cron`+IANA 在 Windows 上注意 `tzdata`。

## Python 反例（审查时对照）

```python
# Bad：可变默认参数
def f(x, acc=[]): ...

# Bad：宽泛捕获
try:
    ...
except Exception:
    pass

# Bad：未校验的路径
open(user_supplied_path).read()

# Good：明确异常与路径约束
```

## 推荐工作流

1. 用 **`list_dir`** 确认范围，用 **`read_file`** 读入口与改动最大文件。
2. 若有 Git，可用 **`shell`**：`git diff`、`git log -n`（在允许的工作区内）。
3. 按清单逐类扫一遍，再按「输出格式」写结论。
4. 不确定处标「需运行测试验证」，不要编造行号。

## 与 tutorials 版差异

- 工具名对齐 **EthanAgent**（`read_file`、`edit_file`、`shell`、`list_dir`、`cron` 等），不假设仅有 `npm`。
- 增加与本仓库相关的 **Agent / Cron / 记忆** 提示，便于审查 `EthanAgent/` 目录本身。
