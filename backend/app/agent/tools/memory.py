"""
memory.py(tools 层)—— Agent 沉淀长期记忆的工具。

四种记忆按内容归类:update_profile(用户个人信息)、update_memory(客观事实/既定结论)、
update_soul(Agent 自身准则)、append_log(今天发生的具体事)。
前三者整份覆盖(旧内容已注入 system prompt,Agent 可见,合并后写回);append_log 追加。
执行体只调 memory service,自动放行(不走审批)。
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
