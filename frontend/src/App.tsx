import { useState } from 'react'

import KbManager from './components/KbManager'
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
    hasPending,
    send,
    approve,
    newSession,
    switchSession,
    removeSession,
    rename,
  } = useChatStream()
  const [input, setInput] = useState('')
  const [view, setView] = useState<'chat' | 'kb'>('chat') // 右侧主区:聊天 or 知识库页
  const locked = streaming || hasPending // 流式中 or 有待审批 → 锁输入

  const onSend = () => {
    const text = input.trim()
    if (!text || locked) return
    setInput('')
    void send(text)
  }

  return (
    <div className="layout">
      <SessionList
        sessions={sessions}
        currentId={currentId}
        onNew={() => {
          setView('chat')
          newSession()
        }}
        onSwitch={(id) => {
          setView('chat')
          switchSession(id)
        }}
        onDelete={removeSession}
        onRename={rename}
        onOpenKb={() => setView('kb')}
      />
      <div className="app">
        {view === 'kb' ? (
          <KbManager />
        ) : (
          <>
            <h1>Superstar</h1>
            <div className="messages">
              {messages.map((it, i) =>
                it.kind === 'tool' ? (
                  <ToolCallCard
                    key={i}
                    name={it.name}
                    args={it.args}
                    result={it.result}
                    approval={it.approval}
                    onDecision={(d) => approve(it.id, d)}
                  />
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
                disabled={locked}
                placeholder={hasPending ? '请先处理待批准的操作…' : '说点什么…'}
              />
              <button onClick={onSend} disabled={locked}>
                发送
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
