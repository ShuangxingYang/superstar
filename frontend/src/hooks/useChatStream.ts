import { useCallback, useEffect, useState } from 'react'

import {
  deleteSession,
  getSession,
  listSessions,
  renameSession,
  streamChat,
  type ChatEvent,
  type SessionMeta,
  type StoredMessage,
} from '../lib/api'

// 消息流的一项:要么一条文本消息,要么一张工具卡片
export type ChatItem =
  | { kind: 'msg'; role: 'user' | 'assistant'; content: string }
  | { kind: 'tool'; id: string; name: string; args: string; result?: string }

// 历史回放:把后端存的原始消息还原成 ChatItem[](assistant 的 tool_calls → 卡片,
// role:tool 消息按 tool_call_id 回填对应卡片的 result)
function messagesToItems(msgs: StoredMessage[]): ChatItem[] {
  const items: ChatItem[] = []
  const toolIndex: Record<string, number> = {} // tool_call_id -> items 下标
  for (const m of msgs) {
    if (m.role === 'user') {
      items.push({ kind: 'msg', role: 'user', content: m.content ?? '' })
    } else if (m.role === 'assistant') {
      if (m.content) items.push({ kind: 'msg', role: 'assistant', content: m.content })
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
    setMessages(messagesToItems(await getSession(sid)))
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

  const send = useCallback(
    async (text: string) => {
      setMessages((m) => [...m, { kind: 'msg', role: 'user', content: text }])
      setStreaming(true)
      try {
        await streamChat(
          text,
          (e: ChatEvent) => {
            if (e.type === 'session') {
              // 懒创建首句:后端回传新 sid,记为当前会话
              setCurrentId(e.session_id)
            } else if (e.type === 'tool_call') {
              // 新工具调用 → 插一张「运行中」卡片(result 未定义 = 运行中)
              setMessages((m) => [...m, { kind: 'tool', id: e.id, name: e.name, args: e.args }])
            } else if (e.type === 'tool_result') {
              // 同 id 卡片填结果
              setMessages((m) =>
                m.map((it) => (it.kind === 'tool' && it.id === e.id ? { ...it, result: e.result } : it)),
              )
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
          },
          currentId ?? undefined,
        )
      } finally {
        setStreaming(false)
        void refreshSessions() // 拉最新标题/时间(新会话首句后列表要更新)
      }
    },
    [currentId, refreshSessions],
  )

  return {
    messages,
    sessions,
    currentId,
    streaming,
    send,
    newSession,
    switchSession,
    removeSession,
    rename,
  }
}
