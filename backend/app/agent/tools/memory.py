"""
memory.py(tools 层)—— Agent 沉淀长期记忆的两个工具。

update_profile:写用户画像;update_soul:调整 Agent 自己的行为准则。
均为整份覆盖(不是追加)——旧内容已注入在 system prompt 里,Agent 直接可见,
先基于它合并再写回完整内容。执行体只调 memory service,自动放行(不走审批)。
"""
import logging

from pydantic import BaseModel, Field

from app.services import memory

logger = logging.getLogger(__name__)


class UpdateProfileArgs(BaseModel):
    content: str = Field(description=(
        "用户画像的完整新内容(整份覆盖,不是追加)。"
        "先基于已注入在 system 里的现有画像,合并后写回完整内容。"))


def update_profile(args: UpdateProfileArgs) -> str:
    memory.write_profile(args.content)
    return "已更新用户画像(profile)"


class UpdateSoulArgs(BaseModel):
    content: str = Field(description=(
        "Agent 准则的完整新内容(整份覆盖,不是追加)。"
        "先基于已注入在 system 里的现有准则,合并后写回完整内容。"))


def update_soul(args: UpdateSoulArgs) -> str:
    memory.write_soul(args.content)
    return "已更新 Agent 准则(soul)"


class AppendLogArgs(BaseModel):
    entry: str = Field(description=(
        "要追加到今天日志的一条记录:今天发生的具体事、做过的操作、遇到的坑、"
        "临时的上下文。一句话或一小段。这是流水账,不是长期画像。"))


def append_log(args: AppendLogArgs) -> str:
    memory.append_log(args.entry)
    return "已记入今天的日志"


class UpdateMemoryArgs(BaseModel):
    content: str = Field(description=(
        "长期记忆的完整新内容(整份覆盖)。"
        "先基于 system 里已注入的现有长期记忆合并,再写回完整内容。"))


def update_memory(args: UpdateMemoryArgs) -> str:
    memory.write_memory(args.content)
    return "已更新长期记忆(memory)"
