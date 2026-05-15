import { useCallback, useEffect, useRef, useState } from 'react'
import {
  createSession,
  getSessionMessages,
  mapApiMessages,
} from '../../lib/api.js'
import { useSessions } from '../../hooks/useSessions.js'
import { useWebSocket } from '../../hooks/useWebSocket.js'
import MessageList from './MessageList.jsx'
import Composer from './Composer.jsx'
import SessionSidebar from './SessionSidebar.jsx'

function newId() {
  return crypto.randomUUID()
}

/**
 * Chat tab: load session history, switch sessions, continue via WebSocket.
 * @param {{ onSessionChange?: (id: string | null) => void }} props
 */
export default function ChatWorkspace({ onSessionChange }) {
  const [sessionId, setSessionId] = useState(null)
  const [sessionKey, setSessionKey] = useState('')
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [loadingSession, setLoadingSession] = useState(false)
  const [sending, setSending] = useState(false)
  const [error, setError] = useState(null)

  const activeSessionRef = useRef(null)
  const loadAbortRef = useRef(null)

  const { sessions, loading: listLoading, error: listError, refresh } =
    useSessions()

  useEffect(() => {
    activeSessionRef.current = sessionId
    onSessionChange?.(sessionId)
  }, [sessionId, onSessionChange])

  const loadSession = useCallback(async (id) => {
    if (!id) return

    loadAbortRef.current?.abort()
    const ac = new AbortController()
    loadAbortRef.current = ac

    setLoadingSession(true)
    setError(null)

    try {
      const data = await getSessionMessages(id, ac.signal)
      if (activeSessionRef.current !== id) return

      setSessionKey(data.key || `web:${id}`)
      setMessages(mapApiMessages(data.messages))
    } catch (e) {
      if (e instanceof Error && e.name === 'AbortError') return
      if (activeSessionRef.current !== id) return
      setError(e instanceof Error ? e.message : String(e))
      setMessages([])
    } finally {
      if (loadAbortRef.current === ac) {
        setLoadingSession(false)
      }
    }
  }, [])

  const activateSession = useCallback(
    async (id) => {
      loadAbortRef.current?.abort()
      activeSessionRef.current = id
      setSessionId(id)
      setInput('')
      setSending(false)
      setMessages([])
      setSessionKey('')
      setError(null)
      await loadSession(id)
    },
    [loadSession],
  )

  const handleNewChat = useCallback(async () => {
    loadAbortRef.current?.abort()
    setError(null)
    setLoadingSession(true)
    setSending(false)
    try {
      const { session_id } = await createSession()
      activeSessionRef.current = session_id
      setSessionId(session_id)
      setSessionKey(`web:${session_id}`)
      setMessages([])
      setInput('')
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoadingSession(false)
    }
  }, [refresh])

  const handleWsMessage = useCallback(
    (payload) => {
      const currentId = activeSessionRef.current
      if (!currentId) return

      const { kind, content } = payload
      if (kind === 'assistant') {
        setMessages((prev) => {
          if (activeSessionRef.current !== currentId) return prev
          return [
            ...prev,
            {
              id: newId(),
              role: 'assistant',
              text: content || '(空回复)',
            },
          ]
        })
        setSending(false)
        refresh()
      } else if (kind === 'error') {
        setMessages((prev) => {
          if (activeSessionRef.current !== currentId) return prev
          return [
            ...prev,
            { id: newId(), role: 'system', text: `错误: ${content}` },
          ]
        })
        setSending(false)
      } else if (kind === 'cron') {
        setMessages((prev) => {
          if (activeSessionRef.current !== currentId) return prev
          return [
            ...prev,
            { id: newId(), role: 'system', text: `[Cron] ${content}` },
          ]
        })
      }
    },
    [refresh],
  )

  const { connected, ready, error: wsError, sendChat } = useWebSocket(
    sessionId,
    handleWsMessage,
  )

  const send = useCallback(() => {
    const text = input.trim()
    if (!sessionId || !text || sending) return
    try {
      sendChat(text)
      setInput('')
      setMessages((prev) => [
        ...prev,
        { id: newId(), role: 'user', text },
      ])
      setSending(true)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [sessionId, input, sending, sendChat])

  const displayError = error || wsError
  const canSend = sessionId && ready && !sending && !loadingSession

  return (
    <div className="chat-workspace">
      <section className="chat-main">
        <header className="chat-main-header">
          <div>
            <h2 className="chat-main-title">聊天</h2>
            {sessionId ? (
              <p className="chat-main-sub">
                <span
                  className={
                    'ws-dot' + (ready ? ' ws-dot--on' : '')
                  }
                  aria-hidden
                />
                {ready ? '已连接' : connected ? '连接中…' : '未连接'}
                <code className="session-key-chip" title={sessionKey}>
                  {sessionKey || sessionId}
                </code>
              </p>
            ) : (
              <p className="chat-main-sub">选择或创建一个会话开始</p>
            )}
          </div>
        </header>

        {displayError && (
          <div className="chat-banner chat-banner--error" role="alert">
            {displayError}
          </div>
        )}

        <MessageList messages={messages} loading={loadingSession} />

        <Composer
          value={input}
          onChange={setInput}
          onSend={send}
          disabled={!canSend}
          sending={sending}
          placeholder={
            !sessionId
              ? '请先创建或选择会话'
              : ready
                ? '输入消息，Enter 发送，Shift+Enter 换行'
                : '等待 WebSocket 连接…'
          }
        />
      </section>

      <SessionSidebar
        sessions={sessions}
        currentSessionId={sessionId}
        loading={listLoading}
        error={listError}
        onRefresh={refresh}
        onSelect={activateSession}
        onNewChat={handleNewChat}
      />
    </div>
  )
}
