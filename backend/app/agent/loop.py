"""
loop.py —— function calling 流式循环(P2a 引擎,Agent 的「大脑」)

职责:喂会话历史(带工具 schema)给模型 → 流式收 delta → 重组分片的 tool_calls →
调注册表执行工具 → 结果喂回 → 再问,直到模型不再调工具(给终答)或到达 max_iters。
产 typed event(text/tool_call/tool_result/done/error),与输出通道解耦——
chat 路由把 event 原样转 SSE,二期飞书适配器可消费同样的 event。
"""
import json
import logging

from app.agent import pending as pending_store
from app.agent.gate import gate_tool_call
from app.agent.tools import registry
from app.services import config_store, llm, memory, session_store

logger = logging.getLogger(__name__)

# 极简 system:告诉模型有工具、大致职责。完整画像/soul 注入留 P5。
SYSTEM_PROMPT = (
    "你是一个本地助手,可以调用工具查看并修改用户电脑上的文件:"
    "grep(按正则搜索)、glob(按通配列文件)、read_file(读文件)、"
    "write_file(写文件)、run_command(跑 shell 命令)、search_kb(检索文档知识库)、"
    "add_workspace/remove_workspace(增删可访问目录)。"
    "你只能访问「允许目录」内的文件(默认工作目录 + 白名单目录);路径优先用绝对路径。"
    "需要访问允许目录之外的文件时,用 add_workspace 申请把该目录(绝对路径)加入白名单(需用户批准);"
    "不再需要时用 remove_workspace 移除。"
    "需要看/改文件再作答时就调用工具;能直接回答的问题不必调用。"
    "写文件和跑命令可能需要用户审批,危险命令会被拒绝,你会在结果里看到反馈。"
    "用 search_kb 查资料时:只依据检索到的片段回答;片段里没有的,"
    "明确说「知识库里没有相关内容」,不要编造;回答时带上来源。"
    "你有长期记忆:update_profile(沉淀关于用户的画像)、update_soul(调整你自己的行为准则)。"
    "发现关于用户的稳定事实(偏好、身份、常用项目)时,主动用 update_profile 记下来;"
    "整份覆盖,先基于上面已注入的记忆合并再写回。"
)


def _prune_dangling_tool_calls(messages: list[dict]) -> list[dict]:
    """剪掉「悬空 tool_call」——带 tool_calls 的 assistant 后面没有对应 tool 结果的情况。

    function calling 铁律:assistant 的每个 tool_call 都必须有一条 role:tool 结果响应,
    否则 provider 会 400「请求参数错误」,且这条脏历史会毒死整个会话(之后每次都带上、每次都挂)。
    悬空只可能来自「上一轮流式被中断」(客户端断连,落了 assistant-tool_calls 没落 tool 结果)。
    这里发请求前做一次「读时净化」:JSONL 仍是真相(不改盘),只把发给模型的视图修干净。
    (P2b 审批的合法暂停态另说——那种悬空是有意的,届时要区别对待,不能一律剪。)
    """
    answered = {m.get("tool_call_id") for m in messages if m.get("role") == "tool"}
    result: list[dict] = []
    valid_ids: set[str] = set()
    for m in messages:
        role = m.get("role")
        if role == "assistant" and m.get("tool_calls"):
            kept = [tc for tc in m["tool_calls"] if tc.get("id") in answered]
            if kept:
                valid_ids.update(tc["id"] for tc in kept)
                result.append({**m, "tool_calls": kept})
            elif m.get("content"):
                result.append({"role": "assistant", "content": m["content"]})  # 只留正文
            # 否则(无正文、tool_calls 全悬空)→ 整条丢弃
        elif role == "tool":
            if m.get("tool_call_id") in valid_ids:
                result.append(m)
            # 否则孤儿 tool 消息(对应调用已被剪)→ 丢
        else:
            result.append(m)
    return result


