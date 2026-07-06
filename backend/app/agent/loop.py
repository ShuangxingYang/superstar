"""
loop.py —— function calling 流式循环(P2a 引擎,Agent 的「大脑」)

职责:喂会话历史(带工具 schema)给模型 → 流式收 delta → 重组分片的 tool_calls →
调注册表执行工具 → 结果喂回 → 再问,直到模型不再调工具(给终答)或到达 max_iters。
产 typed event(text/tool_call/tool_result/done/error),与输出通道解耦——
chat 路由把 event 原样转 SSE,二期飞书适配器可消费同样的 event。
"""
import json
import logging

from app.agent.tools import registry
from app.services import config_store, llm, session_store

logger = logging.getLogger(__name__)

# 极简 system:告诉模型有工具、大致职责。完整画像/soul 注入留 P5。
SYSTEM_PROMPT = (
    "你是一个本地编码助手,可以调用工具查看用户工作区里的代码:"
    "grep(按正则搜索)、glob(按通配列文件)、read_file(读文件)。"
    "需要看代码再作答时就调用工具;能直接回答的问题不必调用。"
)


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


def run_agent_streaming(sid: str):
    """喂该会话历史,跑 function calling 循环,逐步 yield typed event。"""
    client, model = llm.get_llm_client()
    max_iters = config_store.get()["agent"]["max_iters"]
    logger.info("agent 循环开始: sid=%s, max_iters=%d", sid, max_iters)
    try:
        for _ in range(max_iters):
            history = session_store._fit_context(session_store.read_messages(sid))
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

            # 协议要求:先落一条带 tool_calls 的 assistant 消息(content 可为 None)。
            # 「未回答的 tool_call」天然表示暂停 —— P2b 审批的回合边界正落在这。
            session_store.append_message(
                sid,
                {
                    "role": "assistant",
                    "content": "".join(text_parts) or None,
                    "tool_calls": tool_calls,
                },
            )
            for tc in tool_calls:
                name = tc["function"]["name"]
                raw = tc["function"]["arguments"]
                yield {"type": "tool_call", "id": tc["id"], "name": name, "args": raw}
                try:
                    parsed = json.loads(raw) if raw else {}
                except json.JSONDecodeError:
                    parsed = {}          # 参数不是合法 JSON → 交给 registry 自愈(返回参数错误)
                result = registry.run(name, parsed)
                yield {"type": "tool_result", "id": tc["id"], "result": result}
                session_store.append_message(
                    sid, {"role": "tool", "tool_call_id": tc["id"], "content": result}
                )
            # 回到 for 顶:带工具结果再问模型
        logger.info("agent 循环到达 max_iters: sid=%s", sid)
        yield {"type": "error", "message": "达到最大步数,已停止"}
    except Exception as e:  # noqa: BLE001 - 未预期异常 → 兜成 error 事件,已流出的内容保留
        logger.warning("agent 循环失败: sid=%s err=%s", sid, type(e).__name__)
        yield {"type": "error", "message": str(e)}
