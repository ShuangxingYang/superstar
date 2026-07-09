import { Check, Eye, EyeOff, Loader2, Plug, Plus, Save, Star, Trash2, X } from 'lucide-react'
import { useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'

import { getSettings, testConnection, updateSettings } from '../lib/api'
import type { AppConfig, LLMProfile } from '../lib/api'

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

  // ---- 配置预设:切换/另存/删除都「立即落盘生效」,无需再点保存 ----
  // 落盘同时更新本地 cfg,并回填服务端返回(保持与磁盘一致)。
  const persist = async (next: AppConfig) => {
    setCfg(next)                       // 乐观更新,点击即时反馈
    const saved = await updateSettings(next)
    setCfg(saved)
  }

  // 点预设胶囊 → 把它的连接要素(含 reasoning_effort)写进生效的 llm,立即生效
  // 解构剥掉 name,剩下的正好是 llm 的形状;以后 llm 加字段这里也不用改
  const applyProfile = (p: LLMProfile) => {
    const { name: _name, ...conn } = p
    persist({ ...cfg, llm: conn })
  }

  // 另存当前 llm 表单为一个新预设(名字用 prompt 取,本地自用够简单)
  const saveCurrentAsProfile = () => {
    const name = window.prompt('给这套配置起个名字', cfg.llm.model || '未命名')?.trim()
    if (!name) return
    const profile: LLMProfile = { name, ...cfg.llm }
    // 同名则覆盖,否则追加
    const rest = cfg.llm_profiles.filter((p) => p.name !== name)
    persist({ ...cfg, llm_profiles: [...rest, profile] })
  }

  const deleteProfile = (name: string) =>
    persist({ ...cfg, llm_profiles: cfg.llm_profiles.filter((p) => p.name !== name) })

  return (
    <div className="mx-auto w-full max-w-2xl px-8 py-8">
      <div className="mb-6 flex items-end gap-3">
        <h2 className="text-2xl font-bold tracking-tight">设置</h2>
        <div className="mb-1 text-sm text-muted-foreground">改完点底部保存,热生效</div>
      </div>

      {/* 配置预设:多套 LLM 配置一键切换 */}
      <ProfileBar
        profiles={cfg.llm_profiles}
        current={cfg.llm}
        onApply={applyProfile}
        onSaveCurrent={saveCurrentAsProfile}
        onDelete={deleteProfile}
      />

      {/* 模型(LLM) */}
      <ModelCard
        title="对话模型(LLM)"
        conn={cfg.llm}
        test={test.llm}
        onField={(k, v) => patch('llm', k, v)}
        onTest={() => onTest('llm')}
        showReasoning
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

// ---- 配置预设条:胶囊列表 + 另存当前。点胶囊即时切换,当前生效的高亮 ----
function ProfileBar({
  profiles,
  current,
  onApply,
  onSaveCurrent,
  onDelete,
}: {
  profiles: LLMProfile[]
  current: { base_url: string; api_key: string; model: string }
  onApply: (p: LLMProfile) => void
  onSaveCurrent: () => void
  onDelete: (name: string) => void
}) {
  // 当前生效判定:三要素与生效的 llm 全等即为"正在用"(不额外存索引)
  const isActive = (p: LLMProfile) =>
    p.base_url === current.base_url && p.api_key === current.api_key && p.model === current.model

  return (
    <div className="shadow-soft-md mb-4 rounded-2xl bg-card p-6">
      <div className="mb-3 flex items-center gap-2 text-[15px] font-semibold">
        <Star className="h-4 w-4 text-primary" />
        配置预设
        <span className="text-xs font-normal text-muted-foreground">点一下即切换,无需保存</span>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {profiles.length === 0 && (
          <span className="text-xs text-muted-foreground">
            还没有预设。填好下方配置后点「另存当前」存成一套,之后就能一键切换。
          </span>
        )}
        {profiles.map((p) => {
          const active = isActive(p)
          return (
            <div
              key={p.name}
              className={cn(
                'shadow-soft-sm group inline-flex items-center gap-1.5 rounded-full py-1.5 pl-3 pr-1.5 text-[13px] transition-all',
                active
                  ? 'grad-brand font-semibold text-white'
                  : 'bg-secondary/70 hover:bg-secondary text-foreground',
              )}
            >
              <button
                type="button"
                onClick={() => onApply(p)}
                title={`${p.model}  ·  ${p.base_url}`}
                className="inline-flex items-center gap-1.5"
              >
                {active && <Check className="h-3.5 w-3.5" strokeWidth={3} />}
                {p.name}
              </button>
              <button
                type="button"
                title="删除此预设"
                onClick={() => onDelete(p.name)}
                className={cn(
                  'flex h-5 w-5 items-center justify-center rounded-full transition-colors',
                  active ? 'hover:bg-white/25' : 'hover:bg-destructive/15 hover:text-destructive',
                )}
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          )
        })}
        <button
          type="button"
          onClick={onSaveCurrent}
          className="shadow-soft-sm hover:shadow-soft-md inline-flex items-center gap-1 rounded-full bg-card px-3 py-1.5 text-[13px] font-medium text-muted-foreground transition-all hover:text-foreground"
        >
          <Plus className="h-3.5 w-3.5" strokeWidth={2.5} />
          另存当前
        </button>
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
  showReasoning = false,
}: {
  title: string
  conn: { base_url: string; api_key: string; model: string; reasoning_effort?: string }
  test: TestState
  onField: (key: string, val: string) => void
  onTest: () => void
  showReasoning?: boolean // 仅 LLM 卡显示「思考强度」;embedding 无此概念
}) {
  const [showKey, setShowKey] = useState(false) // 默认密文,点眼睛看明文
  return (
    <Card title={title}>
      <Row label="Base URL">
        <Input value={conn.base_url} onChange={(e) => onField('base_url', e.target.value)} />
      </Row>
      <Row label="API Key">
        <div className="relative">
          <Input
            type={showKey ? 'text' : 'password'}
            className="pr-10"
            value={conn.api_key}
            placeholder="sk-..."
            onChange={(e) => onField('api_key', e.target.value)}
          />
          <button
            type="button"
            title={showKey ? '隐藏' : '显示'}
            onClick={() => setShowKey((v) => !v)}
            className="absolute right-2 top-1/2 -translate-y-1/2 rounded-md p-1 text-muted-foreground transition-colors hover:text-foreground"
          >
            {showKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
          </button>
        </div>
      </Row>
      <Row label="模型名">
        <Input value={conn.model} onChange={(e) => onField('model', e.target.value)} />
      </Row>
      {showReasoning && (
        <Row label="思考强度(推理模型)">
          <select
            value={conn.reasoning_effort ?? ''}
            onChange={(e) => onField('reasoning_effort', e.target.value)}
            className="h-9 rounded-xl bg-secondary/60 px-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <option value="">不开启(普通模型选这个)</option>
            <option value="low">low · 浅思考</option>
            <option value="medium">medium · 中等</option>
            <option value="high">high · 深度思考</option>
          </select>
          <div className="text-xs text-muted-foreground">
            仅推理模型(如 gpt-5 系)支持;开启后会展示「思考过程」。普通模型请保持「不开启」,否则可能报错。
          </div>
        </Row>
      )}
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
