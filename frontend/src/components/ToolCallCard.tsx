import { Check, ChevronDown, ChevronRight, Wrench, X } from 'lucide-react'
import { useState } from 'react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { cn } from '@/lib/utils'

import type { ApprovalPreview } from '../lib/api'

type Props = {
  name: string
  args: string
  result?: string
  approval?: { preview: ApprovalPreview; status: 'pending' | 'approved' | 'rejected' }
  onDecision?: (decision: 'approve' | 'reject') => void
}

// diff 文本按行着色:+ 绿、- 红、@@ 蓝、其余默认(diff 着色是特化需求,保留 .diff-* 手写 CSS)
function DiffView({ diff }: { diff: string }) {
  return (
    <pre className="diff">
      {diff.split('\n').map((line, i) => {
        let cls = 'diff-ctx'
        if (line.startsWith('+')) cls = 'diff-add'
        else if (line.startsWith('-')) cls = 'diff-del'
        else if (line.startsWith('@@')) cls = 'diff-hunk'
        return (
          <div key={i} className={cls}>
            {line || ' '}
          </div>
        )
      })}
    </pre>
  )
}

export default function ToolCallCard({ name, args, result, approval, onDecision }: Props) {
  const pending = approval?.status === 'pending'
  const [open, setOpen] = useState(pending) // 待审批默认展开,方便直接看 diff/命令
  // args 是模型给的 JSON 字符串,尝试美化;parse 失败原样显示
  const prettyArgs = (() => {
    try {
      return JSON.stringify(JSON.parse(args), null, 2)
    } catch {
      return args
    }
  })()

  const running = result === undefined && !approval
  const summary = pending
    ? approval.preview.kind === 'write'
      ? `待批准:写 ${approval.preview.path}`
      : `待批准:运行 ${approval.preview.command}`
    : running
      ? '运行中…'
      : (result ?? '').split('\n')[0] || '(空)'

  return (
    <Card className={cn('overflow-hidden text-sm', pending && 'border-destructive/50 bg-destructive/5')}>
      {/* 头部:图标 + 工具名 + 状态徽标 + 摘要 + 折叠箭头 */}
      <button
        className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-accent/40"
        onClick={() => setOpen((o) => !o)}
      >
        <Wrench className="h-4 w-4 shrink-0 text-muted-foreground" />
        <span className="font-medium">{name}</span>
        {pending ? (
          <Badge variant="destructive">待审批</Badge>
        ) : running ? (
          <Badge variant="secondary">运行中</Badge>
        ) : (
          <Badge variant="outline">完成</Badge>
        )}
        <span className="flex-1 truncate text-muted-foreground">{summary}</span>
        {open ? (
          <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" />
        ) : (
          <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
        )}
      </button>

      {open && (
        <div className="border-t px-3 py-2">
          {pending && approval.preview.kind === 'write' && (
            <>
              <div className="mb-1 font-medium text-muted-foreground">将写入 {approval.preview.path}</div>
              <DiffView diff={approval.preview.diff} />
            </>
          )}
          {pending && approval.preview.kind === 'command' && (
            <>
              <div className="mb-1 font-medium text-muted-foreground">将执行命令 ⚠️ 灰名单</div>
              <pre className="overflow-x-auto rounded bg-muted px-2 py-1 text-xs">
                {approval.preview.command}
              </pre>
            </>
          )}
          {!pending && (
            <>
              <div className="mb-1 font-medium text-muted-foreground">参数</div>
              <pre className="overflow-x-auto rounded bg-muted px-2 py-1 text-xs">{prettyArgs}</pre>
              <div className="mb-1 mt-2 font-medium text-muted-foreground">结果</div>
              <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-all rounded bg-muted px-2 py-1 text-xs">
                {running ? '运行中…' : result}
              </pre>
            </>
          )}
          {pending && (
            <div className="mt-3 flex gap-2">
              <Button size="sm" onClick={() => onDecision?.('approve')}>
                <Check className="h-4 w-4" />
                批准
              </Button>
              <Button size="sm" variant="destructive" onClick={() => onDecision?.('reject')}>
                <X className="h-4 w-4" />
                拒绝
              </Button>
            </div>
          )}
        </div>
      )}
    </Card>
  )
}
