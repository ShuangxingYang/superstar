import { useState } from 'react'

import type { SessionMeta } from '../lib/api'

type Props = {
  sessions: SessionMeta[]
  currentId: string | null
  onNew: () => void
  onSwitch: (sid: string) => void
  onDelete: (sid: string) => void
  onRename: (sid: string, title: string) => void
  onOpenKb: () => void
}

export default function SessionList({
  sessions,
  currentId,
  onNew,
  onSwitch,
  onDelete,
  onRename,
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
    <aside className="sidebar">
      <button className="new-btn" onClick={onNew}>
        + 新建会话
      </button>
      <button className="new-btn" onClick={onOpenKb}>
        📚 知识库
      </button>
      <ul className="session-list">
        {sessions.map((s) => (
          <li
            key={s.id}
            className={`session-item ${s.id === currentId ? 'active' : ''}`}
            onClick={() => onSwitch(s.id)}
          >
            {editingId === s.id ? (
              <input
                autoFocus
                value={draft}
                onClick={(e) => e.stopPropagation()}
                onChange={(e) => setDraft(e.target.value)}
                onBlur={commitEdit}
                onKeyDown={(e) => e.key === 'Enter' && commitEdit()}
              />
            ) : (
              <>
                <span className="title">{s.title || '(未命名)'}</span>
                {/* stopPropagation:点按钮不要冒泡到 li 的切换会话 */}
                <span className="actions" onClick={(e) => e.stopPropagation()}>
                  <button title="重命名" onClick={() => startEdit(s)}>
                    ✎
                  </button>
                  <button
                    title="删除"
                    onClick={() => {
                      if (confirm('删除这个会话?')) onDelete(s.id)
                    }}
                  >
                    🗑
                  </button>
                </span>
              </>
            )}
          </li>
        ))}
      </ul>
    </aside>
  )
}
