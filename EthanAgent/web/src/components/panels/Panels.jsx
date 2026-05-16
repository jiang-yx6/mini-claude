import { useState, useEffect } from 'react'
import { getAgentStatus } from '../../lib/api.js'

const MOCK = {
  agent: {
    status: 'running',
    model: 'deepseek-chat',
    uptime_seconds: 7980,
    version: 'v0.1.0',
    start_time: '',
    pid: 0,
  },
  sessions: { total: 0, web: 0, cli: 0, cron: 0 },
  channels: { active_connections: 0, registered: 0 },
  tools: { total: 0, names: [] },
  cron: null,
  mcp: { connected: false, servers: [] },
  memory: { initialized: false },
  usage: { prompt_tokens: 0, completion_tokens: 0, context_total: 65536 },
  activity: [],
}

const ACTIVITY_ICONS = { session: '💬', cron: '⏰', tool: '🔧', dream: '🧠' }

function fmtUptime(seconds) {
  if (!seconds || seconds <= 0) return '—'
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  if (h > 0) return `${h}h ${m}m`
  return `${m}m`
}

function fmtMsDuration(ms) {
  if (!ms) return '—'
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

function fmtNextRun(ms) {
  if (!ms) return '—'
  const diff = ms - Date.now()
  if (diff <= 0) return '即将'
  const m = Math.floor(diff / 60000)
  if (m < 60) return `${m}m 后`
  return `${Math.floor(m / 60)}h ${m % 60}m 后`
}

function scheduleLabel(kind) {
  switch (kind) {
    case 'every': return '循环'
    case 'at': return '单次'
    case 'cron': return 'Cron'
    default: return kind || '—'
  }
}

function StatusDot({ ok, pulse }) {
  return (
    <span
      className={'status-dot' + (ok ? ' status-dot--ok' : ' status-dot--err') + (pulse ? ' status-dot--pulse' : '')}
    />
  )
}

function LoadingSkeleton() {
  return (
    <div className="panel overview">
      <div className="overview-banner overview-banner--skeleton" />
      <div className="overview-stats">
        {[1,2,3,4].map(i => <div key={i} className="overview-stat overview-stat--skeleton" />)}
      </div>
      <div className="overview-cols">
        <div className="overview-card overview-card--skeleton" style={{minHeight: 180}} />
        <div className="overview-card overview-card--skeleton" style={{minHeight: 180}} />
      </div>
    </div>
  )
}

export function OverviewPanel() {
  const [data, setData] = useState(null)
  const [error, setError] = useState(false)

  useEffect(() => {
    let cancelled = false
    getAgentStatus()
      .then((res) => { if (!cancelled) { setData(res); setError(false) } })
      .catch(() => { if (!cancelled) setError(true) })
    return () => { cancelled = true }
  }, [])

  if (!data && !error) return <LoadingSkeleton />

  const src = data || MOCK
  const { agent, sessions, channels, tools, cron, mcp, memory, activity } = src
  const cronJobs = cron?.jobs || []
  const sessionsHist = sessions.total > 0 ? `Web ${sessions.web} · CLI ${sessions.cli} · Cron ${sessions.cron}` : '暂无会话'
  const connText = channels.active_connections > 0 ? `${channels.active_connections} 个连接` : '无连接'

  return (
    <div className="panel overview">
      {error && (
        <div className="overview-api-warning">无法连接后端，显示离线数据</div>
      )}

      {/* ── Agent Status Banner ── */}
      <div className="overview-banner">
        <div className="overview-banner-left">
          <StatusDot ok={agent.status === 'running'} pulse />
          <div>
            <span className="overview-banner-title">EthanAgent</span>
            <span className="overview-banner-ver">{agent.version}</span>
          </div>
          <span className="overview-banner-sep" />
          <span className="overview-banner-label">模型</span>
          <span className="overview-banner-val">{agent.model || '—'}</span>
          <span className="overview-banner-sep" />
          <span className="overview-banner-label">运行时间</span>
          <span className="overview-banner-val">{fmtUptime(agent.uptime_seconds)}</span>
          {agent.pid > 0 && (
            <>
              <span className="overview-banner-sep" />
              <span className="overview-banner-label">PID</span>
              <span className="overview-banner-val mono">{agent.pid}</span>
            </>
          )}
        </div>
        <div className="overview-banner-right">
          <span className="overview-banner-label">启动于</span>
          <span className="overview-banner-val">{agent.start_time || '—'}</span>
        </div>
      </div>

      {/* ── Quick Stats ── */}
      <div className="overview-stats">
        <div className="overview-stat">
          <span className="overview-stat-icon">💬</span>
          <div className="overview-stat-body">
            <span className="overview-stat-num">{sessions.total}</span>
            <span className="overview-stat-label">会话总数</span>
          </div>
          <span className="overview-stat-detail">{sessionsHist}</span>
        </div>
        <div className="overview-stat">
          <span className="overview-stat-icon">📡</span>
          <div className="overview-stat-body">
            <span className="overview-stat-num">{channels.active_connections}<span className="overview-stat-faint">/{channels.registered}</span></span>
            <span className="overview-stat-label">活跃连接/通道</span>
          </div>
          <span className="overview-stat-detail">{connText}</span>
        </div>
        <div className="overview-stat">
          <span className="overview-stat-icon">⏰</span>
          <div className="overview-stat-body">
            <span className="overview-stat-num">{cron?.total ?? '—'}</span>
            <span className="overview-stat-label">定时任务</span>
          </div>
          <span className="overview-stat-detail">{cronJobs.length > 0 ? `${cronJobs.filter(j => j.last_status === 'ok').length} 个正常` : '暂无'}</span>
        </div>
        <div className="overview-stat">
          <span className="overview-stat-icon">🔧</span>
          <div className="overview-stat-body">
            <span className="overview-stat-num">{tools.total}</span>
            <span className="overview-stat-label">已注册工具</span>
          </div>
          <span className="overview-stat-detail">{tools.names.slice(0, 3).join(' · ') || '—'}{tools.names.length > 3 ? ' …' : ''}</span>
        </div>
      </div>

      {/* ── System Health ── */}
      <div className="overview-card">
        <h3 className="overview-card-title">🟢 系统状态</h3>
        <div className="health-list">
          <div className="health-row">
            <StatusDot ok={mcp.connected} />
            <span className="health-name">MCP 服务</span>
            <span className="health-val">
              {mcp.connected ? `${mcp.servers.length} 个已连接` : '未连接'}
            </span>
            {mcp.connected && mcp.servers.length > 0 && (
              <span className="health-detail">{mcp.servers.join(', ')}</span>
            )}
          </div>
          <div className="health-row">
            <StatusDot ok={memory.initialized} />
            <span className="health-name">记忆存储</span>
            <span className="health-val">
              {memory.initialized ? 'Milvus 已初始化' : '未初始化'}
            </span>
          </div>
          <div className="health-row">
            <StatusDot ok />
            <span className="health-name">会话压缩器</span>
            <span className="health-val">TTL 30 分钟</span>
          </div>
          <div className="health-row">
            <StatusDot ok />
            <span className="health-name">并发限制</span>
            <span className="health-val">最多 3 个会话</span>
          </div>
        </div>
      </div>

      {/* ── Cron Jobs Table ── */}
      {cronJobs.length > 0 && (
        <div className="overview-card">
          <h3 className="overview-card-title">⏰ 定时任务</h3>
          <table className="data-table">
            <thead>
              <tr>
                <th>名称</th>
                <th>调度</th>
                <th>下次执行</th>
                <th>上次状态</th>
                <th>耗时</th>
              </tr>
            </thead>
            <tbody>
              {cronJobs.map((job) => (
                <tr key={job.id}>
                  <td><span className="job-name">{job.name}</span></td>
                  <td className="tc-mono">{scheduleLabel(job.schedule_kind)}</td>
                  <td className="tc-mono">{fmtNextRun(job.next_run_at_ms)}</td>
                  <td><span className={`badge ${job.last_status === 'ok' ? 'ok' : job.last_status === 'error' ? 'err' : 'muted'}`}>{job.last_status || '—'}</span></td>
                  <td className="tc-mono">{fmtMsDuration(job.last_duration_ms)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* ── Recent Activity ── */}
      {activity.length > 0 && (
        <div className="overview-card">
          <h3 className="overview-card-title">📋 最近活动</h3>
          <div className="activity-list">
            {activity.map((item, i) => {
              const hasCtx = item.context_total > 0 && item.context_token > 0
              const ctxPct = hasCtx ? Math.min(100, Math.round((item.context_token / item.context_total) * 100)) : 0
              return (
                <div key={i} className="activity-row">
                  <span className="activity-time">{item.time}</span>
                  <span className="activity-icon">{ACTIVITY_ICONS[item.kind] || '📌'}</span>
                  <div className="activity-body">
                    <span className="activity-text">{item.text}</span>
                    {hasCtx && (
                      <div className="activity-bar-wrap">
                        <div className="activity-bar-header">
                          <span className="activity-bar-label">上下文</span>
                          <span className="activity-bar-num">
                            {item.context_token.toLocaleString()} / {item.context_total.toLocaleString()}
                          </span>
                        </div>
                        <div className="activity-bar">
                          <div
                            className="activity-bar-fill"
                            style={{ width: `${ctxPct}%` }}
                          />
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}
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
