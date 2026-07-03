// 与后端 chat.py 的事件协议对齐
export type ChatEvent =
  | { type: 'text'; content: string }
  | { type: 'done' }
  | { type: 'error'; message: string }

// fetch + ReadableStream 读 SSE:按空行切事件、剥掉 data: 前缀、JSON.parse
export async function streamChat(
  message: string,
  onEvent: (e: ChatEvent) => void,
): Promise<void> {
  const resp = await fetch('/api/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message }),
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
