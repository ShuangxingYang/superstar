"""
run.py —— 开发启动入口

跑 `uv run run.py` 就能起后端。等价于命令行 `uvicorn app.api.main:app --reload`,
但写成脚本更省事(还能从 settings 读 host/port)。

reload=True: 改代码自动重启(开发期方便;生产要关掉)。
"""

import uvicorn

from app.config import settings

if __name__ == "__main__":
    uvicorn.run(
        "app.api.main:app",   # "模块路径:FastAPI实例名",uvicorn 按这个找到 app
        host=settings.host,
        port=settings.port,
        reload=True,
    )
