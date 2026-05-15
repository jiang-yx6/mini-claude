import { useCallback, useEffect, useRef, useState } from 'react'
import { buildChatFrame, getWsUrl } from '../lib/api.js'

/**
 * @param {string | null} sessionId
 * @param {(payload: { kind: string, content: string }) => void} onMessage
 */
export function useWebSocket(sessionId, onMessage) {
  const [connected, setConnected] = useState(false)
  const [error, setError] = useState(null)
  const wsRef = useRef(null)
  const onMessageRef = useRef(onMessage)
  onMessageRef.current = onMessage

  useEffect(() => {
    if (!sessionId) {
      wsRef.current = null
      setConnected(false)
      setError(null)
      return
    }

    let alive = true
    const url = getWsUrl(sessionId)
    const ws = new WebSocket(url)
    wsRef.current = ws
    setConnected(false)
    setError(null)

    const handleOpen = () => {
      if (!alive || wsRef.current !== ws) return
      setConnected(true)
      setError(null)
    }

    const handleClose = () => {
      if (wsRef.current === ws) {
        wsRef.current = null
      }
      if (!alive) return
      setConnected(false)
    }

    const handleError = () => {
      if (!alive) return
      setError('WebSocket 连接失败')
      setConnected(false)
    }

    const handleMessage = (ev) => {
      if (!alive || wsRef.current !== ws) return
      try {
        const data = JSON.parse(ev.data)
        onMessageRef.current?.({
          kind: data.kind ?? 'assistant',
          content: data.content ?? '',
        })
      } catch {
        /* ignore malformed frames */
      }
    }

    ws.addEventListener('open', handleOpen)
    ws.addEventListener('close', handleClose)
    ws.addEventListener('error', handleError)
    ws.addEventListener('message', handleMessage)

    return () => {
      alive = false
      setConnected(false)
      ws.removeEventListener('open', handleOpen)
      ws.removeEventListener('close', handleClose)
      ws.removeEventListener('error', handleError)
      ws.removeEventListener('message', handleMessage)
      if (
        ws.readyState === WebSocket.OPEN ||
        ws.readyState === WebSocket.CONNECTING
      ) {
        ws.close()
      }
      if (wsRef.current === ws) {
        wsRef.current = null
      }
    }
  }, [sessionId])

  const sendChat = useCallback((text) => {
    const ws = wsRef.current
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      throw new Error('WebSocket 未连接')
    }
    ws.send(buildChatFrame(text))
  }, [])

  /** True only when the current socket is open and send-safe. */
  const ready =
    connected &&
    wsRef.current != null &&
    wsRef.current.readyState === WebSocket.OPEN

  return { connected, ready, error, sendChat }
}
