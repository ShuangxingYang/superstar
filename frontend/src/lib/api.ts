// 与后端 chat.py / loop.py / session.py 的协议对齐
export type ChatEvent =
  | { type: 'session'; session_id: string; title: string }
  | { type: 'text'; content: string }
  | { type: 'tool_call'; id: string; name: string; args: string }
  | { type: 'tool_result'; id: string; result: string }
  | { type: 'approval_required'; id: string; name: string; args: string; preview: ApprovalPreview }
  | { type: 'done' }
  | { type: 'error'; message: string }

// 待审批操作的预览:写文件带 diff,跑命令带命令串,加工作区带绝对路径
export type ApprovalPreview =
  | { kind: 'write'; path: string; diff: string }
  | { kind: 'command'; command: string; level: string }
  | { kind: 'add_workspace'; path: string }

// 会话里未决的审批(GET /api/sessions/{sid} 回传;刷新后还原待审批卡)
export type PendingState = {
  tool_calls: { id: string; function: { name: string; arguments: string } }[]
  previews: Record<string, ApprovalPreview>
} | null

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

export async function getSession(
  sid: string,
): Promise<{ messages: StoredMessage[]; pending: PendingState }> {
  const r = await fetch(`/api/sessions/${sid}`)
  if (!r.ok) throw new Error('拉取会话历史失败')
  const body = await r.json()
  return { messages: body.messages, pending: body.pending ?? null }
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

// SSE 读流(streamChat / resumeChat 共用):按空行切事件,逐个 parse 回调
async function readSSE(resp: Response, onEvent: (e: ChatEvent) => void): Promise<void> {
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
  await readSSE(resp, onEvent)
}

// ---- 审批恢复:批准/拒绝一个待审批工具调用,续跑循环(同样返回 SSE 流) ----
export async function resumeChat(
  sessionId: string,
  toolCallId: string,
  decision: 'approve' | 'reject',
  onEvent: (e: ChatEvent) => void,
): Promise<void> {
  const resp = await fetch('/api/chat/resume', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, tool_call_id: toolCallId, decision }),
  })
  await readSSE(resp, onEvent)
}

// ---- 知识库(P3) ----
export type KbDoc = { source: string; chunks: number }
export type KbStats = { documents: number; chunks: number; dimension: number }

export async function uploadKb(file: File): Promise<KbDoc> {
  const form = new FormData()
  form.append('file', file)
  const r = await fetch('/api/kb/upload', { method: 'POST', body: form })
  if (!r.ok) {
    const detail = await r.json().catch(() => ({}))
    throw new Error(detail.detail || '上传失败')
  }
  return r.json()
}

export async function listKb(): Promise<KbDoc[]> {
  const r = await fetch('/api/kb/list')
  if (!r.ok) throw new Error('拉取知识库列表失败')
  return r.json()
}

export async function deleteKb(source: string): Promise<void> {
  const r = await fetch(`/api/kb/${encodeURIComponent(source)}`, { method: 'DELETE' })
  if (!r.ok) throw new Error('删除失败')
}

export async function rebuildKb(): Promise<{ documents: number; chunks: number }> {
  const r = await fetch('/api/kb/rebuild', { method: 'POST' })
  if (!r.ok) throw new Error('重建失败')
  return r.json()
}

export async function kbStats(): Promise<KbStats> {
  const r = await fetch('/api/kb/stats')
  if (!r.ok) throw new Error('拉取状态失败')
  return r.json()
}

// ---- 设置(P4;api_key 为脱敏值,回传不改则后端丢弃) ----
export type AppConfig = {
  llm: { base_url: string; api_key: string; model: string }
  embedding: { base_url: string; api_key: string; model: string }
  security: {
    default_cwd: string
    allowed_dirs: string[]
    kb_dir: string
    cmd_whitelist: string[]
    cmd_blacklist: string[]
  }
  agent: { max_iters: number; temperature: number }
}

export type ConfigUpdate = {
  llm?: Partial<AppConfig['llm']>
  embedding?: Partial<AppConfig['embedding']>
  security?: Partial<AppConfig['security']>
  agent?: Partial<AppConfig['agent']>
}

export async function getSettings(): Promise<AppConfig> {
  const r = await fetch('/api/settings')
  if (!r.ok) throw new Error('拉取设置失败')
  return r.json()
}

export async function updateSettings(partial: ConfigUpdate): Promise<AppConfig> {
  const r = await fetch('/api/settings', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(partial),
  })
  if (!r.ok) throw new Error('保存设置失败')
  return r.json()
}

export async function testConnection(
  kind: 'llm' | 'embedding',
  body: { base_url: string; api_key: string; model: string },
): Promise<{ ok: boolean; error: string }> {
  const r = await fetch('/api/settings/test', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...body, kind }),
  })
  return r.json()
}
