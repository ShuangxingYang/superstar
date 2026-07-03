"""
routes/chat.py —— 流式对话(P1:接 session,多轮 + 懒创建持久化)

POST /api/chat/stream  body {session_id?, message}
SSE 事件:session / text / done / error。
时序:定 sid(无则懒创建)→ 落 user 消息 → 发 session 事件 → 读历史喂模型
     → 流 text 并累积 → 正常收尾落 assistant + done;异常发 error 且 assistant 不落盘。
"""
import json
import logging

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.models import schemas
from app.services import llm, session_store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chat", tags=["chat"])


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.post("/stream")
def chat_stream(req: schemas.ChatRequest) -> StreamingResponse:
    logger.info("chat 请求: msg_len=%d, has_sid=%s", len(req.message), bool(req.session_id))

    def event_stream():
        # 定 sid:带 sid 则续写,不带则懒创建(首句到达才落盘,像 ChatGPT 不产生空会话)
        sid = req.session_id or session_store.create()
        try:
            # 先落用户消息:哪怕模型挂了也不丢用户输入(首条会顺带生成标题)
            session_store.append_message(sid, {"role": "user", "content": req.message})
            title = next((s["title"] for s in session_store.list_sessions() if s["id"] == sid), "")
            # session 事件必须在 text 之前发:前端据此记住新 sid、刷新列表标题
            yield _sse({"type": "session", "session_id": sid, "title": title})

            # 全量喂历史(含刚落的 user);_fit_context 是 M12 裁剪钩子,P1 原样返回
            history = session_store._fit_context(session_store.read_messages(sid))
            client, model = llm.get_llm_client()
            stream = client.chat.completions.create(model=model, messages=history, stream=True)
            parts: list[str] = []
            for chunk in stream:
                delta = chunk.choices[0].delta.content if chunk.choices else None
                if delta:
                    parts.append(delta)  # 累积全文,收尾一次性落盘
                    yield _sse({"type": "text", "content": delta})
            # 流正常结束才落 assistant(避免存半截);异常路径不落,下轮重发即可
            session_store.append_message(sid, {"role": "assistant", "content": "".join(parts)})
            yield _sse({"type": "done"})
        except Exception as e:  # noqa: BLE001 - 错误当事件发给前端展示
            logger.warning("chat 失败: sid=%s err=%s", sid, type(e).__name__)
            yield _sse({"type": "error", "message": str(e)})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
