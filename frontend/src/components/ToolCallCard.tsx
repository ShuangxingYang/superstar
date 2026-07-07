import { useState } from 'react'

import type { ApprovalPreview } from '../lib/api'

type Props = {
  name: string
  args: string
  result?: string
  approval?: { preview: ApprovalPreview; status: 'pending' | 'approved' | 'rejected' }
  onDecision?: (decision: 'approve' | 'reject') => void
}

// diff 文本按行着色:+ 绿、- 红、@@ 蓝、其余默认
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
    <div className={`tool-card${pending ? ' tool-card-pending' : ''}`}>
      <div className="tool-head" onClick={() => setOpen((o) => !o)}>
        <span className="tool-name">🔧 {name}</span>
        <span className="tool-summary">
          {pending ? '✋ ' : running ? '⏳ ' : '✓ '}
          {summary}
        </span>
        <span className="tool-toggle">{open ? '▾' : '▸'}</span>
      </div>
      {open && (
        <div className="tool-body">
          {pending && approval.preview.kind === 'write' && (
            <>
              <div className="tool-label">将写入 {approval.preview.path}</div>
              <DiffView diff={approval.preview.diff} />
            </>
          )}
          {pending && approval.preview.kind === 'command' && (
            <>
              <div className="tool-label">将执行命令 ⚠️ 灰名单</div>
              <pre>{approval.preview.command}</pre>
            </>
          )}
          {!pending && (
            <>
              <div className="tool-label">参数</div>
              <pre>{prettyArgs}</pre>
              <div className="tool-label">结果</div>
              <pre>{running ? '运行中…' : result}</pre>
            </>
          )}
          {pending && (
            <div className="approval-actions">
              <button className="btn-approve" onClick={() => onDecision?.('approve')}>
                ✓ 批准
              </button>
              <button className="btn-reject" onClick={() => onDecision?.('reject')}>
                ✗ 拒绝
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
