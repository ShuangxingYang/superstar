# P2a 工具 + 只读安全 —— 设计文档

> 里程碑目标(DEVELOPMENT_PLAN L200,P2 拆分后的前半):function calling 循环 + 3 个只读工具(read_file / grep / glob)+ 沙箱 + 前端工具卡片。让 Agent 从「只会聊天」升级到「能看你的代码」。
> 验证标准:配好 workspace 后,浏览器问「搜一下代码里的 xxx」→ Agent 调 grep/glob/read_file,**工具卡片可见**(名/参数/结果)→ 给终答;`../../` 越界被拒且不崩流;纯聊天仍正常(向后兼容)。

P1 让 Agent 记事(会话持久化 + 多轮)。P2a 让 Agent **动手看**:引入 function calling 循环和只读工具,Agent 能主动 grep 代码、glob 文件名、read 文件内容,再据此回答。全程沙箱守护,只读、无写、无命令、无审批——写文件 / run_command / 三级审批 / diff / 审批弹窗都留给 **P2b**(单独脑暴)。

---

## 〇、P2 拆分说明

P2「工具+安全」拆成两片,各自可独立验证、可 demo:

- **P2a(本文档)**:function calling 循环 + 只读工具(read_file/grep/glob)+ 沙箱 + 工具卡片。产出「Agent 能看你的代码」,**无审批**(只读工具直接执行)。
- **P2b(后续)**:write_file / run_command + 三级安全(白/黑/灰)+ 审批回合边界机制 + 前端 diff / 审批弹窗。产出「Agent 能改你的代码,且安全」。

拆分理由:P2a 的循环/工具/沙箱是地基,先跑通再叠加「写操作 + 人在环路」这层复杂度;checkpoint 更密、更好学、中途可停下验证。

---

## 一、关键设计决策(逐条与用户压测后的定论)

1. **Agent 循环 = function calling(非文字 ReAct),核心放 `agent/loop.py` 生成器,chat 路由只做 SSE 转发。** 循环产 typed event(text/tool_call/tool_result/done/error),chat 路由原样包成 SSE。核心与输出通道解耦(二期飞书适配器消费同样 event),且循环可脱离 HTTP 单测。
2. **只读工具 = 纯 Python 实现(不走 shell/subprocess)。** grep 用 `os.walk` + `re`,glob 用 `pathlib.glob`,read_file 用 `Path.read_text`。零外部依赖、跨平台、根本不存在 shell 注入面。对比:Claude Code 用内置 ripgrep 是为了伺候巨型仓库且有打包二进制的基建;我们优化的是「简单+零依赖」,个人项目慢一点无感。
3. **沙箱 = workspace 单根,`safe_path` 先 `resolve()` 再判祖先。** 光查字符串里有没有 `..` 防不住(软链接、绝对路径、深层 `..`);正确姿势是先 `resolve()` 算出真实绝对路径,再判断它是否是 workspace 根的后代。KB_DIR 第二个根留给 P3。
4. **workspace_dir 空 = 报错引导(必须显式配置)。** 不默认任何目录——Agent 绝不在用户没指定的地方乱翻。验证 P2a 前需先在设置页 / config.json 填 workspace_dir。
5. **ToolRegistry.run() 永远返回字符串,三处自愈。** 未知工具名 / 参数校验失败 / 执行异常(含安全越界)统统捕获成错误文本当 tool 结果喂回模型,让模型自我修正,绝不 500 崩流。
6. **超大结果 = 截断 + 提示。** read_file 限行数/字节、grep 限命中条数,截断处加「…还有 N 行,请缩小范围」,防爆上下文、省 token、引导模型先缩小再看。
7. **max_iters = 复用 config 的 `agent.max_iters`(默认 10)。** 循环到顶仍未收敛 → yield error「达到最大步数」。
8. **前端工具卡片 = 可折叠。** 每次工具调用渲染一张卡片(工具名 + 参数 + 结果摘要),点开看全文;和 DEVELOPMENT_PLAN「不是黑盒」一致,卡片结构 P2b 可复用。

