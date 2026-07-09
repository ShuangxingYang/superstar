import { useCallback, useEffect, useState } from 'react'

import {
  deleteSession,
  getSession,
  listSessions,
  renameSession,
  resumeChat,
  streamChat,
  type ApprovalPreview,
  type ChatEvent,
  type PendingState,
  type SessionMeta,
  type StoredMessage,
} from '../lib/api'

// 消息流的一项:要么一条文本消息,要么一张工具卡片(可含审批子状态)
export type ChatItem =
  | { kind: 'msg'; role: 'user' | 'assistant'; content: string; reasoning?: string }
  | {
      kind: 'tool'
      id: string
      name: string
      args: string
      result?: string
      approval?: { preview: ApprovalPreview; status: 'pending' | 'approved' | 'rejected' }
    }

// 历史回放:后端原始消息 → ChatItem[](assistant 的 tool_calls → 卡片,role:tool 按 id 回填结果);
// pending 里未决的 tool_call 还没有结果,标成「待审批」卡片
function messagesToItems(msgs: StoredMessage[], pending: PendingState): ChatItem[] {
  const items: ChatItem[] = []
  const toolIndex: Record<string, number> = {} // tool_call_id -> items 下标
  for (const m of msgs) {
    if (m.role === 'user') {
      items.push({ kind: 'msg', role: 'user', content: m.content ?? '' })
    } else if (m.role === 'assistant') {
      // 有正文或有思考过程,就还原一条 assistant 气泡(思考挂在 reasoning 上,刷新可回看)
      if (m.content || m.reasoning) {
        items.push({ kind: 'msg', role: 'assistant', content: m.content ?? '', reasoning: m.reasoning })
      }
      for (const tc of m.tool_calls ?? []) {
        toolIndex[tc.id] = items.length
        items.push({ kind: 'tool', id: tc.id, name: tc.function.name, args: tc.function.arguments })
      }
    } else if (m.role === 'tool' && m.tool_call_id != null) {
      const idx = toolIndex[m.tool_call_id]
      const item = idx != null ? items[idx] : undefined
      if (item && item.kind === 'tool') item.result = m.content ?? ''
    }
  }
  // 还原「待审批」卡片:pending 里的 tool_call 还没有结果,挂上 approval 预览
  for (const tc of pending?.tool_calls ?? []) {
    const idx = toolIndex[tc.id]
    const item = idx != null ? items[idx] : undefined
    if (item && item.kind === 'tool') {
      item.approval = { preview: pending!.previews[tc.id], status: 'pending' }
    }
  }
  return items
}

export function useChatStream() {
  const [messages, setMessages] = useState<ChatItem[]>([])
  const [sessions, setSessions] = useState<SessionMeta[]>([])
  const [currentId, setCurrentId] = useState<string | null>(null)
  const [streaming, setStreaming] = useState(false)

  // 首次加载会话列表
  useEffect(() => {
    void listSessions().then(setSessions).catch(() => {})
  }, [])

  const refreshSessions = useCallback(async () => {
    setSessions(await listSessions())
  }, [])

  const newSession = useCallback(() => {
    // 懒创建:纯前端清空,不调后端;首句发出时后端才落盘
    setCurrentId(null)
    setMessages([])
  }, [])

  const switchSession = useCallback(async (sid: string) => {
    setCurrentId(sid)
    const { messages: msgs, pending } = await getSession(sid)
    setMessages(messagesToItems(msgs, pending))
  }, [])

  const removeSession = useCallback(
    async (sid: string) => {
      await deleteSession(sid)
      if (sid === currentId) {
        setCurrentId(null)
        setMessages([])
      }
      await refreshSessions()
    },
    [currentId, refreshSessions],
  )

  const rename = useCallback(
    async (sid: string, title: string) => {
      await renameSession(sid, title)
      await refreshSessions()
    },
    [refreshSessions],
  )

  // 共享事件处理:send / approve 续流都走它
  const onEvent = useCallback((e: ChatEvent) => {
    if (e.type === 'session') {
      setCurrentId(e.session_id)
    } else if (e.type === 'approval_required') {
      // 待审批 → 插一张 pending 卡(带预览,等用户拍板)
      setMessages((m) => [
        ...m,
        { kind: 'tool', id: e.id, name: e.name, args: e.args,
          approval: { preview: e.preview, status: 'pending' } },
      ])
    } else if (e.type === 'tool_call') {
      setMessages((m) => [...m, { kind: 'tool', id: e.id, name: e.name, args: e.args }])
    } else if (e.type === 'tool_result') {
      setMessages((m) =>
        m.map((it) => (it.kind === 'tool' && it.id === e.id ? { ...it, result: e.result } : it)),
      )
    } else if (e.type === 'reasoning') {
      // 思考分片:并到当前 assistant 气泡的 reasoning 字段。它先于正文到,
      // 若还没有 assistant 气泡就新起一条(content 先留空,正文来了再填)。
      setMessages((m) => {
        const next = [...m]
        const last = next[next.length - 1]
        if (last && last.kind === 'msg' && last.role === 'assistant') {
          next[next.length - 1] = { ...last, reasoning: (last.reasoning ?? '') + e.content }
        } else {
          next.push({ kind: 'msg', role: 'assistant', content: '', reasoning: e.content })
        }
        return next
      })
    } else if (e.type === 'text') {
      // 追加到「最后一条 assistant 文本」;若上一项是工具卡片/用户消息,则新起一条
      setMessages((m) => {
        const next = [...m]
        const last = next[next.length - 1]
        if (last && last.kind === 'msg' && last.role === 'assistant') {
          next[next.length - 1] = { ...last, content: last.content + e.content }
        } else {
          next.push({ kind: 'msg', role: 'assistant', content: e.content })
        }
        return next
      })
    } else if (e.type === 'error') {
      setMessages((m) => [...m, { kind: 'msg', role: 'assistant', content: `⚠️ ${e.message}` }])
    }
  }, [])

  const send = useCallback(
    async (text: string) => {
      setMessages((m) => [...m, { kind: 'msg', role: 'user', content: text }])
      setStreaming(true)
      try {
        await streamChat(text, onEvent, currentId ?? undefined)
      } finally {
        setStreaming(false)
        void refreshSessions() // 拉最新标题/时间(新会话首句后列表要更新)
      }
    },
    [currentId, onEvent, refreshSessions],
  )

  // 审批:批准/拒绝一个待审批卡片,续跑循环
  const approve = useCallback(
    async (toolCallId: string, decision: 'approve' | 'reject') => {
      if (!currentId) return
      setMessages((m) =>
        m.map((it) =>
          it.kind === 'tool' && it.id === toolCallId && it.approval
            ? { ...it, approval: { ...it.approval, status: decision === 'approve' ? 'approved' : 'rejected' } }
            : it,
        ),
      )
      setStreaming(true)
      try {
        await resumeChat(currentId, toolCallId, decision, onEvent)
      } finally {
        setStreaming(false)
        void refreshSessions()
      }
    },
    [currentId, onEvent, refreshSessions],
  )

  // 有待审批卡片时锁输入(必须先处理审批)
  const hasPending = messages.some((it) => it.kind === 'tool' && it.approval?.status === 'pending')

  return {
    messages,
    sessions,
    currentId,
    streaming,
    hasPending,
    send,
    approve,
    newSession,
    switchSession,
    removeSession,
    rename,
  }
}
