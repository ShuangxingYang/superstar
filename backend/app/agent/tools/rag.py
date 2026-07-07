"""
rag.py —— search_kb 工具(Agent 查资料的手)

只做检索:调 rag_store.search 拿 top-k 片段,拼成带来源的文本喂回。
A+G(基于片段生成答案)交主循环 LLM——工具是"手"只取数据,不自己调 LLM。
RagStoreError(连不上/维度不一致)由 ToolRegistry 自愈兜成 tool_result,不崩流。
"""
from pydantic import BaseModel, Field

from app.services import rag_store


class SearchKbArgs(BaseModel):
    query: str = Field(description="要在知识库里检索的问题或关键词")
    top_k: int = Field(default=5, description="返回最相关的前几条片段")


def search_kb(args: SearchKbArgs) -> str:
    results = rag_store.search(args.query, top_k=args.top_k)
    if not results:
        return "知识库里没有相关内容。"
    blocks = [f"【片段{i}】{text}\n[来源: {source}]" for i, (text, source, _score) in enumerate(results, 1)]
    return "\n\n".join(blocks)
