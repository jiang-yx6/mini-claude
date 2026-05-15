/** @typedef {'chat'|'overview'|'channels'|'sessions'|'cron'|'skills'|'mcp'|'settings'} Tab */

const NAV_ITEMS = [
  { id: 'chat', label: '聊天', icon: '💬' },
  { id: 'overview', label: '概览', icon: '📊' },
  { id: 'channels', label: '频道', icon: '📡' },
  { id: 'sessions', label: '会话', icon: '📋' },
  { id: 'cron', label: '定时任务', icon: '⏰' },
  { id: 'skills', label: '技能', icon: '🔧' },
  { id: 'mcp', label: 'MCP', icon: '🔌' },
  { id: 'settings', label: '配置', icon: '⚙️' },
]

/**
 * @param {{ activeTab: Tab, onSelect: (tab: Tab) => void }} props
 */
export default function Sidebar({ activeTab, onSelect }) {
  return (
    <aside className="sidebar">
      <div className="sidebar-brand">
        <span className="sidebar-logo">EA</span>
        <span className="sidebar-title">EthanAgent</span>
      </div>

      <nav className="sidebar-nav">
        {NAV_ITEMS.map((item) => (
          <button
            key={item.id}
            type="button"
            className={
              'nav-item' + (activeTab === item.id ? ' nav-item--active' : '')
            }
            onClick={() => onSelect(item.id)}
          >
            <span className="nav-icon">{item.icon}</span>
            <span className="nav-label">{item.label}</span>
          </button>
        ))}
      </nav>

      <div className="sidebar-footer">
        <span className="sidebar-version">v0.1.0</span>
      </div>
    </aside>
  )
}
