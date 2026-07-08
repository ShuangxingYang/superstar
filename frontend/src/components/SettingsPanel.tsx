import { Loader2, Plug, Plus, Save, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'

import { getSettings, testConnection, updateSettings } from '../lib/api'
import type { AppConfig } from '../lib/api'

type TestState = { status: 'idle' | 'testing' | 'ok' | 'fail'; msg: string }
const IDLE: TestState = { status: 'idle', msg: '' }

export default function SettingsPanel() {
  const [cfg, setCfg] = useState<AppConfig | null>(null)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [test, setTest] = useState<Record<'llm' | 'embedding', TestState>>({ llm: IDLE, embedding: IDLE })

  useEffect(() => {
    getSettings().then(setCfg).catch(() => setCfg(null))
  }, [])

  if (!cfg)
    return <div className="p-10 text-sm text-muted-foreground">加载设置中…</div>

  // 局部改一个字段(section 内某 key)。computed key + 断言,避开 TS 对动态键的收窄限制。
  const patch = (section: keyof AppConfig, key: string, val: unknown) =>
    setCfg({ ...cfg, [section]: { ...(cfg[section] as object), [key]: val } } as AppConfig)

  const onSave = async () => {
    setSaving(true)
    try {
      // 整份回传:脱敏 key(含 ***)未改的话由后端 _drop_masked_keys 丢弃,不会覆盖真 key
      const next = await updateSettings(cfg)
      setCfg(next)
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } finally {
      setSaving(false)
    }
  }

  const onTest = async (kind: 'llm' | 'embedding') => {
    setTest((t) => ({ ...t, [kind]: { status: 'testing', msg: '测试中…' } }))
    const r = await testConnection(kind, cfg[kind])
    setTest((t) => ({
      ...t,
      [kind]: r.ok
        ? { status: 'ok', msg: '连接成功' }
        : { status: 'fail', msg: r.error || '连接失败' },
    }))
  }

  return (
    <div className="mx-auto w-full max-w-2xl px-8 py-8">
      <div className="mb-6 flex items-end gap-3">
        <h2 className="text-2xl font-bold tracking-tight">设置</h2>
        <div className="mb-1 text-sm text-muted-foreground">改完点底部保存,热生效</div>
      </div>

      {/* 模型(LLM) */}
      <ModelCard
        title="对话模型(LLM)"
        conn={cfg.llm}
        test={test.llm}
        onField={(k, v) => patch('llm', k, v)}
        onTest={() => onTest('llm')}
      />

      {/* 向量模型(Embedding) */}
      <ModelCard
        title="向量模型(Embedding)"
        conn={cfg.embedding}
        test={test.embedding}
        onField={(k, v) => patch('embedding', k, v)}
        onTest={() => onTest('embedding')}
      />

      {/* 安全与工作区 */}
      <Card title="安全与工作区">
        <Row label="默认工作目录">
          <Input
            value={cfg.security.default_cwd}
            placeholder="~/.superstar"
            onChange={(e) => patch('security', 'default_cwd', e.target.value)}
          />
        </Row>
        <Row label="可访问白名单目录">
          <StringList
            items={cfg.security.allowed_dirs}
            onChange={(v) => patch('security', 'allowed_dirs', v)}
            placeholder="追加一个目录(绝对路径)"
            empty="暂无白名单目录;命令与文件仅限默认工作目录"
          />
        </Row>
        <Row label="知识库目录">
          <Input
            value={cfg.security.kb_dir}
            placeholder="留空 = 用 data 下默认目录"
            onChange={(e) => patch('security', 'kb_dir', e.target.value)}
          />
        </Row>
        <Row label="命令白名单(自动放行)">
          <StringList
            items={cfg.security.cmd_whitelist}
            onChange={(v) => patch('security', 'cmd_whitelist', v)}
            placeholder="追加一个白名单命令,如 git status"
            empty="暂无白名单命令"
          />
        </Row>
        <Row label="命令黑名单(直接拒绝)">
          <StringList
            items={cfg.security.cmd_blacklist}
            onChange={(v) => patch('security', 'cmd_blacklist', v)}
            placeholder="追加一个黑名单片段,如 rm -rf"
            empty="暂无黑名单命令"
          />
        </Row>
      </Card>

      {/* Agent 参数 */}
      <Card title="Agent 参数">
        <Row label="最大迭代轮数">
          <Input
            type="number"
            className="max-w-40"
            value={cfg.agent.max_iters}
            onChange={(e) => patch('agent', 'max_iters', Number(e.target.value))}
          />
        </Row>
        <Row label="温度(temperature)">
          <Input
            type="number"
            step="0.1"
            className="max-w-40"
            value={cfg.agent.temperature}
            onChange={(e) => patch('agent', 'temperature', Number(e.target.value))}
          />
        </Row>
      </Card>

      {/* 保存 */}
      <div className="mt-6 flex items-center gap-3">
        <Button
          onClick={onSave}
          disabled={saving}
          className="grad-brand shadow-soft-md h-10 rounded-full px-6"
        >
          {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
          保存
        </Button>
        {saved && <span className="text-sm font-medium text-primary">✓ 已保存,热生效</span>}
      </div>
    </div>
  )
}

// ---- 模型连接卡(LLM / embedding 共用):base_url + api_key + model + 测试连接 ----
function ModelCard({
  title,
  conn,
  test,
  onField,
  onTest,
}: {
  title: string
  conn: { base_url: string; api_key: string; model: string }
  test: TestState
  onField: (key: string, val: string) => void
  onTest: () => void
}) {
  return (
    <Card title={title}>
      <Row label="Base URL">
        <Input value={conn.base_url} onChange={(e) => onField('base_url', e.target.value)} />
      </Row>
      <Row label="API Key">
        <Input
          type="password"
          value={conn.api_key}
          placeholder="sk-..."
          onChange={(e) => onField('api_key', e.target.value)}
        />
      </Row>
      <Row label="模型名">
        <Input value={conn.model} onChange={(e) => onField('model', e.target.value)} />
      </Row>
      <div className="mt-1 flex items-center gap-3">
        <button
          onClick={onTest}
          disabled={test.status === 'testing'}
          className="shadow-soft-sm hover:shadow-soft-md inline-flex items-center gap-2 rounded-full bg-card px-4 py-2 text-[13px] font-semibold transition-shadow disabled:opacity-50"
        >
          {test.status === 'testing' ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Plug className="h-4 w-4" />
          )}
          测试连接
        </button>
        {test.status !== 'idle' && (
          <span
            className={cn(
              'truncate text-[13px]',
              test.status === 'ok' && 'font-medium text-primary',
              test.status === 'fail' && 'text-destructive',
              test.status === 'testing' && 'text-muted-foreground',
            )}
          >
            {test.status === 'ok' ? '✓ ' : test.status === 'fail' ? '✗ ' : ''}
            {test.msg}
          </span>
        )}
      </div>
    </Card>
  )
}

// ---- 卡片外壳 ----
function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="shadow-soft-md mb-4 rounded-2xl bg-card p-6">
      <div className="mb-4 text-[15px] font-semibold">{title}</div>
      <div className="flex flex-col gap-3.5">{children}</div>
    </div>
  )
}

