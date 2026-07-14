"""
app/api/main.py —— FastAPI 应用实例(整个后端的入口装配点)

职责:
  1. 配置日志(启动时一次)
  2. 创建 FastAPI 实例
  3. 配 CORS(允许前端跨域调用)
  4. 注册各个路由(routes/ 下的模块,后续里程碑逐个挂上来)
  5. 提供一个 /health 健康检查(确认服务活着)

当前已挂:settings 路由、chat 路由。
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agent import scheduler
from app.api.routes import chat as chat_routes
from app.api.routes import kb as kb_routes
from app.api.routes import memory as memory_routes
from app.api.routes import session as session_routes
from app.api.routes import settings as settings_routes


def _setup_logging() -> None:
    """统一日志配置:级别 + 格式,程序启动时配一次。
    - level=INFO:放开各模块的 logger.info(默认级别是 WARNING,info 会被丢弃)
    - format 里的 %(name)s 就是各文件 getLogger(__name__) 的模块名,便于定位来源
    P0 先只输出到控制台(stderr);写文件/可配级别留到 P4 打磨。"""
    import logging

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


_setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.start_scheduler()   # 启动:按 config 决定是否注册 job
    yield
    scheduler.stop_scheduler()    # 关闭:停调度器


app = FastAPI(title="Superstar Backend", version="0.1.0", lifespan=lifespan)

# CORS: 前端(Vite dev 跑在 5173)和后端(8000)端口不同 = 跨域,
# 浏览器默认拦跨域请求,这里显式放行前端来源。
# 开发期先放宽,上线前应收紧到具体域名(现在本地自用,allow 本地即可)。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

# 注册路由(后续里程碑逐个挂上来)
app.include_router(settings_routes.router)
app.include_router(chat_routes.router)
app.include_router(session_routes.router)
app.include_router(kb_routes.router)
app.include_router(memory_routes.router)


@app.get("/health")
def health():
    """健康检查:前端/运维用它确认后端活着。返回啥不重要,能返回就说明服务在跑。"""
    return {"status": "ok", "service": "superstar-backend"}
