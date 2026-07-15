import { Brain, ChevronDown, ChevronRight, Send } from 'lucide-react'
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
                      key={`${currentId ?? 'new'}-${i}`}
                      name={it.name}
                      args={it.args}
                      result={it.result}
                      approval={it.approval}
                      onDecision={(d) => approve(it.id, d)}
                    />
                  ) : (
                    // key 带上 currentId:切会话时 key 变→强制重新挂载,
                    // 让 ReasoningBlock 的 useState(live) 按新会话的 live 重取初始态
                    // (否则 index 复用组件实例,旧的展开态会被带到新会话)
                    <MessageBubble
                      key={`${currentId ?? 'new'}-${i}`}
                      role={it.role}
                      content={it.content}
                      reasoning={it.reasoning}
                      streaming={streaming && i === messages.length - 1 && it.role === 'assistant'}
                    />
                  ),
                )}
                {/* 「正在思考中…」占位:发送后到 assistant 首个内容(思考/正文/工具)到达之间的空窗,
                    末项还是 user 消息 → 显示占位,缓解「按钮置灰像卡住」的等待焦虑。
                    一旦有 assistant 内容进来,末项变 assistant,占位自动消失(交给真实气泡/思考块)。
                    纯 UI 层,不往 messages 塞假数据,不影响落盘/回放。 */}
                {(() => {
                  const last = messages[messages.length - 1]
                  const waiting = streaming && last?.kind === 'msg' && last.role === 'user'
                  return waiting ? (
                    <div className="flex items-center gap-2 text-sm text-muted-foreground">
                      <Brain className="h-4 w-4 animate-pulse" />
                      <span className="animate-pulse">正在思考中…</span>
                    </div>
                  ) : null
                })()}
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
// assistant 若有思考过程,气泡上方叠一个可折叠的「思考过程」块
function MessageBubble({
  role,
  content,
  reasoning,
  streaming,
}: {
  role: 'user' | 'assistant'
  content: string
  reasoning?: string
  streaming: boolean // = 本条正在流式(最后一条 assistant + 全局 streaming)
}) {
  const isUser = role === 'user'
  // 气泡只看正文:有正文才显,空正文永不显气泡(思考阶段无正文交给思考块占位)。
  // 关键:正文被 tool_call 打断后会另起新气泡,这条只承载 reasoning 的气泡 content 恒空。
  // 早先写成 `!!content || !thinking`,回答结束(streaming=false)时会把这种空气泡露出来,
  // 表现为思考块与工具卡片之间多一个空白气泡——改回纯 `!!content` 修掉。
  // reasoning 由下方独立的 ReasoningBlock 渲染,不受此判断影响。
  const showBubble = !!content
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
      {/* items-start:思考块与气泡各自量自身宽度,谁也不撑谁(展开思考不再影响气泡宽度) */}
      <div className="flex max-w-[78%] flex-col items-start">
        {!isUser && reasoning && <ReasoningBlock text={reasoning} live={streaming} />}
        {showBubble && (
          <div
            className={cn(
              'w-fit whitespace-pre-wrap px-4 py-3 text-sm leading-relaxed',
              isUser
                ? 'grad-brand shadow-soft-lg rounded-[18px_4px_18px_18px] text-white'
                : 'shadow-soft-md rounded-[4px_18px_18px_18px] bg-card',
            )}
          >
            {content}
            {streaming && content ? ' ▋' : ''}
          </div>
        )}
      </div>
    </div>
  )
}

// 思考过程块:可折叠。展开态规则——
//   · 本轮活跃(live,新对话流式中):默认展开,让你看着它想;
//   · 历史消息(live=false,切会话/刷新回放):默认折叠,不占地方;
//   · 本轮输出全部结束(live 由 true→false)后自动折叠一次——除非你手动动过它。
// 宽度:自身 w-fit + max-w 限宽,不撑父容器,故展开/折叠不影响正文气泡宽度。
function ReasoningBlock({ text, live }: { text: string; live: boolean }) {
  const [open, setOpen] = useState(live) // 初始态即区分新对话(展开)/历史(折叠)
  const touchedRef = useRef(false) // 用户是否手动点过——点过就不再自动折叠,尊重用户
  const prevLive = useRef(live)
  // live 由 true→false = 本次思考+回答刚结束 → 自动折叠(用户没手动干预过时)
  useEffect(() => {
    if (prevLive.current && !live && !touchedRef.current) setOpen(false)
    prevLive.current = live
  }, [live])

  const thinking = live && !text.trim() // 活跃且思考文字还没来 = 正在思考
  // 网关(gpt-5 系经 tokenhub)常在 reasoning_content 里夹 markdown 注释残片 <!-- --> 和
  // ** 加粗符,纯文本块里显示是噪音。这里清掉:去空注释、剥 **、并压掉多余空行。
  const clean = text
    .replace(/<!--[\s\S]*?-->/g, '') // 去掉 HTML 注释(含空的 <!-- -->)
    .replace(/\*\*/g, '')            // 去掉加粗标记
    .replace(/\n{3,}/g, '\n\n')      // 连续空行压成一个
    .trim()
  // 思考中即便暂时没实质内容也保留块(显脉冲告诉用户在想);思考完却空了则不显,免留空壳
  if (!clean && !thinking && !live) return null
  return (
    <div className="shadow-soft-sm mb-2 w-fit max-w-full overflow-hidden rounded-2xl bg-secondary/50">
      <button
        onClick={() => {
          touchedRef.current = true
          setOpen((v) => !v)
        }}
        className="flex w-full items-center gap-2 px-3.5 py-2 text-xs font-semibold text-muted-foreground transition-colors hover:text-foreground"
      >
        <Brain className={cn('h-3.5 w-3.5', thinking && 'animate-pulse text-primary')} />
        {thinking ? '思考中…' : '思考过程'}
        {open ? (
          <ChevronDown className="ml-auto h-3.5 w-3.5" />
        ) : (
          <ChevronRight className="ml-auto h-3.5 w-3.5" />
        )}
      </button>
      {open && (clean || thinking) && (
        <div className="max-h-56 overflow-y-auto whitespace-pre-wrap px-3.5 pb-3 font-mono text-xs leading-relaxed text-muted-foreground/90">
          {clean || (thinking ? '(模型正在思考,此网关暂不透传完整推理过程)' : '')}
          {thinking ? ' ▋' : ''}
        </div>
      )}
    </div>
  )
}
