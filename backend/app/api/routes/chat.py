"""
routes/chat.py —— 单轮流式对话(P0;工具/会话在后续里程碑加)

POST /api/chat/stream  body {message}
返回 SSE:每行 `data: {json}\n\n`,事件类型 text / done / error。
core 与输出通道解耦的雏形:这里只把 openai 的流转成 typed event,后面 P2 再加 tool_call 等。
"""

import json
import logging

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.models import schemas
from app.services import llm

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chat", tags=["chat"])


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.post("/stream")
def chat_stream(req: schemas.ChatRequest) -> StreamingResponse:
    logger.info("chat 请求: msg_len=%d", len(req.message))  # 只记长度,不打全文

    def event_stream():
        try:
            client, model = llm.get_llm_client()
            stream = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": req.message}],
                stream=True,
            )
            for chunk in stream:
                delta = chunk.choices[0].delta.content if chunk.choices else None
                if delta:
                    yield _sse({"type": "text", "content": delta})
            yield _sse({"type": "done"})
        except Exception as e:  # noqa: BLE001 - 把错误当事件发给前端展示
            logger.warning("chat 失败: %s", type(e).__name__)
            yield _sse({"type": "error", "message": str(e)})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
