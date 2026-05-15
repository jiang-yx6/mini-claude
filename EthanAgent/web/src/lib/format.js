export function formatRelativeTime(iso) {
  if (!iso) return '—'
  try {
    const d = new Date(iso)
    const mins = Math.floor((Date.now() - d.getTime()) / 60000)
    if (mins < 1) return '刚刚'
    if (mins < 60) return `${mins} 分钟前`
    const hours = Math.floor(mins / 60)
    if (hours < 24) return `${hours} 小时前`
    const days = Math.floor(hours / 24)
    if (days < 7) return `${days} 天前`
    return d.toLocaleDateString('zh-CN')
  } catch {
    return '—'
  }
}

export function shortSessionKey(key) {
  if (!key) return '未命名'
  if (key.startsWith('web:')) return key.slice(4, 14) + '…'
  if (key.startsWith('cron:')) return key.slice(5, 15) + '…'
  if (key.length > 14) return key.slice(0, 14) + '…'
  return key
}

export function sessionChannelLabel(key) {
  if (!key) return '?'
  if (key.startsWith('web:')) return 'Web'
  if (key.startsWith('cli:')) return 'CLI'
  if (key.startsWith('cron:')) return 'Cron'
  return key.split(':')[0] || '?'
}

export function previewFromMessages(messages) {
  if (!messages?.length) return '新对话'
  const last = [...messages].reverse().find((m) => m.role === 'user' || m.role === 'assistant')
  if (!last) return '新对话'
  const text = typeof last.content === 'string' ? last.content : ''
  const oneLine = text.replace(/\s+/g, ' ').trim()
  return oneLine.slice(0, 48) || '新对话'
}
