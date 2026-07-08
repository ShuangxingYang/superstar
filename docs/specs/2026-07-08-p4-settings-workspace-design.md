# P4 设置页 + 工作区权限模型 + 上下文面板 设计

> 里程碑 P4:补齐产品界面(设置页、右栏上下文面板、各种态),并把「工作区/文件权限模型」从全局单目录重构为「默认 cwd + 白名单目录组」的业界通用形态。
> 状态:设计已评审通过,待拆实现计划。
> 前置:P0-P3(竖切闭环 / 会话 / 工具+安全+审批 / RAG)已完成。

---

## 〇、项目阶段约定(重要)

superstar 处于**项目初期、个人自用、无外部用户**。本设计**豁免**全局的 api-compatibility「只加不删」约束(2026-07-08 用户决定):涉及已有字段/接口/逻辑的**破坏性变更是允许的**,优先**最终代码精简**——不写迁移、回退、双字段并存等兼容分支。现有运行时数据(`config.json`)需要时**手动迁一次**,代码里不留兼容逻辑。

---

## 一、背景与目标

P0-P3 打通了「对话 → 工具 → 安全审批 → RAG」的闭环,但产品界面还缺三块:

1. **设置页**:目前只有后端 `settings` 路由,前端 ⚙️ 是禁用占位。换机器后只能手改 `data/config.json` 配 key —— 跟"多端维护"诉求冲突。
2. **工作区权限模型偏简单**:`security.workspace_dir` 是全局单目录,既是唯一沙箱根、又是 cwd。无法"同时介入多个项目",也没有"信任目录白名单"的表达力。
3. **右栏上下文面板**缺失:用户看不到"当前工作区 / 知识库文档数"等运行时上下文。

P4 目标:三块一起补齐,其中工作区模型对标 Claude Code / OpenClaw 重构。

---

## 二、工作区权限模型(核心)

### 2.1 调研结论(对标业界)

| 维度 | Claude Code | OpenClaw(小龙虾) |
|---|---|---|
| 可访问范围 | 启动目录 + `--add-dir` 的**多个**目录 | workspace + specified directories 白名单(**多个**) |
| cwd(命令默认在哪跑) | **单一**:启动目录 | **单一**:`~/.openclaw/workspace/`(`agents.defaults.workspace`) |
| 寻址 | 文件工具吃**绝对路径**;有硬 workspace boundary | 单一 workspace 内 |

**提炼出两条业界共识**:

1. **「可访问范围」与「cwd」是两个分开的概念**:可访问范围可以是多个目录;cwd 是单一固定的主目录。
2. **绝对路径寻址**消除多目录下"相对路径指哪个根"的歧义。

**针对本项目的判断(单用户本地)**:per-session 工作区隔离**没有价值**——都是同一个人、同一台电脑、自己的文件,不需要防自己。因此采用**全局**工作区模型(不随会话变),与 OpenClaw 一致。这也让实现大幅简化:全部落在 `config.json` 的 `security` 段,**不动 session_store,不需要 ContextVar 注入**。

### 2.2 模型定义

`security` 段两个字段(**直接重构,不保留旧 `workspace_dir`**):

- **`default_cwd: str`**:默认工作目录 = `run_command` 默认 cwd + 相对路径基准。默认 `~/.superstar`(agent 的"家",类比 `~/.openclaw/workspace/`)。自动纳入可访问范围。
- **`allowed_dirs: list[str]`**:白名单,一组可读写的目录,全局所有会话通用。默认 `["/Users/shuangxingyang/Desktop"]`。
- **可访问边界**:`safe_path` 校验目标落在 `{default_cwd} ∪ allowed_dirs` 中**任一**根内(各自 `expanduser().resolve()` 后判祖先),否则 `SecurityError`。
- **run_command 的 cwd**:默认 `default_cwd`;可选参数 `cwd` 指定为白名单内的其他目录(校验后使用,越界拒)。
- **路径支持 `~` 展开**:配置里可写 `~/.superstar`,读取时 `expanduser()`。

### 2.3 动态增删工作区(工具)

新增两个工具,让 agent 在对话中管理白名单:

