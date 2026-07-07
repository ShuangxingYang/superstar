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
from app.services import config_store, llm, session_store

logger = logging.getLogger(__name__)

# 极简 system:告诉模型有工具、大致职责。完整画像/soul 注入留 P5。
SYSTEM_PROMPT = (
    "你是一个本地编码助手,可以调用工具查看并修改用户工作区里的代码:"
    "grep(按正则搜索)、glob(按通配列文件)、read_file(读文件)、"
    "write_file(写文件)、run_command(跑 shell 命令)。"
    "需要看/改代码再作答时就调用工具;能直接回答的问题不必调用。"
    "写文件和跑命令可能需要用户审批,危险命令会被拒绝,你会在结果里看到反馈。"
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
    return (text_parts, tool_calls) —— tool_calls 是 OpenAI 兼容结构,可直接回灌历史。

    面试难点:流式下 tool_calls 是「碎着吐」的——id/name 先到,arguments 的 JSON
    字符串分几个 chunk 拼。用 delta.tool_calls[].index 把碎片按槽位累积。
    """
    text_parts: list[str] = []
    acc: dict[int, dict] = {}          # index -> {id, name, arguments}
    for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
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
    return text_parts, tool_calls


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
    max_iters = config_store.get()["agent"]["max_iters"]
    logger.info("agent 循环开始: sid=%s, max_iters=%d", sid, max_iters)
    try:
        for _ in range(max_iters):
            history = session_store._fit_context(session_store.read_messages(sid))
            # 发请求前剪掉悬空 tool_call(上一轮被中断留下的脏态),否则 provider 400 毒死会话
            history = _prune_dangling_tool_calls(history)
            messages = [{"role": "system", "content": SYSTEM_PROMPT}, *history]
            stream = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=registry.to_openai_schema(),
                stream=True,
            )
            # yield from:把 _accumulate 里的 text 事件原样透传给外层消费者,
            # 同时用它的 return 值拿到重组好的 (text_parts, tool_calls)。
            text_parts, tool_calls = yield from _accumulate(stream)

            if not tool_calls:
                session_store.append_message(
                    sid, {"role": "assistant", "content": "".join(text_parts)}
                )
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
