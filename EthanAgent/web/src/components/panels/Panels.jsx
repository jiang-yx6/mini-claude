export function OverviewPanel() {
  return (
    <div className="panel">
      <h2>概览</h2>
      <div className="panel-grid">
        <div className="stat-card">
          <span className="stat-label">Agent Status</span>
          <span className="stat-value ok">Running</span>
        </div>
        <div className="stat-card">
          <span className="stat-label">Channels</span>
          <span className="stat-value">1 active</span>
        </div>
        <div className="stat-card">
          <span className="stat-label">Sessions</span>
          <span className="stat-value">--</span>
        </div>
        <div className="stat-card">
          <span className="stat-label">Cron Jobs</span>
          <span className="stat-value">--</span>
        </div>
      </div>
    </div>
  )
}

export function ChannelsPanel() {
  return (
    <div className="panel">
      <h2>频道</h2>
      <table className="data-table">
        <thead>
          <tr>
            <th>Name</th>
            <th>Type</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>web</td>
            <td>WebSocket</td>
            <td><span className="badge ok">active</span></td>
          </tr>
          <tr>
            <td>weixin</td>
            <td>iLink</td>
            <td><span className="badge muted">not configured</span></td>
          </tr>
          <tr>
            <td>wecom</td>
            <td>WebSocket</td>
            <td><span className="badge muted">not configured</span></td>
          </tr>
        </tbody>
      </table>
    </div>
  )
}

export function SessionsPanel() {
  return (
    <div className="panel">
      <h2>会话</h2>
      <p className="panel-hint">在「聊天」页创建或切换会话；此处为管理视图占位。</p>
      <table className="data-table">
        <thead>
          <tr>
            <th>Session Key</th>
            <th>Messages</th>
            <th>Updated</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td colSpan={3} className="tc-muted">请前往聊天页查看活跃会话</td>
          </tr>
        </tbody>
      </table>
    </div>
  )
}

export function CronPanel() {
  return (
    <div className="panel">
      <h2>定时任务</h2>
      <p className="panel-hint">由 Agent 管理的计划任务。</p>
      <table className="data-table">
        <thead>
          <tr>
            <th>Job</th>
            <th>Schedule</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td colSpan={3} className="tc-muted">暂无定时任务</td>
          </tr>
        </tbody>
      </table>
    </div>
  )
}

export function SkillsPanel() {
  return (
    <div className="panel">
      <h2>技能</h2>
      <p className="panel-hint">已注册的技能与工具。</p>
      <table className="data-table">
        <thead>
          <tr>
            <th>Skill</th>
            <th>Description</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td colSpan={2} className="tc-muted">暂无技能</td>
          </tr>
        </tbody>
      </table>
    </div>
  )
}

export function MCPPanel() {
  return (
    <div className="panel">
      <h2>MCP</h2>
      <p className="panel-hint">Model Context Protocol 服务连接。</p>
      <table className="data-table">
        <thead>
          <tr>
            <th>Server</th>
            <th>Transport</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td colSpan={3} className="tc-muted">未连接 MCP 服务</td>
          </tr>
        </tbody>
      </table>
    </div>
  )
}

export function SettingsPanel() {
  return (
    <div className="panel">
      <h2>配置</h2>
      <div className="settings-section">
        <h3>Model</h3>
        <p className="panel-hint">通过环境变量配置模型与 API。</p>
        <dl className="kv-list">
          <dt>MODEL_ID</dt>
          <dd><code>deepseek-chat</code></dd>
          <dt>ANTHROPIC_BASE_URL</dt>
          <dd><code>https://api.deepseek.com/anthropic</code></dd>
        </dl>
      </div>
      <div className="settings-section">
        <h3>Workspace</h3>
        <p className="panel-hint">Agent 工作区与会话存储路径。</p>
      </div>
    </div>
  )
}
