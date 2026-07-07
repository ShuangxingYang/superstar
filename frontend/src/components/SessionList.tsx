import { BookOpen, MessageSquare, Pencil, Plus, Settings, Trash2 } from 'lucide-react'
import { useState } from 'react'
import type { ReactNode } from 'react'

import { Button } from '@/components/ui/button'
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
      {/* 最左:细图标导航条 */}
      <nav className="flex w-14 shrink-0 flex-col items-center gap-1 border-r bg-muted/30 py-3">
        <NavIcon label="会话" active={activeView === 'chat'} onClick={onOpenChat}>
          <MessageSquare className="h-5 w-5" />
        </NavIcon>
        <NavIcon label="知识库" active={activeView === 'kb'} onClick={onOpenKb}>
          <BookOpen className="h-5 w-5" />
        </NavIcon>
        <NavIcon label="设置(敬请期待)" active={false} disabled onClick={() => {}}>
          <Settings className="h-5 w-5" />
        </NavIcon>
      </nav>

      {/* 右侧:会话区 */}
      <aside className="flex w-56 shrink-0 flex-col border-r bg-background">
        <div className="p-3">
          <Button variant="outline" className="w-full justify-start gap-2" onClick={onNew}>
            <Plus className="h-4 w-4" />
            新建会话
          </Button>
        </div>
        <div className="px-3 pb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">
          会话历史
        </div>
        <ul className="flex-1 space-y-0.5 overflow-y-auto px-2 pb-3">
          {sessions.length === 0 && (
            <li className="px-2 py-4 text-center text-xs text-muted-foreground">还没有会话</li>
          )}
          {sessions.map((s) => (
            <li
              key={s.id}
              className={cn(
                'group flex items-center gap-1 rounded-md px-2 py-1.5 text-sm cursor-pointer',
                s.id === currentId && activeView === 'chat'
                  ? 'bg-accent text-accent-foreground'
                  : 'hover:bg-accent/50',
              )}
              onClick={() => onSwitch(s.id)}
            >
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
                      className="rounded p-1 text-muted-foreground hover:text-foreground"
                      onClick={() => startEdit(s)}
                    >
                      <Pencil className="h-3.5 w-3.5" />
                    </button>
                    <button
                      title="删除"
                      className="rounded p-1 text-muted-foreground hover:text-destructive"
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
          ))}
        </ul>
      </aside>
    </div>
  )
}

// 图标导航按钮:竖排,当前视图高亮
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
        'flex h-10 w-10 items-center justify-center rounded-lg transition-colors',
        active
          ? 'bg-primary text-primary-foreground'
          : 'text-muted-foreground hover:bg-accent hover:text-foreground',
        disabled && 'opacity-40 cursor-not-allowed hover:bg-transparent',
      )}
    >
      {children}
    </button>
  )
}
