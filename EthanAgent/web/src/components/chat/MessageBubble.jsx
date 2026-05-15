import ReactMarkdown from 'react-markdown'

const ROLE_LABEL = {
  user: '你',
  assistant: 'Agent',
  system: '系统',
}

/**
 * @param {{ message: { role: string, text: string } }} props
 */
export default function MessageBubble({ message }) {
  const { role, text } = message
  const isUser = role === 'user'
  const isAssistant = role === 'assistant'

  return (
    <li
      className={
        'message-row' +
        (isUser ? ' message-row--user' : '') +
        (isAssistant ? ' message-row--assistant' : '')
      }
    >
      {isAssistant && (
        <div className="message-avatar message-avatar--agent" aria-hidden>
          A
        </div>
      )}
      <article
        className={
          'message-bubble' +
          (isUser
            ? ' message-bubble--user'
            : isAssistant
              ? ' message-bubble--agent'
              : ' message-bubble--system')
        }
      >
        <span className={`message-role message-role--${role}`}>
          {ROLE_LABEL[role] || role}
        </span>
        <div className="message-body">
          <ReactMarkdown>{text}</ReactMarkdown>
        </div>
      </article>
      {isUser && (
        <div className="message-avatar message-avatar--user" aria-hidden>
          Y
        </div>
      )}
    </li>
  )
}
