"""
workspace.py —— agent 动态增删可访问目录(白名单)。

add 走审批(由 gate 判定),remove 自动放行(收权无害)。执行体只管读写 config.security.allowed_dirs;
路径统一 expanduser().resolve() 成绝对路径后落库,消除 ~/.. 歧义(审批预览也展示绝对路径)。
"""
import logging
from pathlib import Path

from pydantic import BaseModel, Field

from app.services import config_store

logger = logging.getLogger(__name__)


class AddWorkspaceArgs(BaseModel):
    path: str = Field(description="要加入可访问白名单的目录(绝对路径)")


def add_workspace(args: AddWorkspaceArgs) -> str:
    abs_path = str(Path(args.path).expanduser().resolve())
    dirs = list(config_store.get()["security"].get("allowed_dirs") or [])
    if abs_path not in dirs:                         # 去重:已在白名单就不重复追加
        dirs.append(abs_path)
        config_store.update({"security": {"allowed_dirs": dirs}})
    logger.info("加入可访问目录: %s", abs_path)
    return f"已加入可访问目录:{abs_path}"


class RemoveWorkspaceArgs(BaseModel):
    path: str = Field(description="要从白名单移除的目录(绝对路径)")


def remove_workspace(args: RemoveWorkspaceArgs) -> str:
    abs_path = str(Path(args.path).expanduser().resolve())
    dirs = list(config_store.get()["security"].get("allowed_dirs") or [])
    if abs_path in dirs:
        dirs.remove(abs_path)
        config_store.update({"security": {"allowed_dirs": dirs}})
        logger.info("移除可访问目录: %s", abs_path)
        return f"已移除可访问目录:{abs_path}"
    return f"目录不在白名单中(无需移除):{abs_path}"
