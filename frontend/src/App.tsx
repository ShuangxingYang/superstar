import { Send } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'

import { getSettings } from './lib/api'

import ContextPanel from './components/ContextPanel'
import KbManager from './components/KbManager'
import SessionList from './components/SessionList'
import SettingsPanel from './components/SettingsPanel'
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
  const [view, setView] = useState<'chat' | 'kb' | 'settings'>('chat') // 右侧主区:聊天 / 知识库 / 设置
  const [needSetup, setNeedSetup] = useState(false) // 首启未配 LLM → 空态引导进设置页
  const locked = streaming || hasPending // 流式中 or 有待审批 → 锁输入

  // 挂载时探一次 LLM 配置:api_key/model 任一为空(脱敏后仍为空串)→ 还没配
  useEffect(() => {
    getSettings()
      .then((c) => setNeedSetup(!c.llm.api_key || !c.llm.model))
      .catch(() => {})
  }, [])

  // 消息流自动跟随:仅当用户本来就贴在底部时,新消息才自动滚到底;
  // 用户往上翻看历史时不硬拽回去(atBottom 在每次滚动时更新)。
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

  const currentTitle = sessions.find((s) => s.id === currentId)?.title

  return (
    <div className="flex h-screen overflow-hidden">
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
        onOpenSettings={() => setView('settings')}
      />

      <main className="flex min-w-0 flex-1 flex-col">
        {view === 'kb' ? (
          <div className="flex-1 overflow-y-auto">
            <KbManager />
          </div>
        ) : view === 'settings' ? (
          <div className="flex-1 overflow-y-auto">
            <SettingsPanel />
          </div>
        ) : (
          <>
            {/* 毛玻璃顶栏 */}
            <div className="glass flex h-14 shrink-0 items-center gap-2.5 border-b px-6">
              <span className="grad-brand h-2 w-2 rounded-full shadow-[0_0_8px_rgba(91,91,240,.5)]" />
              <span className="font-semibold">{currentTitle || '新对话'}</span>
              <span className="font-mono text-xs text-muted-foreground">superstar · 本地 Agent</span>
            </div>

            {/* 消息流:独立滚动容器 */}
            <div ref={scrollRef} onScroll={onScroll} className="flex-1 overflow-y-auto">
              <div className="mx-auto flex w-full max-w-3xl flex-col gap-5 p-6">
                {messages.length === 0 && (
                  <div className="mt-24 text-center">
                    <div className="grad-text text-3xl font-bold tracking-tight">Superstar</div>
                    <div className="mt-3 text-sm text-muted-foreground">
                      本地干活型 Agent,问点什么开始吧。
                    </div>
                    {needSetup && (
                      <button
                        onClick={() => setView('settings')}
                        className="shadow-soft-md hover:shadow-soft-lg mt-6 inline-flex items-center gap-2 rounded-full bg-card px-5 py-2.5 text-sm font-semibold text-primary transition-shadow"
                      >
                        还没配模型,点这里去设置 →
                      </button>
                    )}
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

            {/* 液态胶囊输入区 */}
            <div className="p-4">
              <div className="shadow-soft-md mx-auto flex w-full max-w-3xl items-center gap-2 rounded-full bg-card py-1.5 pl-5 pr-1.5">
                <Input
                  className="h-9 flex-1 border-0 bg-transparent shadow-none focus-visible:ring-0"
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && onSend()}
                  disabled={locked}
                  placeholder={hasPending ? '请先处理待批准的操作…' : '说点什么…'}
                />
                <Button
                  onClick={onSend}
                  disabled={locked}
                  className="grad-brand shadow-soft-sm h-9 rounded-full px-5"
                >
                  <Send className="h-4 w-4" />
                  发送
                </Button>
              </div>
            </div>
          </>
        )}
      </main>

      {/* 右栏上下文面板:仅聊天视图显示(知识库/设置页占满主区,保持宽敞) */}
      {view === 'chat' && (
        <ContextPanel
          onOpenKb={() => setView('kb')}
          onOpenSettings={() => setView('settings')}
        />
      )}
    </div>
  )
}

// 聊天气泡:user 靠右(渐变)、assistant 靠左(白卡浮起),各带圆角头像
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
    <div className={cn('flex gap-3', isUser ? 'flex-row-reverse' : 'flex-row')}>
      <div
        className={cn(
          'flex h-8 w-8 shrink-0 items-center justify-center rounded-xl font-mono text-xs font-semibold',
          isUser ? 'grad-brand shadow-soft-lg text-white' : 'shadow-soft-sm bg-card',
        )}
      >
        {isUser ? '你' : 'S'}
      </div>
      <div
        className={cn(
          'max-w-[78%] whitespace-pre-wrap px-4 py-3 text-sm leading-relaxed',
          isUser
            ? 'grad-brand shadow-soft-lg rounded-[18px_4px_18px_18px] text-white'
            : 'shadow-soft-md rounded-[4px_18px_18px_18px] bg-card',
        )}
      >
        {content}
        {streaming ? ' ▋' : ''}
      </div>
    </div>
  )
}