---

## 二、架构与新增/改动文件

```
backend/app/
├── services/
│   └── security.py          【新增】沙箱:get_workspace / safe_path / SecurityError
├── agent/
│   ├── tools.py             【新增】Tool + ToolRegistry(Pydantic→schema、校验自愈、执行)
│   ├── tools/
│   │   ├── fs.py            【新增】read_file(只读)
│   │   └── search.py        【新增】grep、glob(纯 Python)
│   └── loop.py              【新增·核心】run_agent_streaming:function calling 流式循环
├── api/routes/
│   └── chat.py              【改】改调 loop.run_agent_streaming,SSE 转发新增事件类型
└── models/
    └── schemas.py           【改】(如需)SSE 事件文档模型;工具入参模型放在各 tools 文件里

frontend/src/
├── components/
│   └── ToolCallCard.tsx     【新增】可折叠工具卡片
├── hooks/useChatStream.ts   【改】处理 tool_call/tool_result,把工具卡片插进消息流;历史回放还原卡片
├── lib/api.ts               【改】ChatEvent union 加 tool_call/tool_result
└── App.tsx                  【改】消息流里渲染 ToolCallCard
```

**分层职责(下→上):**
1. `security.py` — 最底层守卫:给路径,判断 `resolve()` 后是否落在 workspace 根内,越界抛 `SecurityError`。碰文件的工具都先过它。
2. `agent/tools/*.py` — 具体工具函数,签名 `def f(args: PydanticModel) -> str`。
3. `agent/tools.py` — 注册表:登记工具 + Pydantic 入参模型,自动生成 OpenAI function schema;执行时校验参数、失败自愈。
4. `agent/loop.py` — 循环引擎:喂 messages(带 tools)给模型,流式收 delta,重组分片 tool_calls,调注册表执行,结果喂回,循环;产 typed event。
5. `routes/chat.py` — 只把 event 转成 SSE。

---

## 三、security 沙箱(头号安全难点)

```python
# services/security.py
class SecurityError(Exception):
    """安全拦截(越界/未配置)—— 被工具执行层捕获成 tool 结果喂回,不崩流。"""

def get_workspace() -> Path:
    ws = config_store.get()["security"]["workspace_dir"]
    if not ws:
        raise SecurityError("未配置工作区目录,请先在设置页指定 workspace_dir")
    return Path(ws).resolve()

def safe_path(rel: str) -> Path:
    """把工具传来的相对路径钉进 workspace 根内;越界抛 SecurityError。"""
    root = get_workspace()
    target = (root / rel).resolve()   # 先拼再 resolve:把 ../ 和软链接全解开
    if target != root and root not in target.parents:
        raise SecurityError(f"路径越界,超出工作区: {rel}")
    return target
```

**为什么能防穿越**:`resolve()` 把 `a/../../etc` 真正算成 `/etc`、软链接也解开,得到真实绝对路径,再用 `root in target.parents` 判祖先(比 `startswith` 稳,避开 `/home/user-evil` 冒充 `/home/user` 的前缀陷阱)。绝对路径 `/etc/passwd` 拼进来 resolve 后也不在 root 下 → 拒。

**单根**:P2a 只有 workspace。P3 加 KB_DIR 时把 `safe_path` 扩成「落在任一允许根内即可」。

---

## 四、ToolRegistry(工具统一接口 + 自愈)

```python
# agent/tools.py
class Tool:
    def __init__(self, name, func, args_model, description): ...

class ToolRegistry:
    def register(self, name, args_model, description):
        """装饰器:登记 函数 + Pydantic 入参模型 + 描述。"""

    def to_openai_schema(self) -> list[dict]:
        """每个工具的 Pydantic 模型 → OpenAI function calling JSON schema。
        用 model.model_json_schema() 填进 {type:'function', function:{name,description,parameters}}。"""

    def run(self, name: str, raw_args: dict) -> str:
        """执行一个工具调用,统一兜错(自愈的核心),永远返回字符串:"""
        tool = self._tools.get(name)
        if not tool:
            return f"错误:未知工具 {name}"          # ① 模型幻觉的工具名
        try:
            args = tool.args_model(**raw_args)        # Pydantic 校验
        except ValidationError as e:
            return f"参数错误:{e}"                    # ② 参数不对 → 喂回让模型改
        try:
            return tool.func(args)
        except SecurityError as e:
            return f"安全拦截:{e}"                    # ③a 越界
        except Exception as e:                        # noqa: BLE001
            return f"工具执行失败:{e}"                # ③b 任何异常 → 喂回,不崩流
```

