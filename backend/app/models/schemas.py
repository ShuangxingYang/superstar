"""
schemas.py —— API 出入口的 Pydantic 模型(请求校验 + 响应塑形 + key 脱敏)

配置在 config_store 里是裸 dict(内部方便);API 边界要守门:
  - 入参:前端传来的更新请求,结构不对直接 422(Pydantic 自动校验)
  - 出参:返回配置时 api_key 必须脱敏(只回 sk-***1234),绝不吐明文
类比:这层就是给 HTTP 边界加了 TS interface + 运行时校验。
"""

from copy import deepcopy

from pydantic import BaseModel


# ---- 出参:GET /api/settings(key 已脱敏) ----
class LLMSettings(BaseModel):
    base_url: str = ""
    api_key: str = ""
    model: str = ""


class EmbeddingSettings(BaseModel):
    base_url: str = ""
    api_key: str = ""
    model: str = ""


class SecuritySettings(BaseModel):
    workspace_dir: str = ""
    kb_dir: str = ""
    cmd_whitelist: list[str] = []
    cmd_blacklist: list[str] = []


class AgentSettings(BaseModel):
    max_iters: int = 10
    temperature: float = 0.7


class AppConfig(BaseModel):
    llm: LLMSettings
    embedding: EmbeddingSettings
    security: SecuritySettings
    agent: AgentSettings


# ---- 入参:PUT /api/settings 局部更新(字段全可选,None=不改) ----
class LLMUpdate(BaseModel):
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None


class EmbeddingUpdate(BaseModel):
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None


class SecurityUpdate(BaseModel):
    workspace_dir: str | None = None
    kb_dir: str | None = None
    cmd_whitelist: list[str] | None = None
    cmd_blacklist: list[str] | None = None


class AgentUpdate(BaseModel):
    max_iters: int | None = None
    temperature: float | None = None


class ConfigUpdate(BaseModel):
    llm: LLMUpdate | None = None
    embedding: EmbeddingUpdate | None = None
    security: SecurityUpdate | None = None
    agent: AgentUpdate | None = None


# ---- 测试连接 ----
class TestConnectionRequest(BaseModel):
    base_url: str
    api_key: str
    model: str


class TestConnectionResult(BaseModel):
    ok: bool
    error: str = ""


# ---- 对话(P0 单轮;session 在 P1 引入) ----
class ChatRequest(BaseModel):
    message: str


# ---- key 脱敏 ----
def mask_key(key: str) -> str:
    """只保留末 4 位:sk-abcdef123456 -> sk-***3456;空串→空串;过短(≤4)→ ****。"""
    if not key:
        return ""
    if len(key) <= 4:
        return "****"
    return f"{key[:3]}***{key[-4:]}"


def to_masked_config(config: dict) -> AppConfig:
    """内部 dict → 响应模型,并对两个 api_key 脱敏。"""
    masked = deepcopy(config)
    masked["llm"]["api_key"] = mask_key(config["llm"].get("api_key", ""))
    masked["embedding"]["api_key"] = mask_key(config["embedding"].get("api_key", ""))
    return AppConfig(**masked)
