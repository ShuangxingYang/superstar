import { FileText, RotateCw, Trash2, Upload } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Progress } from '@/components/ui/progress'

import { deleteKb, kbStats, listKb, rebuildKb, uploadKb } from '../lib/api'
import type { KbDoc, KbStats } from '../lib/api'

const ACCEPT = '.md,.txt,.pdf,.py,.js,.ts,.tsx,.json,.yaml,.yml'

export default function KbManager() {
  const [docs, setDocs] = useState<KbDoc[]>([])
  const [stats, setStats] = useState<KbStats | null>(null)
  const [progress, setProgress] = useState(0) // 假进度条:0=空闲
  const [error, setError] = useState('')
  const [dragActive, setDragActive] = useState(false) // 拖拽悬停高亮
  const fileRef = useRef<HTMLInputElement>(null)
  const timer = useRef<ReturnType<typeof setInterval> | null>(null)

  const refresh = async () => {
    try {
      setDocs(await listKb())
      setStats(await kbStats())
      setError('')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }
  useEffect(() => {
    void refresh()
  }, [])

  // 假进度条:匀速爬到 90%,请求回来跳 100% 再归零。
  // 真流式灌库进度与 chat SSE 是同一知识点,边际收益低,这里用纯前端假进度。
  const startFakeProgress = () => {
    setProgress(8)
    timer.current = setInterval(() => {
      setProgress((p) => (p < 90 ? p + Math.max(1, (90 - p) * 0.15) : p))
    }, 200)
  }
  const stopFakeProgress = () => {
    if (timer.current) clearInterval(timer.current)
    timer.current = null
    setProgress(100)
    setTimeout(() => setProgress(0), 400)
  }

  const onUpload = async (file: File) => {
    setError('')
    startFakeProgress()
    try {
      await uploadKb(file)
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      stopFakeProgress()
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  const onRebuild = async () => {
    setError('')
    startFakeProgress()
    try {
      await rebuildKb()
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      stopFakeProgress()
    }
  }

  const busy = progress > 0

  // 拖拽上传:阻止浏览器默认打开文件,取第一个文件走上传
  const onDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setDragActive(false)
    if (busy) return
    const f = e.dataTransfer.files?.[0]
    if (f) void onUpload(f)
  }

  return (
    <div className="flex h-full flex-col gap-4 p-2">
      {/* 顶部:标题 + 统计 + 重建 */}
      <div className="flex items-center gap-3">
        <h2 className="text-xl font-semibold">📚 知识库</h2>
        {stats && (
          <div className="flex items-center gap-1.5 text-muted-foreground">
            <Badge variant="secondary">{stats.documents} 篇</Badge>
            <Badge variant="secondary">{stats.chunks} 块</Badge>
            <Badge variant="secondary">{stats.dimension} 维</Badge>
          </div>
        )}
        <Button
          variant="outline"
          size="sm"
          className="ml-auto"
          disabled={busy}
          onClick={onRebuild}
        >
          <RotateCw className="h-4 w-4" />
          重建索引
        </Button>
      </div>

      {/* 拖拽上传区 */}
      <div
        role="button"
        tabIndex={0}
        onClick={() => !busy && fileRef.current?.click()}
        onKeyDown={(e) => {
          if ((e.key === 'Enter' || e.key === ' ') && !busy) fileRef.current?.click()
        }}
        onDragOver={(e) => {
          e.preventDefault()
          if (!busy) setDragActive(true)
        }}
        onDragLeave={() => setDragActive(false)}
        onDrop={onDrop}
        className={cnDrop(dragActive, busy)}
      >
        <Upload className="mb-2 h-7 w-7 text-muted-foreground" />
        <div className="text-sm font-medium">拖拽文件到此,或点击选择</div>
        <div className="mt-1 text-xs text-muted-foreground">
          支持 .md .txt .pdf 及常见代码文件
        </div>
        <input
          ref={fileRef}
          type="file"
          accept={ACCEPT}
          className="hidden"
          disabled={busy}
          onChange={(e) => {
            const f = e.target.files?.[0]
            if (f) void onUpload(f)
          }}
        />
        {busy && <Progress value={progress} className="mt-3 w-4/5" />}
      </div>

      {error && (
        <div className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          ⚠️ {error}
        </div>
      )}

      {/* 文档列表 */}
      <div className="flex flex-1 flex-col gap-2 overflow-y-auto">
        {docs.length === 0 ? (
          <div className="flex flex-1 flex-col items-center justify-center gap-2 text-muted-foreground">
            <FileText className="h-10 w-10 opacity-40" />
            <div className="text-sm">还没有文档,上传一篇试试。</div>
          </div>
        ) : (
          docs.map((d) => (
            <Card key={d.source} className="flex items-center gap-3 p-3">
              <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
              <span className="flex-1 truncate text-sm" title={d.source}>
                {d.source}
              </span>
              <Badge variant="secondary">{d.chunks} 块</Badge>
              <Button
                variant="ghost"
                size="icon"
                className="h-8 w-8 text-muted-foreground hover:text-destructive"
                disabled={busy}
                title="删除"
                onClick={async () => {
                  await deleteKb(d.source)
                  void refresh()
                }}
              >
                <Trash2 className="h-4 w-4" />
              </Button>
            </Card>
          ))
        )}
      </div>
    </div>
  )
}

// 拖拽区样式:悬停高亮 / 忙碌禁用
function cnDrop(active: boolean, busy: boolean): string {
  const base =
    'flex flex-col items-center justify-center rounded-lg border-2 border-dashed px-4 py-8 text-center transition-colors'
  if (busy) return `${base} border-border opacity-60`
  if (active) return `${base} border-primary bg-primary/5 cursor-pointer`
  return `${base} border-border hover:border-primary/50 cursor-pointer`
}
