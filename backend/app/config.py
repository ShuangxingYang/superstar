"""
config.py —— 启动必需的配置(用 pydantic-settings 从环境变量/.env 读)

【这里只放"启动就要用、且很少变"的配置】: 端口、数据文件目录、Qdrant 地址。
【业务配置】(LLM/embedding 的 key、命令白黑名单等)不放这儿——它们要能在
前端配置页随时改、热生效,存在 data/config.json 里(仿 Claude Code 的 ~/.claude/*.json、
OpenClaw 的 ~/.openclaw/openclaw.json),到 1.3/1.4 再做。个人单用户项目,配置就十几项,
用 JSON 文件足够,不上数据库。

pydantic-settings 会自动:
  - 从环境变量读(名字大小写不敏感)
  - 从 .env 文件读
  - 按类型注解校验(端口写成非数字会直接报错,不会等到运行时才炸)
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # model_config: 告诉 pydantic-settings 去哪读、怎么读
    model_config = SettingsConfigDict(
        env_file=".env",  # 从 backend/.env 读(没有也不报错)
        env_file_encoding="utf-8",
        extra="ignore",  # .env 里有多余的键就忽略,不报错
    )

    # 服务监听地址/端口
    host: str = "127.0.0.1"
    port: int = 8000

    # 运行时数据目录(存 config.json / 会话 jsonl / 记忆 md)。相对 backend/ 目录
    data_dir: str = "./data"

    # Qdrant 地址(P3 做 RAG 时才真正用到,先占位)
    qdrant_url: str = "http://localhost:6333"


# 全局单例:整个后端共用这一份配置。import settings 就能拿到。
settings = Settings()
