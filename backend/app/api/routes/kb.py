"""
kb.py —— 知识库管理路由(上传/列表/删除/重建/状态)

上传:存文件到 kb_dir → rag_store.index_document。
RagStoreError(连不上/维度不一致)→ 503 + 明确 message,前端提示用户。

沙箱:上传文件名 / 删除 source 都可能含 ../ 越界,统一过 _safe_kb_path
(照 security.safe_path 同款「先 resolve 再判祖先」),钉死在 kb_dir 内。
"""

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile

from app.services import config_store, rag_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/kb", tags=["kb"])


def _kb_dir() -> Path:
    kb = config_store.get()["security"].get("kb_dir") or ""
    if not kb:
        raise HTTPException(
            status_code=400, detail="未配置知识库目录(kb_dir),请到设置页填写"
        )
    p = Path(kb).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _safe_kb_path(source: str) -> Path:
    """把 source 钉进 kb_dir 内;越界(../、绝对路径、软链接)→ 400。

    照 security.safe_path 的姿势:(root / source) 再 resolve,判它是不是 root 的后代。
    kb 沙箱根是 kb_dir(不是工作区允许根),所以不能直接复用 security.safe_path。
    """
    root = _kb_dir()
    target = (root / source).resolve()
    if target != root and root not in target.parents:
        logger.warning("知识库路径越界拦截: source=%s", source)
        raise HTTPException(
            status_code=400, detail=f"路径越界,超出知识库目录: {source}"
        )
    return target


@router.post("/upload")
async def upload(file: UploadFile):
    source = file.filename or "unnamed"
    dest = _safe_kb_path(source)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(await file.read())
    try:
        return rag_store.index_document(dest, source=source)
    except rag_store.RagStoreError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@router.get("/list")
def kb_list():
    try:
        return rag_store.list_documents()
    except rag_store.RagStoreError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@router.delete("/{source:path}")
def kb_delete(source: str):
    fp = _safe_kb_path(source)  # 先校验越界(会先于删库执行)
    try:
        n = rag_store.delete_document(source)
    except rag_store.RagStoreError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    # 删磁盘文件(存在才删)
    if fp.is_file():
        fp.unlink()
    return {"deleted": n}


@router.post("/rebuild")
def kb_rebuild():
    try:
        return rag_store.rebuild()
    except rag_store.RagStoreError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@router.get("/stats")
def kb_stats():
    try:
        return rag_store.stats()
    except rag_store.RagStoreError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
