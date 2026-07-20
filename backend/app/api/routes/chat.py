"""
routes/chat.py —— 流式对话(P2a:退化成「定 sid + 落 user + 转发 loop 事件」)

POST /api/chat/stream  body {session_id?, message}
SSE 事件:session / text / tool_call / tool_result / done / error。
时序:定 sid(无则懒创建)→ 落 user 消息 → 发 session 事件 → 把 loop 产的 event 原样转 SSE。
真正的 function calling 循环、工具执行、落盘都在 agent/loop.py;路由只做通道适配。
"""
import asyncio
import json
import logging

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.agent import loop
from app.agent import pending as pending_store
from app.models import schemas
from app.services import session_store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chat", tags=["chat"])


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.post("/stream")
def chat_stream(req: schemas.ChatRequest) -> StreamingResponse:
    logger.info("chat 请求: msg_len=%d, has_sid=%s", len(req.message), bool(req.session_id))

    async def event_stream():
        # 定 sid:带 sid 则续写,不带则懒创建(首句到达才落盘,不产生空会话)
        sid = req.session_id or await asyncio.to_thread(session_store.create)
        try:
            # 残留 pending 防护:审批未决却发了新消息 → 先自动拒绝收尾,避免悬空毒死会话
            if req.session_id and await asyncio.to_thread(pending_store.read, sid):
                await loop.reject_all_pending(sid)
            # 先落用户消息:哪怕模型挂了也不丢输入(首条会顺带生成标题)
            await asyncio.to_thread(session_store.append_message, sid, {"role": "user", "content": req.message})
            sessions = await asyncio.to_thread(session_store.list_sessions)
            title = next((s["title"] for s in sessions if s["id"] == sid), "")
            # session 事件必须在 text 之前:前端据此记住新 sid、刷新列表标题
            yield _sse({"type": "session", "session_id": sid, "title": title})
            # 循环产啥,原样转 SSE(text/tool_call/tool_result/done/error 都自动透传)
            async for event in loop.run_agent_streaming(sid):
                yield _sse(event)
        except Exception as e:  # noqa: BLE001 - 兜底:错误也当事件发给前端展示
            logger.warning("chat 失败: sid=%s err=%s", sid, type(e).__name__)
            yield _sse({"type": "error", "message": str(e)})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/resume")
def chat_resume(req: schemas.ResumeRequest) -> StreamingResponse:
    """人在环路审批的恢复:批准/拒绝一个待审批的工具调用,续跑循环。"""
    logger.info("resume 请求: sid=%s, id=%s, decision=%s",
                req.session_id, req.tool_call_id, req.decision)

    async def event_stream():
        try:
            async for event in loop.resume_streaming(req.session_id, req.tool_call_id, req.decision):
                yield _sse(event)
        except Exception as e:  # noqa: BLE001 - 兜底:错误也当事件发给前端展示
            logger.warning("resume 失败: sid=%s err=%s", req.session_id, type(e).__name__)
            yield _sse({"type": "error", "message": str(e)})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
