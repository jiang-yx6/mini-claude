# EthanAgent API 接口文档

后端默认地址：`http://127.0.0.1:8000`  
Web 开发时通过 Vite 代理访问同源路径（`/api`、`/ws`）。

## 通用约定

| 项目 | 说明 |
|------|------|
| 编码 | UTF-8，JSON 响应 |
| Session ID | 32 位十六进制字符串（UUID hex），**不含** `web:` 前缀 |
| Session Key | 存储用完整键，Web 会话为 `web:{session_id}` |
| 错误 | HTTP 4xx/5xx，body 可能为纯文本或 JSON |

环境变量：

- `ETHAN_CORS_ORIGINS`：允许的前端源，逗号分隔（默认含 `http://localhost:5173`）
- `VITE_API_BASE`（前端）：可选，覆盖 API 根地址

---

## REST

### GET `/api/health`

健康检查。

**响应 200**

```json
{ "ok": true }
```

---

### POST `/api/sessions`

创建新的 Web 会话。

**响应 200**

```json
{
  "session_id": "fd22dfba52024faeb0bc16b80d33bbe1"
}
```

- 服务端 session key：`web:{session_id}`
- 持久化文件：`.sessions/web_{session_id}.jsonl`

---

### GET `/api/sessions`

列出所有已持久化的会话。

**响应 200**

```json
{
  "sessions": [
    {
      "session_id": "fd22dfba52024faeb0bc16b80d33bbe1",
      "key": "web:fd22dfba52024faeb0bc16b80d33bbe1",
      "created_at": "2026-05-15T17:57:59.938255",
      "updated_at": "2026-05-15T17:58:10.602804",
      "message_count": 4
    }
  ]
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `session_id` | string | 裸 ID，用于 WS 与路径参数 |
| `key` | string | 完整 session key |
| `created_at` | string \| null | ISO 8601 |
| `updated_at` | string \| null | ISO 8601 |
| `message_count` | number | 当前消息条数 |

---

### GET `/api/sessions/{session_id}/messages`

获取指定会话的消息历史。

**路径参数**

- `session_id`：裸 hex（与 `POST /api/sessions` 返回一致）

**响应 200**

```json
{
  "session_id": "fd22dfba52024faeb0bc16b80d33bbe1",
  "key": "web:fd22dfba52024faeb0bc16b80d33bbe1",
  "messages": [
    {
      "role": "user",
      "content": "你好",
      "timestamp": "2026-05-15T17:58:05.531124"
    },
    {
      "role": "assistant",
      "content": "你好！有什么我可以帮你的？",
      "timestamp": "2026-05-15T17:58:05.531124"
    }
  ]
}
```

服务端会依次尝试 key：`web:{session_id}`、`{session_id}`，并返回消息较多的一份（兼容旧数据）。

---

## WebSocket

### `WS /ws?session_id={session_id}`

与 Agent 实时对话。`session_id` 为裸 hex。

**连接**

- 成功：101 Switching Protocols
- 缺少参数：关闭码 `4401`，reason `missing session_id`

**客户端 → 服务端（文本 JSON）**

```json
{
  "type": "chat",
  "message": "用户输入内容"
}
```

**服务端 → 客户端（文本 JSON）**

```json
{
  "kind": "assistant",
  "content": "助手回复",
  "channel": "web"
}
```

| `kind` | 说明 |
|--------|------|
| `assistant` | 正常回复 |
| `error` | 处理错误 |
| `cron` | 定时任务推送（含 metadata） |

**Session 存储**

- 入站消息经 `InboundMessage.session_key` 解析为 `web:{session_id}`（`channel=web`，`chat_id=session_id`）
- 与 `POST /api/sessions` 使用同一 key，保证可继续聊天

---

## 前端封装（`web/src/lib/api.js`）

| 函数 | 对应接口 |
|------|----------|
| `healthCheck()` | GET `/api/health` |
| `createSession()` | POST `/api/sessions` |
| `listSessions()` | GET `/api/sessions` |
| `getSessionMessages(id)` | GET `/api/sessions/{id}/messages` |
| `getWsUrl(id)` | WS `/ws?session_id=` |
| `buildChatFrame(text)` | WS 发送帧 |
| `mapApiMessages(rows)` | 将 API 消息转为 UI 结构 |

错误类型：`ApiError`（含 `status` 字段）。
