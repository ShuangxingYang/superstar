import { Check, ChevronDown, ChevronRight, Wrench, X } from 'lucide-react'
import { useState } from 'react'

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
      : approval.preview.kind === 'command'
        ? `待批准:运行 ${approval.preview.command}`
        : `待批准:加入工作区 ${approval.preview.path}`
    : running
      ? '运行中…'
      : (result ?? '').split('\n')[0] || '(空)'

  return (
    <div
      className={cn(
        // 宽度跟消息气泡对齐(max-w-[78%]),不横向铺满整行,视觉更协调
        'w-fit max-w-[78%] overflow-hidden rounded-2xl bg-card text-sm transition-shadow',
        pending ? 'shadow-[0_4px_20px_rgba(240,80,107,.18)]' : 'shadow-soft-md hover:shadow-soft-lg',
      )}
    >
      {/* 头部:图标底块 + 工具名 + 胶囊状态 + 摘要 + 折叠箭头 */}
      <button
        className="flex w-full items-center gap-2.5 px-4 py-3 text-left"
        onClick={() => setOpen((o) => !o)}
      >
        <span
          className={cn(
            'flex h-[30px] w-[30px] shrink-0 items-center justify-center rounded-[10px]',
            pending ? 'bg-destructive/10 text-destructive' : 'bg-primary/10 text-primary',
          )}
        >
          <Wrench className="h-4 w-4" />
        </span>
        <span className="font-mono text-[13px] font-semibold">{name}</span>
        {pending ? (
          <span className="grad-danger rounded-full px-2.5 py-[3px] font-mono text-[10px] font-semibold text-white">
            待审批
          </span>
        ) : running ? (
          <span className="rounded-full bg-primary/10 px-2.5 py-[3px] font-mono text-[10px] font-semibold text-primary">
            运行中
          </span>
        ) : (
          <span className="rounded-full bg-secondary px-2.5 py-[3px] font-mono text-[10px] font-semibold text-muted-foreground">
            完成
          </span>
        )}
        <span className="flex-1 truncate text-[13px] text-muted-foreground">{summary}</span>
        {open ? (
          <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" />
        ) : (
          <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
        )}
      </button>

      {open && (
        <div className="border-t px-4 py-3">
          {pending && approval.preview.kind === 'write' && (
            <>
              <div className="mb-1.5 font-mono text-[10.5px] uppercase tracking-wide text-muted-foreground">
                将写入 {approval.preview.path}
              </div>
              <DiffView diff={approval.preview.diff} />
            </>
          )}
          {pending && approval.preview.kind === 'command' && (
            <>
              <div className="mb-1.5 font-mono text-[10.5px] uppercase tracking-wide text-muted-foreground">
                将执行命令 ⚠️ 灰名单
              </div>
              <pre className="overflow-x-auto rounded-[10px] bg-background px-3 py-2.5 font-mono text-xs">
                {approval.preview.command}
              </pre>
            </>
          )}
          {pending && approval.preview.kind === 'add_workspace' && (
            <>
              <div className="mb-1.5 font-mono text-[10.5px] uppercase tracking-wide text-muted-foreground">
                将把以下目录加入可访问白名单
              </div>
              <pre className="overflow-x-auto rounded-[10px] bg-background px-3 py-2.5 font-mono text-xs">
                {approval.preview.path}
              </pre>
            </>
          )}
          {!pending && (
            <>
              <div className="mb-1.5 font-mono text-[10.5px] uppercase tracking-wide text-muted-foreground">
                参数
              </div>
              <pre className="overflow-x-auto rounded-[10px] bg-background px-3 py-2.5 font-mono text-xs">
                {prettyArgs}
              </pre>
              <div className="mb-1.5 mt-2.5 font-mono text-[10.5px] uppercase tracking-wide text-muted-foreground">
                结果
              </div>
              <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-all rounded-[10px] bg-background px-3 py-2.5 font-mono text-xs">
                {running ? '运行中…' : result}
              </pre>
            </>
          )}
          {pending && (
            <div className="mt-3 flex gap-2.5">
              <button
                onClick={() => onDecision?.('approve')}
                className="grad-brand shadow-soft-sm inline-flex items-center gap-1.5 rounded-full px-4 py-2 text-[13px] font-semibold text-white transition-[filter] hover:brightness-105"
              >
                <Check className="h-4 w-4" strokeWidth={2.5} />
                批准
              </button>
              <button
                onClick={() => onDecision?.('reject')}
                className="grad-danger shadow-soft-sm inline-flex items-center gap-1.5 rounded-full px-4 py-2 text-[13px] font-semibold text-white transition-[filter] hover:brightness-105"
              >
                <X className="h-4 w-4" strokeWidth={2.5} />
                拒绝
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
