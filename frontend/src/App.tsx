import { useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'

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

  // 消息流自动跟随:仅当用户本来就贴在底部时,新消息才自动滚到底;
  // 用户往上翻看历史时不硬拽回去(isAtBottom 在每次滚动时更新)。
  const scrollRef = useRef<HTMLDivElement>(null)
  const atBottomRef = useRef(true)
  const onScroll = () => {
    const el = scrollRef.current
    if (!el) return
    atBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80
  }
  useEffect(() => {
    if (atBottomRef.current && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [messages])

  const onSend = () => {
    const text = input.trim()
    if (!text || locked) return
    setInput('')
    atBottomRef.current = true // 自己发消息,视为要跟到底
    void send(text)
  }

  return (
    <div className="flex h-screen overflow-hidden bg-background text-foreground">
      <SessionList
        sessions={sessions}
        currentId={currentId}
        activeView={view}
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
        onOpenChat={() => setView('chat')}
        onOpenKb={() => setView('kb')}
      />

      <main className="flex min-w-0 flex-1 flex-col">
        {view === 'kb' ? (
          <div className="mx-auto w-full max-w-3xl flex-1 overflow-y-auto p-4">
            <KbManager />
          </div>
        ) : (
          <>
            {/* 消息流:独立滚动容器,只有它滚,侧栏和输入框不动 */}
            <div ref={scrollRef} onScroll={onScroll} className="flex-1 overflow-y-auto">
              <div className="mx-auto flex w-full max-w-3xl flex-col gap-4 p-4">
                {messages.length === 0 && (
                  <div className="mt-20 text-center text-muted-foreground">
                    <div className="text-2xl font-semibold">Superstar</div>
                    <div className="mt-2 text-sm">本地干活型 Agent,问点什么开始吧。</div>
                  </div>
                )}
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
                    <MessageBubble
                      key={i}
                      role={it.role}
                      content={it.content}
                      streaming={streaming && i === messages.length - 1 && it.role === 'assistant'}
                    />
                  ),
                )}
              </div>
            </div>

            {/* 输入框:常驻底部 */}
            <div className="border-t bg-background p-3">
              <div className="mx-auto flex w-full max-w-3xl gap-2">
                <Input
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && onSend()}
                  disabled={locked}
                  placeholder={hasPending ? '请先处理待批准的操作…' : '说点什么…'}
                />
                <Button onClick={onSend} disabled={locked}>
                  发送
                </Button>
              </div>
            </div>
          </>
        )}
      </main>
    </div>
  )
}

// 聊天气泡:user 靠右(primary)、assistant 靠左(muted),各带圆形头像
function MessageBubble({
  role,
  content,
  streaming,
}: {
  role: 'user' | 'assistant'
  content: string
  streaming: boolean
}) {
  const isUser = role === 'user'
  return (
    <div className={cn('flex gap-2', isUser ? 'flex-row-reverse' : 'flex-row')}>
      <div
        className={cn(
          'flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-sm',
          isUser ? 'bg-primary text-primary-foreground' : 'bg-muted',
        )}
      >
        {isUser ? '你' : '🤖'}
      </div>
      <div
        className={cn(
          'max-w-[80%] whitespace-pre-wrap rounded-lg px-3 py-2 text-sm leading-relaxed',
          isUser ? 'bg-primary text-primary-foreground' : 'bg-muted',
        )}
      >
        {content}
        {streaming ? ' ▋' : ''}
      </div>
    </div>
  )
}
