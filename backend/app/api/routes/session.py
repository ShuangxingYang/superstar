"""
routes/session.py —— 会话 CRUD(P1)

懒创建:没有 POST /sessions,新建发生在首句 /chat/stream。这里只管列表/读历史/重命名/删除。
"""
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.models import schemas
from app.services import session_store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.get("")
def list_sessions() -> list[schemas.SessionMeta]:
    return [schemas.SessionMeta(**s) for s in session_store.list_sessions()]


@router.get("/{sid}")
def get_session(sid: str) -> dict:
    """切会话时前端拉历史铺进消息流。"""
    try:
        return {"messages": session_store.read_messages(sid)}
    except session_store.SessionNotFound:
        raise HTTPException(status_code=404, detail="session not found")


@router.patch("/{sid}")
def rename_session(sid: str, req: schemas.RenameRequest) -> schemas.SessionMeta:
    try:
        session_store.rename(sid, req.title)
    except session_store.SessionNotFound:
        raise HTTPException(status_code=404, detail="session not found")
    meta = next(s for s in session_store.list_sessions() if s["id"] == sid)
    return schemas.SessionMeta(**meta)


@router.delete("/{sid}", status_code=204)
def delete_session(sid: str) -> Response:
    try:
        session_store.delete(sid)
    except session_store.SessionNotFound:
        raise HTTPException(status_code=404, detail="session not found")
    return Response(status_code=204)
