"""
schemas.py —— API 出入口的 Pydantic 模型(请求校验 + 响应塑形 + key 脱敏)

配置在 config_store 里是裸 dict(内部方便);API 边界要守门:
  - 入参:前端传来的更新请求,结构不对直接 422(Pydantic 自动校验)
  - 出参:返回配置时 api_key 必须脱敏(只回 sk-***1234),绝不吐明文
类比:这层就是给 HTTP 边界加了 TS interface + 运行时校验。
"""

from copy import deepcopy
from typing import Literal

from pydantic import BaseModel


# ---- 出参:GET /api/settings(key 已脱敏) ----
class LLMSettings(BaseModel):
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    reasoning_effort: str = ""   # 空=不传;low/medium/high 才让推理模型吐思考过程


# 配置预设:一套具名的 LLM 连接快照。复用 base_url/api_key/model + reasoning_effort + 一个显示名。
class LLMProfile(BaseModel):
    name: str = ""
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    reasoning_effort: str = ""   # 随预设走:切到推理模型的预设自动开思考,切回自动关


class EmbeddingSettings(BaseModel):
    base_url: str = ""
    api_key: str = ""
    model: str = ""


class SecuritySettings(BaseModel):
    default_cwd: str = ""
    allowed_dirs: list[str] = []
    kb_dir: str = "./data/kb"
    cmd_whitelist: list[str] = []
    cmd_blacklist: list[str] = []


class AgentSettings(BaseModel):
    max_iters: int = 10
    temperature: float = 0.7


class AppConfig(BaseModel):
    llm: LLMSettings
    llm_profiles: list[LLMProfile] = []
    embedding: EmbeddingSettings
    security: SecuritySettings
    agent: AgentSettings


# ---- 入参:PUT /api/settings 局部更新(字段全可选,None=不改) ----
class LLMUpdate(BaseModel):
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None


class EmbeddingUpdate(BaseModel):
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None


class SecurityUpdate(BaseModel):
    default_cwd: str | None = None
    allowed_dirs: list[str] | None = None
    kb_dir: str | None = None
    cmd_whitelist: list[str] | None = None
    cmd_blacklist: list[str] | None = None


class AgentUpdate(BaseModel):
    max_iters: int | None = None
    temperature: float | None = None


class ConfigUpdate(BaseModel):
    llm: LLMUpdate | None = None
    llm_profiles: list[LLMProfile] | None = None   # 传 = 整份替换(增删都回传全量数组)
    embedding: EmbeddingUpdate | None = None
    security: SecurityUpdate | None = None
    agent: AgentUpdate | None = None


# ---- 测试连接 ----
class TestConnectionRequest(BaseModel):
    base_url: str
    api_key: str
    model: str
    kind: Literal["llm", "embedding"] = "llm"   # 区分测哪种服务;前端按分区传


class TestConnectionResult(BaseModel):
    ok: bool
    error: str = ""


# ---- 对话(P1:带 session_id 多轮) ----
class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None   # 不传 = 懒创建;向后兼容 P0 老调用


# ---- 会话 ----
class SessionMeta(BaseModel):
    id: str
    title: str = ""
    created_at: str
    updated_at: str


class RenameRequest(BaseModel):
    title: str


# ---- 审批恢复(P2b) ----
class ResumeRequest(BaseModel):
    session_id: str
    tool_call_id: str
    decision: Literal["approve", "reject"]


# ---- key 脱敏(mask_key 仍保留:_drop_masked_keys 靠 *** 识别未改动的 key) ----
def mask_key(key: str) -> str:
    """只保留末 4 位:sk-abcdef123456 -> sk-***3456;空串→空串;过短(≤4)→ ****。"""
    if not key:
        return ""
    if len(key) <= 4:
        return "****"
    return f"{key[:3]}***{key[-4:]}"


def to_config_response(config: dict) -> AppConfig:
    """内部 dict → 响应模型。

    本地、单用户、自用工具:api_key 直接**明文**回传(前端默认密文展示,可点眼睛看明文,
    连通测试也能直接用真 key)。key 本就明文躺在本机 data/config.json 里,不额外增加泄露面。
    """
    return AppConfig(**deepcopy(config))