def _accumulate(stream):
    """消费一次流式响应:普通文字 yield text 事件;tool_calls 分片按 index 重组。
    return (text_parts, reasoning_parts, tool_calls) —— tool_calls 是 OpenAI 兼容结构,可直接回灌历史。

    面试难点:流式下 tool_calls 是「碎着吐」的——id/name 先到,arguments 的 JSON
    字符串分几个 chunk 拼。用 delta.tool_calls[].index 把碎片按槽位累积。
    """
    text_parts: list[str] = []
    reasoning_parts: list[str] = []    # 思考分片,拼起来落盘(供刷新回放),但喂模型前会被 _strip_reasoning 摘掉
    acc: dict[int, dict] = {}          # index -> {id, name, arguments}
    for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        # 推理模型:思考内容在正文之前先到,字段名可能是 reasoning_content(DeepSeek/网关系)
        # 或 reasoning(OpenAI 系)。实时 yield 给前端展示,同时攒起来落盘(刷新可回放);
        # 但绝不喂回模型——发请求前 _strip_reasoning 会把它摘掉(省 token、不污染上下文)。
        reasoning = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
        if reasoning:
            reasoning_parts.append(reasoning)
            yield {"type": "reasoning", "content": reasoning}
        if getattr(delta, "content", None):
            text_parts.append(delta.content)
            yield {"type": "text", "content": delta.content}
        for tc in getattr(delta, "tool_calls", None) or []:
            slot = acc.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
            if tc.id:
                slot["id"] = tc.id
            if tc.function and tc.function.name:
                slot["name"] = tc.function.name
            if tc.function and tc.function.arguments:
                slot["arguments"] += tc.function.arguments
    tool_calls = [
        {
            "id": s["id"],
            "type": "function",
            "function": {"name": s["name"], "arguments": s["arguments"]},
        }
        for _, s in sorted(acc.items())
    ]
    return text_parts, reasoning_parts, tool_calls


def _strip_reasoning(messages: list[dict]) -> list[dict]:
    """摘掉每条消息的 reasoning 字段——思考只给人看(落盘回放用),绝不喂回模型。
    浅拷贝去键即可:reasoning 是我们额外加的,OpenAI 不认;留着白占 token 还可能被 provider 拒。"""
    return [{k: v for k, v in m.items() if k != "reasoning"} for m in messages]


def _parse_args(tc: dict) -> dict:
    """解析一个 tool_call 的 arguments(JSON 字符串)→ dict;非法/空 → {}(交给下游自愈)。"""
    raw = tc["function"]["arguments"]
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}


def run_agent_streaming(sid: str):
    """喂该会话历史,跑 function calling 循环,逐步 yield typed event。"""
    client, model = llm.get_llm_client()
    cfg = config_store.get()
    max_iters = cfg["agent"]["max_iters"]
    # 推理强度:配置里设了才传(空=不传)。非推理模型不认这参数,传空会 400;
    # 设了则让推理模型吐 reasoning_content,由 _accumulate 转成 reasoning 事件给前端。
    effort = cfg["llm"].get("reasoning_effort") or ""
    extra = {"reasoning_effort": effort} if effort else {}
    logger.info("agent 循环开始: sid=%s, max_iters=%d, reasoning_effort=%s",
                sid, max_iters, effort or "(off)")
    try:
        for _ in range(max_iters):
            history = session_store._fit_context(session_store.read_messages(sid))
            # 发请求前剪掉悬空 tool_call(上一轮被中断留下的脏态),否则 provider 400 毒死会话
            history = _prune_dangling_tool_calls(history)
            # 摘掉历史里的 reasoning:思考只落盘给人回放,绝不喂回模型(省 token、不污染上下文)
            history = _strip_reasoning(history)
            memory_block = memory.build_memory_block()   # 每轮读盘;内容不变则前缀稳定,保 prompt cache
            system_content = SYSTEM_PROMPT + memory_block
            messages = [{"role": "system", "content": system_content}, *history]
            stream = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=registry.to_openai_schema(),
                stream=True,
                **extra,
            )
            # yield from:把 _accumulate 里的 text/reasoning 事件原样透传给外层消费者,
            # 同时用它的 return 值拿到重组好的 (text_parts, reasoning_parts, tool_calls)。
            text_parts, reasoning_parts, tool_calls = yield from _accumulate(stream)
            reasoning = "".join(reasoning_parts)   # 拼成整段思考,落盘供刷新回放

            if not tool_calls:
                # 终答:content + reasoning(有则存,供回放;_strip_reasoning 保证下轮不喂回模型)
                msg: dict = {"role": "assistant", "content": "".join(text_parts)}
                if reasoning:
                    msg["reasoning"] = reasoning
                session_store.append_message(sid, msg)
                yield {"type": "done"}
                return

            # 每个 tool_call 先过 gate 判处置:auto 当场跑,deny 直接拒,approve 停下等审批。
            # 期间照常 yield 事件给前端实时显示卡片;真正落盘留到整轮末尾连续写(治本防悬空)。
            tool_results: list[tuple[str, str]] = []   # (id, result) —— auto/deny 的
            pending_calls: list[dict] = []             # approve 的完整 tool_call
            previews: dict = {}                        # id -> 预览
            for tc in tool_calls:
                name = tc["function"]["name"]
                parsed = _parse_args(tc)
                action, preview = gate_tool_call(name, parsed)
                if action == "approve":
                    yield {"type": "approval_required", "id": tc["id"], "name": name,
                           "args": tc["function"]["arguments"], "preview": preview}
                    pending_calls.append(tc)
                    previews[tc["id"]] = preview
                else:
                    yield {"type": "tool_call", "id": tc["id"], "name": name,
                           "args": tc["function"]["arguments"]}
                    result = ("被安全策略拒绝(黑名单/越界)" if action == "deny"
                              else registry.run(name, parsed))   # 仅 auto 真执行
                    yield {"type": "tool_result", "id": tc["id"], "result": result}
                    tool_results.append((tc["id"], result))

            # 一整轮跑完才落盘:assistant(tool_calls) + 各 tool 结果「连续追加、中间不 yield」(治本防悬空)。
            session_store.append_message(
                sid,
                {
                    "role": "assistant",
                    "content": "".join(text_parts) or None,
                    "tool_calls": tool_calls,
                    **({"reasoning": reasoning} if reasoning else {}),   # 调工具那轮的思考也落盘回放
                },
            )
            for tid, r in tool_results:
                session_store.append_message(sid, {"role": "tool", "tool_call_id": tid, "content": r})
            if pending_calls:
                # 有待审批 → 写 sidecar 标记「有意悬空」,结束流,等 /resume 恢复
                pending_store.write(sid, pending_calls, previews)
                logger.info("审批暂停: sid=%s, 待审批=%d", sid, len(pending_calls))
                return
            # 无 pending → 回 for 顶,带工具结果再问模型(全 auto/deny 的轮,与 P2a 行为一致)
        logger.info("agent 循环到达 max_iters: sid=%s", sid)
        yield {"type": "error", "message": "达到最大步数,已停止"}
    except Exception as e:  # noqa: BLE001 - 未预期异常 → 兜成 error 事件,已流出的内容保留
        logger.warning("agent 循环失败: sid=%s err=%s", sid, type(e).__name__)
        yield {"type": "error", "message": str(e)}


