import { useCallback, useEffect, useState } from 'react'

import {
  deleteSession,
  getSession,
  listSessions,
  renameSession,
  streamChat,
  type ChatEvent,
  type SessionMeta,
} from '../lib/api'

export type Message = { role: 'user' | 'assistant'; content: string }

export function useChatStream() {
  const [messages, setMessages] = useState<Message[]>([])
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
    setMessages(await getSession(sid))
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
      setMessages((m) => [...m, { role: 'user', content: text }, { role: 'assistant', content: '' }])
      setStreaming(true)
      try {
        await streamChat(
          text,
          (e: ChatEvent) => {
            if (e.type === 'session') {
              // 懒创建首句:后端回传新 sid,记为当前会话
              setCurrentId(e.session_id)
            } else if (e.type === 'text') {
              setMessages((m) => {
                const next = [...m]
                const last = next[next.length - 1]
                next[next.length - 1] = { role: 'assistant', content: last.content + e.content }
                return next
              })
            } else if (e.type === 'error') {
              setMessages((m) => {
                const next = [...m]
                next[next.length - 1] = { role: 'assistant', content: `⚠️ ${e.message}` }
                return next
              })
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