**三处自愈**:未知工具名 / 参数校验失败 / 执行异常(含越界)全部返回错误文本当 tool 结果喂回。在 function calling 协议里工具结果本就是一条 `role:tool` 文本消息,把错误也变成一种「正常返回值」,模型看到即自我修正。`run()` 从不向上抛异常,循环层无需管工具会不会炸。

---

## 五、loop.py —— function calling 流式循环(P2a 引擎)

```python
# agent/loop.py
def run_agent_streaming(sid: str):
    """喂该会话历史,跑 function calling 循环,逐步 yield typed event。
    事件:text / tool_call / tool_result / done / error。"""
    client, model = llm.get_llm_client()
    max_iters = config_store.get()["agent"]["max_iters"]

    for _ in range(max_iters):
        history = session_store._fit_context(session_store.read_messages(sid))
        stream = client.chat.completions.create(
            model=model, messages=history,
            tools=registry.to_openai_schema(), stream=True,
        )
        text_parts, tool_calls = _accumulate(stream)   # 转发 text + 重组 tool_calls 分片

        if not tool_calls:
            session_store.append_message(sid, {"role": "assistant", "content": "".join(text_parts)})
            yield {"type": "done"}
            return

        # 协议要求:先落带 tool_calls 的 assistant 消息
        session_store.append_message(sid, {
            "role": "assistant",
            "content": "".join(text_parts) or None,
            "tool_calls": tool_calls,
        })
        for tc in tool_calls:
            name = tc["function"]["name"]
            yield {"type": "tool_call", "id": tc["id"], "name": name, "args": tc["function"]["arguments"]}
            result = registry.run(name, json.loads(tc["function"]["arguments"] or "{}"))
            yield {"type": "tool_result", "id": tc["id"], "result": result}
            session_store.append_message(sid, {"role": "tool", "tool_call_id": tc["id"], "content": result})
        # 回到 for 顶:带工具结果再问模型

    yield {"type": "error", "message": "达到最大步数,已停止"}
```

**`_accumulate(stream)`(流式重组,面试难点)**:流式模式下 tool_calls 是分片吐的——`id`/`name` 先到,`arguments` 的 JSON 字符串分几个 chunk 拼。按 `delta.tool_calls[].index` 把碎片累积,直到流结束才得到完整 arguments。同时普通文字 delta(`delta.content`)照常 `yield {"type":"text",...}`。返回 `(text_parts, tool_calls_list)`。

**消息落盘时序**(接 P1 薄信封,也是 P2b 审批地基):
```
assistant(带 tool_calls)   ← 一条
tool(结果, tool_call_id)    ← 每个工具一条
[再循环] assistant(终答)    ← 一条
```
每条走 `session_store.append_message` 逐条落 JSONL。「未回答的 tool_call」天然表示暂停 —— P2b 审批「回合边界」正是在「落了 assistant-tool_calls、还没执行」处停、恢复时接着执行。P2a 只读工具不停(直接执行),但结构已为 P2b 铺好。

**错误处理**:整个循环包 try,未预期异常 → yield error;已流出的文字/工具卡片保留,不崩前端。

---

## 六、chat 路由 + 前端

### chat 路由(极简,方案 A 红利)

```python
# routes/chat.py event_stream 内
sid = req.session_id or session_store.create()
session_store.append_message(sid, {"role": "user", "content": req.message})
yield _sse({"type": "session", "session_id": sid, "title": <当前 title>})
for event in loop.run_agent_streaming(sid):   # 循环产啥,原样 SSE 转发
    yield _sse(event)
```
路由不再自拼 messages / 自调 LLM,全下放 loop。`text/done/error` 沿用 P1,新增 `tool_call/tool_result` 自动透传。异常仍在 event_stream 层兜一个 error 事件。

