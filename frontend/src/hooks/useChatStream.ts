import { useState, useCallback } from 'react'

import { streamChat, type ChatEvent } from '../lib/api'

export type Message = { role: 'user' | 'assistant'; content: string }

export function useChatStream() {
  const [messages, setMessages] = useState<Message[]>([])
  const [streaming, setStreaming] = useState(false)

  const send = useCallback(async (text: string) => {
    // 先塞入用户消息 + 一个空的 assistant 占位,后续把 token 往占位里追加
    setMessages((m) => [
      ...m,
      { role: 'user', content: text },
      { role: 'assistant', content: '' },
    ])
    setStreaming(true)
    try {
      await streamChat(text, (e: ChatEvent) => {
        if (e.type === 'text') {
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
      })
    } finally {
      setStreaming(false)
    }
  }, [])

  return { messages, streaming, send }
}
