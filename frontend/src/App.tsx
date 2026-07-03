import { useState } from 'react'

import { useChatStream } from './hooks/useChatStream'
import './App.css'

export default function App() {
  const { messages, streaming, send } = useChatStream()
  const [input, setInput] = useState('')

  const onSend = () => {
    const text = input.trim()
    if (!text || streaming) return
    setInput('')
    void send(text)
  }

  return (
    <div className="app">
      <h1>Superstar</h1>
      <div className="messages">
        {messages.map((m, i) => (
          <div key={i} className={`msg ${m.role}`}>
            <b>{m.role === 'user' ? '你' : 'AI'}:</b> {m.content}
            {streaming && i === messages.length - 1 && m.role === 'assistant' ? ' ▋' : ''}
          </div>
        ))}
      </div>
      <div className="composer">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && onSend()}
          placeholder="说点什么…"
        />
        <button onClick={onSend} disabled={streaming}>
          发送
        </button>
      </div>
    </div>
  )
}
