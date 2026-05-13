# EthanAgent 长期记忆

## 项目记录
- 项目名称：EthanAgent，基于Anthropic API（兼容DeepSeek Chat），代码量约16个Python文件
- 已完成功能：ReAct Agent流程、工具调用、上下文压缩、Session管理、Checkpoint机制
- 待优化项：长期记忆未加载到system prompt（context.py中get_memory_context()被注释掉）；Dream类未实现；Session TTL（30分钟）过短需延长；上下文窗口大小硬编码为65K tokens需动态适配；并发控制（Semaphore=3）对CLI模式不适用；工具调用缺乏独立超时控制；错误处理可增加重试和更友好提示
- CronTool bug：list报AttributeError（_format_timestamp），remove无法通过名称移除任务，cron执行上下文中无法创建新任务
- 系统存在ZoneInfoNotFoundError: 'No time zone found with key UTC'
- 执行Python脚本时需先cd到脚本所在目录再运行

## 代码审查记录
- SessionManager.list_sessions：key重建逻辑bug（`replace("_", ":", 1)`），需修复
- providers/base.py：未使用的import（`from ast import UAdd`、`from tkinter import ANCHOR`、`from anthropic.types import content_block`），需清理
- 结构建议：为所有子包添加`__init__.py`（高优先级）；拆分`agent_runner.py`；清理`commands/commands.py`命名冗余