### 前端

- `api.ts`:`ChatEvent` union 加
  ```typescript
  | { type: 'tool_call'; id: string; name: string; args: string }
  | { type: 'tool_result'; id: string; result: string }
  ```
  (`args` 是模型给的 arguments JSON 字符串,前端展示时尝试 parse 美化,parse 失败原样显示。)
- `useChatStream.ts`:消息流元素从「纯文本」扩展成可含「工具卡片项」。收 `tool_call` 插一张卡片(状态=运行中),收同 `id` 的 `tool_result` 填结果(状态=完成),用 `id` 关联(对齐后端 tool_call_id)。
- `ToolCallCard.tsx`:可折叠卡片,标题行 `🔧 grep · "def" utils.py`,展开看完整参数 + 结果全文;默认折叠显示摘要(结果首行 / 命中数)。
- `App.tsx`:遍历消息流,文本项渲染气泡、工具卡片项渲染 `<ToolCallCard>`。
- **历史回放**:`GET /api/sessions/{sid}` 现在会返回带 `tool_calls` 的 assistant 消息和 `role:tool` 消息;切会话加载历史时要把它们还原成工具卡片(而非当普通文本)。

---

## 七、测试策略

**后端(TDD,mock LLM):**
- `test_security.py`:`safe_path` 挡住 `../../`、绝对路径、软链接越界;合法相对路径放行;workspace 未配置 → SecurityError。
- `test_tools.py`:grep 命中/未命中/超 100 条截断;glob 匹配;read_file 正常/越界/超大截断;registry 三处自愈(未知工具、参数错、执行异常都返回错误字符串,不抛)。
- `test_loop.py`:mock「先调 grep、拿结果再给终答」的假 LLM 流,断言事件序列(text→tool_call→tool_result→…→done)与落盘(assistant-tool_calls / tool / assistant 三条)。**最能证明循环正确**。含 max_iters 用尽 → error 的用例。
- `test_chat_routes.py`:补一个带工具的端到端(mock LLM),断言 SSE 里出现 tool_call/tool_result。

**前端**:沿用 P0/P1,无单测;`npm run build` 类型通过 + 浏览器手验。

---

## 八、验证方式

**后端**:`cd backend && uv run pytest -q` 全绿(含 P0/P1 老测试)。

**浏览器**(先在 config.json 的 `security.workspace_dir` 填一个真实目录,如某个代码仓库):
1. 问「工作区里有哪些 .py 文件?」→ Agent 调 glob → 工具卡片显示匹配结果 → 终答列出文件。
2. 问「grep 一下 def」→ Agent 调 grep → 卡片显示命中 → 终答。
3. 问「读一下 xxx.py」→ Agent 调 read_file → 卡片显示内容(超大则截断)→ 终答。
4. 诱导越界(「读 ../../etc/passwd」)→ 工具结果回显「路径越界」,模型道歉,**不崩流**。
5. 纯聊天(「你好」)→ 模型不调工具,直接答(向后兼容)。
6. 切换到旧会话 → 含工具调用的历史正确还原成卡片。

---

## 九、显式取舍(诚实边界)

- **只读**:P2a 无 write_file / run_command / 审批 / diff —— 全在 P2b。
- **单根沙箱**:只有 workspace,KB_DIR 留 P3。
- **上下文不裁剪**:`_fit_context` 仍原样返回(M12 再做);多轮 + 多工具结果累积可能变长,P2a 不管。
- **纯 Python 工具**:不追原生 ripgrep 速度;大仓库略慢,个人项目无感。
- **超大结果截断**:可能截掉模型想要的内容,靠「缩小范围」提示 + 模型重查兜;不做分页(交互太繁)。
- **system prompt**:P2a 可加一句极简 system(告诉模型有哪些工具、工作区是什么);完整的画像/soul 注入留 P5。
