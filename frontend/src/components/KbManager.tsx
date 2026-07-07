import { FileText, RotateCw, Trash2, Upload } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import { cn } from '@/lib/utils'

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

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setDragActive(false)
    if (busy) return
    const f = e.dataTransfer.files?.[0]
    if (f) void onUpload(f)
  }

  return (
    <div className="w-full px-10 py-8">
      {/* 顶部:标题 + 副文案 + 重建(液态 ghost) */}
      <div className="mb-6 flex items-end gap-4">
        <h2 className="text-2xl font-bold tracking-tight">知识库</h2>
        <div className="mb-1 text-sm text-muted-foreground">
          拖入文档,Agent 就能检索并带来源回答
        </div>
        <button
          onClick={onRebuild}
          disabled={busy}
          className="shadow-soft-sm hover:shadow-soft-md ml-auto inline-flex items-center gap-2 rounded-full bg-card px-4 py-2 text-[13px] font-semibold transition-shadow disabled:opacity-50"
        >
          <RotateCw className="h-4 w-4" />
          重建索引
        </button>
      </div>

      {/* 读数卡:渐变大数字(呼应仪器招牌,但用液态圆角浮起) */}
      <div className="mb-5 grid grid-cols-3 gap-3.5">
        <Gauge num={stats?.documents ?? 0} cap="Documents" />
        <Gauge num={stats?.chunks ?? 0} cap="Chunks" />
        <Gauge num={stats?.dimension ?? 0} cap="Dimension" />
      </div>

      {/* 大上传区(无流光,悬停浮起) */}
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
        className={cn(
          'flex cursor-pointer flex-col items-center gap-3 rounded-[20px] bg-card p-11 text-center transition-shadow',
          dragActive ? 'shadow-soft-lg ring-2 ring-primary/40' : 'shadow-soft-md hover:shadow-soft-lg',
          busy && 'pointer-events-none opacity-60',
        )}
      >
        <span className="grad-brand shadow-soft-lg flex h-[60px] w-[60px] items-center justify-center rounded-[18px]">
          <Upload className="h-7 w-7 text-white" />
        </span>
        <div className="text-base font-semibold">拖拽文件到此,或点击选择</div>
        <div className="font-mono text-[11px] text-muted-foreground">
          .md .txt .pdf 及常见代码文件
        </div>
        {busy && (
          <div className="mt-1 h-1.5 w-3/5 overflow-hidden rounded-full bg-secondary">
            <div
              className="grad-brand h-full rounded-full transition-[width] duration-200"
              style={{ width: `${progress}%` }}
            />
          </div>
        )}
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
      </div>

      {error && (
        <div className="mt-4 rounded-xl bg-destructive/10 px-4 py-3 text-sm text-destructive">
          ⚠️ {error}
        </div>
      )}

      {/* 宽行文档列表 */}
      <div className="mb-3 mt-7 text-xs font-semibold tracking-wide text-muted-foreground">
        已入库文档
      </div>
      {docs.length === 0 ? (
        <div className="flex flex-col items-center gap-2 py-14 text-muted-foreground">
          <FileText className="h-10 w-10 opacity-30" />
          <div className="text-sm">还没有文档,上传一篇试试。</div>
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          {docs.map((d) => (
            <div
              key={d.source}
              className="shadow-soft-sm hover:shadow-soft-md flex items-center gap-4 rounded-2xl bg-card px-5 py-4 transition-shadow"
            >
              <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-[13px] bg-primary/10">
                <FileText className="h-5 w-5 text-primary" />
              </span>
              <div className="min-w-0 flex-1">
                <div className="text-[15px] font-semibold">{d.source}</div>
                <div className="font-mono text-[11.5px] text-muted-foreground">{d.source}</div>
              </div>
              <span className="grad-brand shadow-soft-sm rounded-full px-3.5 py-1.5 font-mono text-[13px] font-semibold text-white">
                {d.chunks} blocks
              </span>
              <button
                disabled={busy}
                title="删除"
                onClick={async () => {
                  await deleteKb(d.source)
                  void refresh()
                }}
                className="flex h-9 w-9 items-center justify-center rounded-xl text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive disabled:opacity-50"
              >
                <Trash2 className="h-[18px] w-[18px]" />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// 读数卡:渐变大数字 + 等宽小标签
function Gauge({ num, cap }: { num: number; cap: string }) {
  return (
    <div className="shadow-soft-md rounded-[18px] bg-card px-[22px] py-5">
      <div className="grad-text font-mono text-[34px] font-semibold leading-none">{num}</div>
      <div className="mt-2.5 font-mono text-[10.5px] uppercase tracking-wide text-muted-foreground">
        {cap}
      </div>
    </div>
  )
}
