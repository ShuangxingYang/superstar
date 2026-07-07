import { useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Progress } from '@/components/ui/progress'

import { deleteKb, kbStats, listKb, rebuildKb, uploadKb } from '../lib/api'
import type { KbDoc, KbStats } from '../lib/api'

export default function KbManager() {
  const [docs, setDocs] = useState<KbDoc[]>([])
  const [stats, setStats] = useState<KbStats | null>(null)
  const [progress, setProgress] = useState(0) // 假进度条:0=空闲
  const [error, setError] = useState('')
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

  return (
    <div className="kb-manager">
      <h2>📚 知识库</h2>

      <div className="kb-upload">
        <input
          ref={fileRef}
          type="file"
          accept=".md,.txt,.pdf,.py,.js,.ts,.tsx,.json,.yaml,.yml"
          disabled={busy}
          onChange={(e) => {
            const f = e.target.files?.[0]
            if (f) void onUpload(f)
          }}
        />
        {busy && <Progress value={progress} className="mt-2" />}
      </div>

      {error && <div className="kb-error">⚠️ {error}</div>}

      <div className="kb-list">
        {docs.length === 0 && <div className="kb-empty">还没有文档,上传一篇试试。</div>}
        {docs.map((d) => (
          <Card key={d.source}>
            <CardContent className="flex items-center gap-2 p-3">
              <span className="kb-source">{d.source}</span>
              <span className="kb-chunks">{d.chunks} 块</span>
              <Button
                variant="destructive"
                size="sm"
                disabled={busy}
                onClick={async () => {
                  await deleteKb(d.source)
                  void refresh()
                }}
              >
                删除
              </Button>
            </CardContent>
          </Card>
        ))}
      </div>

      <div className="kb-footer">
        <Button variant="outline" size="sm" disabled={busy} onClick={onRebuild}>
          重建索引
        </Button>
        {stats && (
          <span className="kb-stats">
            {stats.documents} 篇 / {stats.chunks} 块 / {stats.dimension} 维
          </span>
        )}
      </div>
    </div>
  )
}