def resume_streaming(sid: str, tool_call_id: str, decision: str):
    """恢复一个待审批的 tool_call。decision ∈ 'approve'|'reject'。
    批准 → 真执行;拒绝 → 落「已拒绝」。若本轮还有别的待批 → 结束等下次;全批完 → 继续正常循环。
    """
    pend = pending_store.read(sid)
    if not pend:
        yield {"type": "error", "message": "没有待审批的操作"}
        return
    tc = next((t for t in pend["tool_calls"] if t["id"] == tool_call_id), None)
    if tc is None:
        yield {"type": "error", "message": "待审批操作不存在或已处理"}
        return

    if decision == "approve":
        result = registry.run(tc["function"]["name"], _parse_args(tc))   # 真正执行
    else:
        result = "用户已拒绝此操作"
    logger.info("审批恢复: sid=%s, id=%s, decision=%s", sid, tool_call_id, decision)
    yield {"type": "tool_result", "id": tool_call_id, "result": result}
    session_store.append_message(sid, {"role": "tool", "tool_call_id": tool_call_id, "content": result})

    remaining = [t for t in pend["tool_calls"] if t["id"] != tool_call_id]
    if remaining:                              # 本轮还有别的待批 → 结束,等下次点击
        prev = {k: v for k, v in pend["previews"].items() if k != tool_call_id}
        pending_store.write(sid, remaining, prev)
        return
    pending_store.clear(sid)                   # 全批完 → 带新结果继续问模型
    yield from run_agent_streaming(sid)


def reject_all_pending(sid: str) -> None:
    """把某会话所有待批操作按拒绝落盘并清 sidecar(不 yield)。
    用于「审批未决、用户却发了新消息」:先把悬空协议合法地收尾,再走新消息。"""
    pend = pending_store.read(sid)
    if not pend:
        return
    for tc in pend["tool_calls"]:
        session_store.append_message(sid, {
            "role": "tool", "tool_call_id": tc["id"],
            "content": "用户已拒绝此操作(发起了新消息)"})
    pending_store.clear(sid)
    logger.info("残留 pending 自动拒绝: sid=%s, 数量=%d", sid, len(pend["tool_calls"]))
