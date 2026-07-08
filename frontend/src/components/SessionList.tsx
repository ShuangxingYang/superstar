import { BookOpen, MessageSquare, Pencil, Plus, Settings, Trash2 } from 'lucide-react'
import { useState } from 'react'
import type { ReactNode } from 'react'

import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'

import type { SessionMeta } from '../lib/api'

type View = 'chat' | 'kb'

type Props = {
  sessions: SessionMeta[]
  currentId: string | null
  activeView: View
  onNew: () => void
  onSwitch: (sid: string) => void
  onDelete: (sid: string) => void
  onRename: (sid: string, title: string) => void
  onOpenChat: () => void
  onOpenKb: () => void
}

export default function SessionList({
  sessions,
  currentId,
  activeView,
  onNew,
  onSwitch,
  onDelete,
  onRename,
  onOpenChat,
  onOpenKb,
}: Props) {
  // 就地编辑:editingId 标记正在改哪条,draft 是输入框草稿
  const [editingId, setEditingId] = useState<string | null>(null)
  const [draft, setDraft] = useState('')

  const startEdit = (s: SessionMeta) => {
    setEditingId(s.id)
    setDraft(s.title)
  }
  const commitEdit = () => {
    if (editingId && draft.trim()) onRename(editingId, draft.trim())
    setEditingId(null)
  }

  return (
    <div className="flex h-screen shrink-0">
      {/* 最左:毛玻璃图标导航条 */}
      <nav className="glass flex w-[60px] shrink-0 flex-col items-center gap-1.5 border-r py-4">
        <div className="grad-brand shadow-soft-lg mb-3 flex h-9 w-9 items-center justify-center rounded-xl font-mono text-[17px] font-semibold text-white">
          S
        </div>
        <NavIcon label="会话" active={activeView === 'chat'} onClick={onOpenChat}>
          <MessageSquare className="h-5 w-5" />
        </NavIcon>
        <NavIcon label="知识库" active={activeView === 'kb'} onClick={onOpenKb}>
          <BookOpen className="h-5 w-5" />
        </NavIcon>
        <div className="flex-1" />
        <NavIcon label="设置(敬请期待)" active={false} disabled onClick={() => {}}>
          <Settings className="h-5 w-5" />
        </NavIcon>
      </nav>

      {/* 右侧:毛玻璃会话区 */}
      <aside className="glass flex w-60 shrink-0 flex-col border-r">
        <div className="p-3.5">
          <button
            onClick={onNew}
            className="sheen grad-brand shadow-soft-md flex w-full items-center justify-center gap-2 rounded-full px-4 py-2.5 text-sm font-semibold text-white transition-[filter] hover:brightness-105"
          >
            <Plus className="h-4 w-4" strokeWidth={2.5} />
            新建会话
          </button>
        </div>
        <div className="px-4 pb-1 text-xs font-semibold tracking-wide text-muted-foreground">
          会话历史
        </div>
        <ul className="flex flex-1 flex-col gap-0.5 overflow-y-auto px-2.5 pb-3.5">
          {sessions.length === 0 && (
            <li className="px-2 py-4 text-center text-xs text-muted-foreground">还没有会话</li>
          )}
          {sessions.map((s) => {
            const active = s.id === currentId && activeView === 'chat'
            return (
              <li
                key={s.id}
                className={cn(
                  // 固定 min-h,让行高不随 hover 出现的操作按钮/active 圆点变化(否则会抖)
                  'group flex min-h-9 items-center gap-2 rounded-xl px-3 text-sm cursor-pointer transition-[background,box-shadow,color]',
                  active
                    ? 'shadow-soft-md bg-card font-medium text-foreground'
                    : 'text-muted-foreground hover:shadow-soft-sm hover:bg-card hover:text-foreground',
                )}
                onClick={() => onSwitch(s.id)}
              >
                {active && (
                  <span className="grad-brand h-[7px] w-[7px] shrink-0 rounded-full shadow-[0_0_8px_rgba(91,91,240,.5)]" />
                )}
                {editingId === s.id ? (
                  <Input
                    autoFocus
                    className="h-7"
                    value={draft}
                    onClick={(e) => e.stopPropagation()}
                    onChange={(e) => setDraft(e.target.value)}
                    onBlur={commitEdit}
                    onKeyDown={(e) => e.key === 'Enter' && commitEdit()}
                  />
                ) : (
                  <>
                    <span className="flex-1 truncate">{s.title || '(未命名)'}</span>
                    {/* stopPropagation:点按钮不要冒泡到切换会话 */}
                    <span
                      className="hidden shrink-0 items-center gap-0.5 group-hover:flex"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <button
                        title="重命名"
                        className="rounded-md p-1 text-muted-foreground hover:text-foreground"
                        onClick={() => startEdit(s)}
                      >
                        <Pencil className="h-3.5 w-3.5" />
                      </button>
                      <button
                        title="删除"
                        className="rounded-md p-1 text-muted-foreground hover:text-destructive"
                        onClick={() => {
                          if (confirm('删除这个会话?')) onDelete(s.id)
                        }}
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </span>
                  </>
                )}
              </li>
            )
          })}
        </ul>
      </aside>
    </div>
  )
}

// 图标导航按钮:竖排,当前视图渐变高亮
function NavIcon({
  children,
  label,
  active,
  disabled,
  onClick,
}: {
  children: ReactNode
  label: string
  active: boolean
  disabled?: boolean
  onClick: () => void
}) {
  return (
    <button
      title={label}
      disabled={disabled}
      onClick={onClick}
      className={cn(
        'flex h-[42px] w-[42px] items-center justify-center rounded-[14px] transition-all',
        active
          ? 'grad-brand shadow-soft-lg text-white'
          : 'text-muted-foreground hover:shadow-soft-md hover:bg-card hover:text-foreground',
        disabled && 'opacity-40 cursor-not-allowed hover:bg-transparent hover:shadow-none',
      )}
    >
      {children}
    </button>
  )
}
