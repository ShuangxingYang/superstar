import { BookOpen, FolderOpen, Home, Settings } from 'lucide-react'
import { useEffect, useState } from 'react'

import { getSettings, kbStats } from '../lib/api'

// 右栏上下文面板:展示全局运行时上下文(工作目录 / 白名单 / 知识库数),不随会话变。
export default function ContextPanel({
  onOpenKb,
  onOpenSettings,
}: {
  onOpenKb: () => void
  onOpenSettings: () => void
}) {
  const [cwd, setCwd] = useState('')
  const [dirs, setDirs] = useState<string[]>([])
  const [docs, setDocs] = useState<number | null>(null)

  useEffect(() => {
    getSettings()
      .then((c) => {
        setCwd(c.security.default_cwd)
        setDirs(c.security.allowed_dirs)
      })
      .catch(() => {})
    kbStats()
      .then((s) => setDocs(s.documents))
      .catch(() => setDocs(null))
  }, [])

  return (
    <aside className="glass hidden w-64 shrink-0 flex-col gap-4 overflow-y-auto border-l p-4 lg:flex">
      <div className="font-mono text-[11px] uppercase tracking-wide text-muted-foreground">
        当前上下文
      </div>

      {/* 工作目录 */}
      <div className="shadow-soft-sm rounded-2xl bg-card p-4">
        <div className="mb-2.5 flex items-center gap-2 text-[13px] font-semibold">
          <Home className="h-4 w-4 text-primary" />
          工作目录
        </div>
        <div className="break-all font-mono text-[11.5px] text-muted-foreground">
          {cwd || '(未配置)'}
        </div>
        {dirs.length > 0 && (
          <>
            <div className="mb-1.5 mt-3 flex items-center gap-1.5 text-[11px] text-muted-foreground">
              <FolderOpen className="h-3.5 w-3.5" />
              白名单目录
            </div>
            <div className="flex flex-col gap-1">
              {dirs.map((d) => (
                <div key={d} className="break-all font-mono text-[11px] text-muted-foreground">
                  · {d}
                </div>
              ))}
            </div>
          </>
        )}
      </div>

      {/* 知识库 */}
      <button
        onClick={onOpenKb}
        className="shadow-soft-sm hover:shadow-soft-md rounded-2xl bg-card p-4 text-left transition-shadow"
      >
        <div className="mb-2 flex items-center gap-2 text-[13px] font-semibold">
          <BookOpen className="h-4 w-4 text-primary" />
          知识库
        </div>
        <div className="flex items-baseline gap-1.5">
          <span className="grad-text font-mono text-2xl font-semibold leading-none">
            {docs ?? '—'}
          </span>
          <span className="text-xs text-muted-foreground">篇文档</span>
        </div>
        <div className="mt-2 text-[11px] text-primary">管理知识库 →</div>
      </button>

      <div className="flex-1" />

      {/* 设置入口 */}
      <button
        onClick={onOpenSettings}
        className="shadow-soft-sm hover:shadow-soft-md inline-flex items-center justify-center gap-2 rounded-full bg-card px-4 py-2.5 text-[13px] font-semibold transition-shadow"
      >
        <Settings className="h-4 w-4" />
        打开设置
      </button>
    </aside>
  )
}
