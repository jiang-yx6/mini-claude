import { useCallback, useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import './App.css'

type Role = 'user' | 'assistant' | 'system'

interface ChatMessage {
  id: string
  role: Role
  text: string
}

function apiBase(): string {
  const b = import.meta.env.VITE_API_BASE
  return typeof b === 'string' && b.length > 0 ? b.replace(/\/$/, '') : ''
}

function wsUrlForSession(sessionId: string): string {
  const b = apiBase()
  const base = b || window.location.origin
  try {
    const u = new URL(base)
    u.protocol = u.protocol === 'https:' ? 'wss:' : 'ws:'
    u.pathname = '/ws'
    u.searchParams.set('session_id', sessionId)
    return u.toString()
  } catch {
    return ''
  }
}

export default function App() {
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [wsConnected, setWsConnected] = useState(false)
  const wsRef = useRef<WebSocket | null>(null)

  const createSession = useCallback(async () => {
    setError(null)
    setLoading(true)
    try {
      const res = await fetch(`${apiBase()}/api/sessions`, { method: 'POST' })
      if (!res.ok) {
        const t = await res.text()
        throw new Error(t || `HTTP ${res.status}`)
      }
      const data = (await res.json()) as { session_id: string }
      setSessionId(data.session_id)
      setMessages([])
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!sessionId) {
      setWsConnected(false)
      return
    }
    const url = wsUrlForSession(sessionId)
    const ws = new WebSocket(url)
    wsRef.current = ws
    ws.onopen = () => {
      setWsConnected(true)
      setError(null)
    }
    ws.onclose = () => {
      setWsConnected(false)
      wsRef.current = null
    }
    ws.onerror = () => {
      setError('WebSocket 连接错误')
    }
    ws.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data as string) as {
          kind?: string
          content?: string
        }
        const kind = data.kind ?? 'assistant'
        const text = data.content ?? ''
        if (kind === 'assistant') {
          setMessages((m) => [
            ...m,
            {
              id: crypto.randomUUID(),
              role: 'assistant',
              text: text.length > 0 ? text : '(empty reply)',
            },
          ])
          setLoading(false)
        } else if (kind === 'error') {
          setMessages((m) => [
            ...m,
            { id: crypto.randomUUID(), role: 'system', text: `Error: ${text}` },
          ])
          setLoading(false)
        } else if (kind === 'cron') {
          setMessages((m) => [
            ...m,
            {
              id: crypto.randomUUID(),
              role: 'system',
              text: `[Cron] ${text}`,
            },
          ])
        }
      } catch {
        setLoading(false)
      }
    }
    return () => {
      ws.close()
      wsRef.current = null
    }
  }, [sessionId])

  const send = useCallback(() => {
    const text = input.trim()
    if (!sessionId || !text || loading) return
    const ws = wsRef.current
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      setError('WebSocket 未连接，请稍候再试')
      return
    }
    setError(null)
    setInput('')
    setMessages((m) => [...m, { id: crypto.randomUUID(), role: 'user', text }])
    setLoading(true)
    ws.send(JSON.stringify({ type: 'chat', message: text }))
  }, [sessionId, input, loading])

  return (
    <div className="chat-app">
      <header className="chat-header">
        <h1>EthanAgent</h1>
        <div className="session-row">
          <button type="button" disabled={loading} onClick={createSession}>
            新会话
          </button>
          {sessionId ? (
            <code className="session-id" title="当前会话 ID">
              {sessionId}
            </code>
          ) : (
            <span className="session-hint">请先创建会话</span>
          )}
          {sessionId ? (
            <span className="ws-status" data-connected={wsConnected}>
              {wsConnected ? 'WS 已连接' : 'WS 连接中…'}
            </span>
          ) : null}
        </div>
      </header>

      {error ? (
        <div className="chat-error" role="alert">
          {error}
        </div>
      ) : null}

      <ul className="chat-messages" aria-live="polite">
        {messages.map((msg) => (
          <li
            key={msg.id}
            className={
              msg.role === 'user'
                ? 'msg msg-user'
                : msg.role === 'assistant'
                  ? 'msg msg-assistant'
                  : 'msg msg-system'
            }
          >
            <span className="msg-role">
              {msg.role === 'user' ? 'You' : msg.role === 'assistant' ? 'Agent' : 'System'}
            </span>
            <div className="msg-text">
              <ReactMarkdown>{msg.text}</ReactMarkdown>
            </div>
          </li>
        ))}
      </ul>

      <footer className="chat-footer">
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={
            sessionId
              ? wsConnected
                ? '输入消息…'
                : '等待 WebSocket 连接…'
              : '创建会话后即可聊天'
          }
          disabled={!sessionId || loading || !wsConnected}
          rows={3}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              send()
            }
          }}
        />
        <button
          type="button"
          disabled={!sessionId || loading || !input.trim() || !wsConnected}
          onClick={send}
        >
          {loading ? '等待回复…' : '发送'}
        </button>
      </footer>
    </div>
  )
}