- **`add_workspace(path)`**:把 `path` 加入 `allowed_dirs`。**走审批**(复用 P2b 人在环路):审批卡明确展示要加入的 `expanduser().resolve()` 后**绝对路径**,由用户判断放行。批准后写入 `config.security.allowed_dirs`(去重),热生效。
- **`remove_workspace(path)`**:从 `allowed_dirs` 移除 `path`。收权无害,**自动放行**(不审批)。移除不存在的项返回幂等提示。

**安全说明**:`add_workspace` 不设"allowed_roots 护栏"——**人工审批就是护栏**。审批预览必须展示 `resolve()` 后的绝对路径(而非模型传入的原始串),让用户看清真实目标(防 `~`/`../` 障眼)。

---

## 三、后端改动

### 3.1 `services/config_store.py`
- `DEFAULTS["security"]` 重构:删 `workspace_dir`,改为 `"default_cwd": "~/.superstar"` + `"allowed_dirs": ["/Users/shuangxingyang/Desktop"]`。
- 现有 `data/config.json` 里的旧 `workspace_dir` 值:实现时手动把它挪到 `default_cwd`(一次性改文件),代码不留迁移分支。

### 3.2 `services/security.py`
- 新增 `get_default_cwd() -> Path`:读 `default_cwd`,`expanduser().resolve()`;不存在则 `mkdir(parents=True, exist_ok=True)`(`~/.superstar` 首次自动建);为空则 `SecurityError`。
- 新增 `get_allowed_roots() -> list[Path]`:`default_cwd` + `allowed_dirs`,各 `expanduser().resolve()`,去空去重返回。全空则 `SecurityError`。
- `safe_path(path)` 改为多根校验:对每个 root 计算 `(root / path).resolve()`(绝对 `path` 直接 `resolve()`),命中任一 `root == target or root in target.parents` 即返回;都不命中抛 `SecurityError`。
- 删除旧 `get_workspace()`(被 `get_default_cwd` / `get_allowed_roots` 取代)。
- `classify_command` 不变(命令三级名单与工作区正交)。

### 3.3 `agent/tools/shell.py`
- `RunCommandArgs` 新增可选 `cwd: str | None`(默认 None → 用 `security.get_default_cwd()`)。传入时经 `safe_path` 校验须落在白名单内,越界拒。

### 3.4 `agent/tools/workspace.py`(新增)
- `AddWorkspaceArgs{path: str}` / `add_workspace(args) -> str`:`expanduser().resolve()` 后写入 `allowed_dirs`(去重),返回"已加入可访问目录: {abs}"。执行体只管写配置;是否需审批由 gate 判定(见 3.6)。
- `RemoveWorkspaceArgs{path: str}` / `remove_workspace(args) -> str`:从 `allowed_dirs` 移除,返回结果(幂等)。

### 3.5 工具注册
- 注册 `add_workspace` / `remove_workspace`。
- system prompt 补一句工具用途(何时用 add/remove;路径用绝对路径)。

### 3.6 `agent/gate.py` + 审批预览
- gate 判 `add_workspace` 为**需审批**(与 `write_file` 同级);`remove_workspace` **自动放行**。
- `add_workspace` 的 approval preview 新增 kind:`{kind: 'add_workspace', path: <绝对路径>}`。

### 3.7 `models/schemas.py`
- `SecuritySettings` / `SecurityUpdate`:删 `workspace_dir`,加 `default_cwd: str` + `allowed_dirs: list[str]`。
- `TestConnectionRequest` 新增 `kind: Literal["llm", "embedding"]`(区分测哪种服务;前端按分区传)。
- 审批事件 preview 联合类型新增 `add_workspace` 分支。

### 3.8 `api/routes/settings.py`
- `POST /api/settings/test` 按 `kind` 分流:`llm` 走 `chat.completions.create(max_tokens=1)`;`embedding` 走 `client.embeddings.create(model, input="ping")`。错误透传,日志不打印 key。

---

## 四、前端改动

### 4.1 `lib/api.ts`
- 新增类型 `AppConfig` / `ConfigUpdate`(对齐后端,含 `default_cwd` / `allowed_dirs`)。
- 新增 `getSettings()` / `updateSettings(partial)` / `testConnection(kind, {base_url,api_key,model})`。
- `ApprovalPreview` 联合类型新增 `{kind:'add_workspace'; path:string}`。

