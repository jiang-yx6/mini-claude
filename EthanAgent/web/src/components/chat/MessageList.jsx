import { useEffect, useRef } from 'react'
import MessageBubble from './MessageBubble.jsx'

/**
 * @param {{ messages: object[], loading?: boolean }} props
 */
export default function MessageList({ messages, loading }) {
  const endRef = useRef(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading])

  return (
    <div className="message-list-wrap">
      {loading && messages.length === 0 ? (
        <div className="message-list-empty">
          <span className="spinner" aria-hidden />
          <p>加载对话中…</p>
        </div>
      ) : messages.length === 0 ? (
        <div className="message-list-empty">
          <p className="message-list-empty-title">开始新对话</p>
          <p className="message-list-empty-hint">
            点击「新对话」或从右侧选择历史会话继续聊天
          </p>
        </div>
      ) : (
        <ul className="message-list" aria-live="polite">
          {messages.map((msg) => (
            <MessageBubble key={msg.id} message={msg} />
          ))}
          <li ref={endRef} className="message-list-anchor" aria-hidden />
        </ul>
      )}
    </div>
  )
}
