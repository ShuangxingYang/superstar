// 与后端 chat.py / loop.py / session.py 的协议对齐
export type ChatEvent =
  | { type: 'session'; session_id: string; title: string }
  | { type: 'text'; content: string }
  | { type: 'tool_call'; id: string; name: string; args: string }
  | { type: 'tool_result'; id: string; result: string }
  | { type: 'done' }
  | { type: 'error'; message: string }

export type SessionMeta = {
  id: string
  title: string
  created_at: string
  updated_at: string
}

// 后端 JSONL 里存的原始消息形状(历史回放要按它还原工具卡片)
export type StoredMessage = {
  role: 'user' | 'assistant' | 'tool'
  content: string | null
  tool_calls?: { id: string; function: { name: string; arguments: string } }[]
  tool_call_id?: string
}

// ---- 会话 CRUD ----
export async function listSessions(): Promise<SessionMeta[]> {
  const r = await fetch('/api/sessions')
  if (!r.ok) throw new Error('拉取会话列表失败')
  return r.json()
}

export async function getSession(sid: string): Promise<StoredMessage[]> {
  const r = await fetch(`/api/sessions/${sid}`)
  if (!r.ok) throw new Error('拉取会话历史失败')
  return (await r.json()).messages
}

export async function renameSession(sid: string, title: string): Promise<SessionMeta> {
  const r = await fetch(`/api/sessions/${sid}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title }),
  })
  if (!r.ok) throw new Error('重命名失败')
  return r.json()
}

export async function deleteSession(sid: string): Promise<void> {
  const r = await fetch(`/api/sessions/${sid}`, { method: 'DELETE' })
  if (!r.ok) throw new Error('删除失败')
}

// ---- 流式对话(带可选 sessionId;不传 = 懒创建) ----
export async function streamChat(
  message: string,
  onEvent: (e: ChatEvent) => void,
  sessionId?: string,
): Promise<void> {
  const resp = await fetch('/api/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, session_id: sessionId ?? null }),
  })
  if (!resp.body) throw new Error('无响应体')

  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const parts = buffer.split('\n\n')   // SSE 事件以空行分隔
    buffer = parts.pop() ?? ''           // 最后一段可能不完整,留到下次
    for (const part of parts) {
      const line = part.trim()
      if (!line.startsWith('data:')) continue
      const payload = line.slice(line.indexOf('data:') + 5).trim()
      if (payload) onEvent(JSON.parse(payload) as ChatEvent)
    }
  }
}
