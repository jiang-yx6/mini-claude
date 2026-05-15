import {
  formatRelativeTime,
  sessionChannelLabel,
  shortSessionKey,
} from '../../lib/format.js'

/**
 * @param {{
 *   sessions: object[],
 *   currentSessionId: string | null,
 *   loading: boolean,
 *   error: string | null,
 *   onRefresh: () => void,
 *   onSelect: (sessionId: string) => void,
 *   onNewChat: () => void,
 * }} props
 */
export default function SessionSidebar({
  sessions,
  currentSessionId,
  loading,
  error,
  onRefresh,
  onSelect,
  onNewChat,
}) {
  return (
    <aside className="session-rail">
      <header className="session-rail-header">
        <h3 className="session-rail-title">会话</h3>
        <div className="session-rail-actions">
          <button
            type="button"
            className="btn-icon"
            title="刷新列表"
            disabled={loading}
            onClick={onRefresh}
          >
            ↻
          </button>
          <button type="button" className="btn-new-chat" onClick={onNewChat}>
            + 新对话
          </button>
        </div>
      </header>

      {error && <p className="session-rail-error">{error}</p>}

      <div className="session-rail-list">
        {sessions.length === 0 && !loading ? (
          <p className="session-rail-empty">暂无会话，点击「新对话」开始</p>
        ) : (
          sessions.map((s) => {
            const active = s.session_id === currentSessionId
            return (
              <button
                key={s.key}
                type="button"
                className={'session-card' + (active ? ' session-card--active' : '')}
                onClick={() => onSelect(s.session_id)}
              >
                <div className="session-card-top">
                  <span className="session-card-title">
                    {shortSessionKey(s.key)}
                  </span>
                  <span className="session-card-badge">
                    {sessionChannelLabel(s.key)}
                  </span>
                </div>
                <div className="session-card-meta">
                  <span>{formatRelativeTime(s.updated_at)}</span>
                  <span>{s.message_count} 条</span>
                </div>
              </button>
            )
          })
        )}
      </div>
    </aside>
  )
}