### 4.2 设置页组件 `components/SettingsPanel.tsx`(新增,独立 view)
- App 的 `view` 增加 `'settings'`;侧栏 ⚙️ 从禁用占位改为可点,切到设置页(像知识库页那样占主区)。
- 分区:
  - **LLM**:base_url / api_key / model + [测试连接];key 输入框显示脱敏值,聚焦编辑时清空占位。
  - **embedding**:同上,测连接 `kind='embedding'`。
  - **安全设置**:默认工作目录(`default_cwd`)、白名单 `allowed_dirs`(列表增删)、知识库目录(`kb_dir`,P3 现有字段)、命令白名单 / 黑名单(列表增删)。
  - **Agent 参数**:max_iters(number)、temperature(number)。
- 保存走 `PUT /api/settings`(局部更新);脱敏 key 未改时原样回传由后端 `_drop_masked_keys` 丢弃(这是**安全需求**,非兼容逻辑,保留)。
- **不做**:RAG 参数编辑(留 `config.json`)、服务商预置下拉(手填 base_url)。

### 4.3 右栏上下文面板 `components/ContextPanel.tsx`(新增)
- 布局从两栏(侧栏 + 主区)改为三栏,右栏常驻(聊天视图下显示)。
- 内容(全局,不随会话变):默认工作目录、白名单目录列表、知识库文档数(调 `kbStats`)。
- 快捷入口:[管理知识库] → kb 视图;[打开设置] → settings 视图。

### 4.4 审批卡 `components/ToolCallCard.tsx`
- 支持 `add_workspace` 预览:展示"将把 <绝对路径> 加入可访问目录",批准/拒绝复用现有审批 UI。

### 4.5 各态
- 首启未配 LLM(`is_llm_configured` 为假)→ 首屏引导进设置页。
- 测连接:进行中 / 成功(绿)/ 失败(红,展示 error)。
- 保存:成功 / 失败提示。
- 空态:白名单为空、知识库为空。

---

## 五、测试策略(不依赖真网络/真库)

- **security 多根 safe_path**:命中 default_cwd、命中 allowed_dirs 之一、越界被拒、`../` 穿越被拒、绝对路径归属、`~` 展开。
- **run_command cwd 参数**:默认用 default_cwd;传白名单内目录 OK;传越界目录被拒(mock subprocess)。
- **workspace 工具**:`add_workspace` 写入去重、`remove_workspace` 幂等;gate 判 `add_workspace` 需审批 / `remove_workspace` 放行。
- **审批流**:`add_workspace` 触发 `approval_required`(preview.kind == 'add_workspace' 且 path 为绝对路径),批准后 resume 真正写入白名单。
- **settings embedding 测连接**:`kind='embedding'` 走 embeddings 接口(mock client)。
- **前端**:`npm run build` 通过(tsc + vite)。

---

## 六、安全要点(延续既有约束)

- `add_workspace` 审批预览展示 `resolve()` 后**绝对路径**;日志只记路径,不记其他。
- `safe_path` 多根仍是"先 resolve 再判祖先",防软链接 / `../` / 绝对路径穿越。
- key 绝不进日志;API 出参脱敏(`sk-***1234`);脱敏 key 回传不覆盖真 key(安全需求,保留)。
- 命令三级名单不变。

---

## 七、明确不做(YAGNI / 二版)

- **per-session 工作区隔离**:单用户本地无价值,不做。
- **allowed_roots 大区护栏**:`add_workspace` 已走人工审批,不叠加护栏。
- **RAG 参数前端编辑、服务商预置下拉**:留 `config.json` / 手填。
- **多 cwd 并行 / 会话级 cwd 切换**:命令按需带 `cwd` 参数即可,不做有状态的 active 切换。
- **API 兼容包袱**:项目初期无用户,破坏性变更允许,优先精简(见 §〇)。

---

## 八、涉及文件

**后端**
- 改:`services/config_store.py`、`services/security.py`、`agent/tools/shell.py`、`agent/gate.py`、`models/schemas.py`、`api/routes/settings.py`、工具注册处、system prompt
- 新增:`agent/tools/workspace.py`、对应测试

**前端**
- 改:`lib/api.ts`、`App.tsx`(三栏 + settings view)、`components/SessionList.tsx`(⚙️ 可点)、`components/ToolCallCard.tsx`(add_workspace 预览)
- 新增:`components/SettingsPanel.tsx`、`components/ContextPanel.tsx`
