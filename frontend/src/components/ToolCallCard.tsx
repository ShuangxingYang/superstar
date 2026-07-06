import { useState } from 'react'

type Props = { name: string; args: string; result?: string }

export default function ToolCallCard({ name, args, result }: Props) {
  const [open, setOpen] = useState(false)
  // args 是模型给的 JSON 字符串,尝试美化;parse 失败原样显示
  const prettyArgs = (() => {
    try {
      return JSON.stringify(JSON.parse(args), null, 2)
    } catch {
      return args
    }
  })()
  const running = result === undefined
  const summary = running ? '运行中…' : result.split('\n')[0] || '(空)'

  return (
    <div className="tool-card">
      <div className="tool-head" onClick={() => setOpen((o) => !o)}>
        <span className="tool-name">🔧 {name}</span>
        <span className="tool-summary">
          {running ? '⏳ ' : '✓ '}
          {summary}
        </span>
        <span className="tool-toggle">{open ? '▾' : '▸'}</span>
      </div>
      {open && (
        <div className="tool-body">
          <div className="tool-label">参数</div>
          <pre>{prettyArgs}</pre>
          <div className="tool-label">结果</div>
          <pre>{running ? '运行中…' : result}</pre>
        </div>
      )}
    </div>
  )
}
