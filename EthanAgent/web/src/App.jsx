import { useState } from 'react'
import Sidebar from './components/Sidebar.jsx'
import ChatWorkspace from './components/chat/ChatWorkspace.jsx'
import {
  OverviewPanel,
  ChannelsPanel,
  SessionsPanel,
  CronPanel,
  SkillsPanel,
  MCPPanel,
  SettingsPanel,
} from './components/panels/Panels.jsx'
import './styles/app.css'

export default function App() {
  const [activeTab, setActiveTab] = useState('chat')

  return (
    <div className="app-shell">
      <Sidebar activeTab={activeTab} onSelect={setActiveTab} />

      <main className="app-main">
        {activeTab === 'chat' && <ChatWorkspace />}
        {activeTab === 'overview' && <OverviewPanel />}
        {activeTab === 'channels' && <ChannelsPanel />}
        {activeTab === 'sessions' && <SessionsPanel />}
        {activeTab === 'cron' && <CronPanel />}
        {activeTab === 'skills' && <SkillsPanel />}
        {activeTab === 'mcp' && <MCPPanel />}
        {activeTab === 'settings' && <SettingsPanel />}
      </main>
    </div>
  )
}