// ---- 一行:左标签 + 右控件 ----
function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <div className="font-mono text-[11px] uppercase tracking-wide text-muted-foreground">
        {label}
      </div>
      {children}
    </div>
  )
}

// ---- 字符串列表:逐行编辑 + 删除 + 底部追加 ----
function StringList({
  items,
  onChange,
  placeholder,
  empty,
}: {
  items: string[]
  onChange: (v: string[]) => void
  placeholder?: string
  empty?: string
}) {
  const [draft, setDraft] = useState('')
  const add = () => {
    const v = draft.trim()
    if (!v || items.includes(v)) return
    onChange([...items, v])
    setDraft('')
  }
  return (
    <div className="flex flex-col gap-2">
      {items.length === 0 && empty && (
        <div className="rounded-xl bg-secondary/60 px-3 py-2 text-xs text-muted-foreground">{empty}</div>
      )}
      {items.map((it, i) => (
        <div key={i} className="flex items-center gap-2">
          <Input
            value={it}
            onChange={(e) => onChange(items.map((x, j) => (j === i ? e.target.value : x)))}
          />
          <button
            title="删除"
            onClick={() => onChange(items.filter((_, j) => j !== i))}
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive"
          >
            <Trash2 className="h-4 w-4" />
          </button>
        </div>
      ))}
      <div className="flex items-center gap-2">
        <Input
          value={draft}
          placeholder={placeholder}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && add()}
        />
        <button
          title="添加"
          onClick={add}
          className="grad-brand shadow-soft-sm flex h-9 w-9 shrink-0 items-center justify-center rounded-xl text-white transition-[filter] hover:brightness-105"
        >
          <Plus className="h-4 w-4" strokeWidth={2.5} />
        </button>
      </div>
    </div>
  )
}
