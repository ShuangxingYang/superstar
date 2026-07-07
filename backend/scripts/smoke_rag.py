"""
手动冒烟(不进 pytest):真起 Qdrant + 真 embedding key,验证端到端灌库→检索。
前置:docker start qdrant;设置页/config.json 配好 embedding 的 api_key;
     security.kb_dir 指向一个放了文档的目录。
运行:cd backend && uv run python scripts/smoke_rag.py

真连外部服务,任一未就绪会抛 RagStoreError;这里兜成一行友好提示,不丢 traceback。
"""
from pathlib import Path

from app.services import rag_store

if __name__ == "__main__":
    try:
        print("stats(初始):", rag_store.stats())
        sample = Path("scripts/_sample.md")
        sample.write_text("ReAct 是推理与行动结合的 Agent 范式。CoT 是思维链。", encoding="utf-8")
        print("灌库:", rag_store.index_document(sample, source="_sample.md"))
        print("列表:", rag_store.list_documents())
        print("检索『什么是ReAct』:")
        for text, source, score in rag_store.search("什么是ReAct"):
            print(f"  [{score:.3f}] {text}  [来源: {source}]")
        print("检索『库里没有的东西·股票代码』:", rag_store.search("腾讯股票代码是多少"))
        print("删除:", rag_store.delete_document("_sample.md"))
        sample.unlink(missing_ok=True)
    except rag_store.RagStoreError as e:
        print(f"[冒烟失败] {e}")
        print("请检查:1) docker 里 qdrant 起了吗;2) 设置页 embedding 的 api_key/model 配了吗。")
