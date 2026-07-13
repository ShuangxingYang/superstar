"""
subagent.py —— 子 Agent 引擎(隔离上下文跑一个子任务)。

主 Agent 通过 dispatch_subagent 工具派发;这里同步跑一个独立的 function calling 循环:
独立 messages(只有 system + task,看不到父会话)、只给读写子集工具、非流式、跑到底、
纯内存(不落盘)。最终把一段结论字符串交回主 Agent(它看不到中间过程)。

与主循环(loop.run_agent_streaming)的差异都是有意的:
  - 非流式:子 Agent 不往前端透传,直接拿完整 msg.tool_calls,不用复用主循环的碎片重组。
  - 无审批:白名单里没有需审批的工具(write 已改 auto),故永不进暂停路径。
  - 无落盘:messages 只活在内存,跑完即焚。
  - 递归防护:dispatch_subagent 不在白名单 → 子 Agent 拿不到它 → 不会派孙 Agent。
"""
import json
import logging

from app.agent.tools import registry
from app.services import config_store, llm

logger = logging.getLogger(__name__)

# 子 Agent 只给读写子集:能读/搜/写,不能跑命令(灰名单要审批,子 Agent 无暂停能力)、
# 不能扩权(add_workspace)、不能派孙 Agent(dispatch_subagent)。
SUBAGENT_TOOLS = {"read_file", "grep", "glob", "search_kb", "write_file"}

SUBAGENT_SYSTEM_PROMPT = (
    "你是一个子 Agent,被主 Agent 派来独立完成一个具体的子任务。"
    "你能读取、检索和写文件(read_file/grep/glob/search_kb/write_file),"
    "但不能跑命令,也不能改变可访问目录。"
    "专注完成任务,完成后用一段清晰、自足的文字把结论/所做改动汇总返回——"
    "这段汇总会直接交回主 Agent,它看不到你的中间过程,所以要说清你查到了什么、改了哪些文件,"
    "带上关键路径/行号/来源。"
    "如果任务需要跑命令,你没有这个工具,不要尝试;把「建议执行什么命令」写进结论,交给主 Agent。"
)


def _safe_json(raw: str | None) -> dict:
    """解析 tool_call arguments;非法/空 → {}(交给 registry.run 的 Pydantic 校验自愈)。"""
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}


def run_subagent(task: str) -> str:
    """独立上下文跑一个子任务,同步跑到底,返回给主 Agent 的结论字符串。绝不向上抛。"""
    try:
        client, model = llm.get_llm_client()
        max_iters = config_store.get()["agent"]["max_iters"]     # 复用父的步数上限
        schema = registry.to_openai_schema(SUBAGENT_TOOLS)       # 只给读写子集
        messages = [
            {"role": "system", "content": SUBAGENT_SYSTEM_PROMPT},
            {"role": "user", "content": task},                   # 隔离:只有 task,看不到父会话
        ]
        logger.info("子 Agent 开始: task_len=%d, max_iters=%d", len(task), max_iters)
        for i in range(max_iters):
            resp = client.chat.completions.create(model=model, messages=messages, tools=schema)
            msg = resp.choices[0].message
            if not msg.tool_calls:                               # 不调工具了 → 这就是结论
                result = (msg.content or "").strip()
                logger.info("子 Agent 完成: 迭代=%d, 结论长=%d", i + 1, len(result))
                return result or "(子 Agent 未产出结论)"
            messages.append(msg.model_dump(exclude_none=True))   # 完整回灌 assistant(含 tool_calls)
            for tc in msg.tool_calls:
                name = tc.function.name
                if name in SUBAGENT_TOOLS:
                    out = registry.run(name, _safe_json(tc.function.arguments))
                else:                                            # 双保险:幻觉调越权工具 → 喂回自愈
                    out = f"错误:子 Agent 只能用工具 {sorted(SUBAGENT_TOOLS)},不能调用 {name}"
                    logger.warning("子 Agent 越权工具: name=%s", name)
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": out})
        logger.info("子 Agent 达到 max_iters: task_len=%d", len(task))
        return "(子 Agent 达到最大步数仍未得出结论,收集的信息可能不完整)"
    except Exception as e:  # noqa: BLE001 - 子 Agent 任何失败都收敛成给父的字符串,父循环永不崩
        logger.warning("子 Agent 执行失败: err=%s", type(e).__name__)
        return f"(子 Agent 执行失败:{e})"
