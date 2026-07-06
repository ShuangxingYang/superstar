import { useState } from 'react'

import SessionList from './components/SessionList'
import ToolCallCard from './components/ToolCallCard'
import { useChatStream } from './hooks/useChatStream'
import './App.css'

export default function App() {
  const {
    messages,
    sessions,
    currentId,
    streaming,
    send,
    newSession,
    switchSession,
    removeSession,
    rename,
  } = useChatStream()
  const [input, setInput] = useState('')

  const onSend = () => {
    const text = input.trim()
    if (!text || streaming) return
    setInput('')
    void send(text)
  }

  return (
    <div className="layout">
      <SessionList
        sessions={sessions}
        currentId={currentId}
        onNew={newSession}
        onSwitch={switchSession}
        onDelete={removeSession}
        onRename={rename}
      />
      <div className="app">
        <h1>Superstar</h1>
        <div className="messages">
          {messages.map((it, i) =>
            it.kind === 'tool' ? (
              <ToolCallCard key={i} name={it.name} args={it.args} result={it.result} />
            ) : (
              <div key={i} className={`msg ${it.role}`}>
                <b>{it.role === 'user' ? '你' : 'AI'}:</b> {it.content}
                {streaming && i === messages.length - 1 && it.role === 'assistant' ? ' ▋' : ''}
              </div>
            ),
          )}
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
    </div>
  )
}
