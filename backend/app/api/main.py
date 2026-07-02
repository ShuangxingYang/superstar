"""
app/api/main.py —— FastAPI 应用实例(整个后端的入口装配点)

职责:
  1. 创建 FastAPI 实例
  2. 配 CORS(允许前端跨域调用)
  3. 注册各个路由(routes/ 下的模块,后续里程碑逐个挂上来)
  4. 提供一个 /health 健康检查(确认服务活着)

现在是最小版:只有 /health。P1 后面会挂 settings 路由、chat 路由。
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Superstar Backend", version="0.1.0")

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


@app.get("/health")
def health():
    """健康检查:前端/运维用它确认后端活着。返回啥不重要,能返回就说明服务在跑。"""
    return {"status": "ok", "service": "superstar-backend"}
