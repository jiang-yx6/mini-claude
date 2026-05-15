# EthanAgent

## Web UI（前后端分离 + 消息总线）

### 架构概要

- **`MessageBus`**（[`bus/queue.py`](EthanAgent/bus/queue.py)）：进程内 **`inbound` / `outbound` 两个 `asyncio.Queue`**。
- **`InboundMessage` / `OutboundMessage`**（[`bus/events.py`](EthanAgent/bus/events.py)）：通道与 Agent 之间的统一信封。
- **`BusManager`**（[`bus/manager.py`](EthanAgent/bus/manager.py)）：从 `outbound` 取消息并按 `msg.channel` 分发给已注册的 **`BaseChannel`**。
- **`WebChannel`**（[`channels/web.py`](EthanAgent/channels/web.py)）：维护 `web:<session>` → WebSocket 连接；`send` 时向对应浏览器推送 JSON。

用户聊天路径：**浏览器 WS** → `publish_inbound` → **`inbound_worker`**（[`api/app.py`](EthanAgent/api/app.py)）里 `process_direct` → `publish_outbound` → **BusManager** → **WebChannel** → 同一 WS 回传。

定时任务（Cron）若配置了 **`deliver` + `session_key`（`web:...`）**，在 [`agent_runner.attach_cron_job_handler`](EthanAgent/agent_runner.py) 里会向 **`outbound`** 投递，同样经 BusManager 推到浏览器（`kind: cron`）。

### 配置与启动

1. **环境**：在 `EthanAgent` 目录准备 `.env`（或系统环境变量），至少包含 `ANTHROPIC_API_KEY` 或 `DEEPSEEK_API_KEY`，以及按需的 `ANTHROPIC_API_BASE`、`MODEL_NAME`（与 CLI [`gateway.py`](EthanAgent/gateway.py) 相同）。

2. **后端**（必须在 `EthanAgent` 目录下执行）：

   ```bash
   cd EthanAgent
   uv run uvicorn api.app:app --host 0.0.0.0 --port 8000 --reload
   ```

   - 探活：`GET http://127.0.0.1:8000/api/health`
   - 创建会话：`POST http://127.0.0.1:8000/api/sessions` → `{ "session_id": "web:..." }`
   - **WebSocket**：`ws://127.0.0.1:8000/ws?session_id=web:...`（HTTPS 部署时用 `wss:`）

3. **前端**（[`frontend/.env.development`](EthanAgent/frontend/.env.development) 中 `VITE_API_BASE=http://127.0.0.1:8000`，用于 HTTP 建会话与推导 WS URL）：

   ```bash
   cd EthanAgent/frontend
   npm install
   npm run dev
   ```

   Vite 默认 **http://localhost:5173**。其他前端端口请设置 `ETHAN_CORS_ORIGINS` 后重启 API。

### WebSocket 消息约定

- **客户端 → 服务端**（发用户话）：

  ```json
  { "type": "chat", "message": "用户输入文本" }
  ```

- **服务端 → 客户端**（JSON 文本帧）：

  - 正常回复：`{ "kind": "assistant", "content": "...", "channel": "web" }`
  - 错误：`{ "kind": "error", "content": "错误说明", "channel": "web" }`
  - Cron 推送：`{ "kind": "cron", "content": "...", "channel": "web", "metadata": { "job_id": "...", "job_name": "..." } }`

**说明**：已移除 `POST /api/chat`；聊天仅通过 **WebSocket + inbound 队列**。

不要与 `python gateway.py`（CLI REPL）同时长期运行，以免共享 `cron/jobs.json` 等产生干扰；CLI 路径下 `attach_cron_job_handler` 不传 `message_bus`，Cron 结果仍打印到终端而不会推送到 Web。
