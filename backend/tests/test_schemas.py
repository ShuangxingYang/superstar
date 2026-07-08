from app.models import schemas


def test_mask_key():
    assert schemas.mask_key("") == ""
    assert schemas.mask_key("ab") == "****"
    assert schemas.mask_key("sk-abcdef123456") == "sk-***3456"


def test_to_masked_config_hides_keys():
    cfg = {
        "llm": {"base_url": "u", "api_key": "sk-abcdef123456", "model": "m"},
        "embedding": {"base_url": "u2", "api_key": "sk-zzzz9999", "model": "e"},
        "security": {"default_cwd": "", "allowed_dirs": [], "kb_dir": "",
                     "cmd_whitelist": [], "cmd_blacklist": []},
        "agent": {"max_iters": 10, "temperature": 0.7},
    }
    out = schemas.to_masked_config(cfg)
    assert out.llm.api_key == "sk-***3456"
    assert out.embedding.api_key == "sk-***9999"
    assert out.llm.model == "m"          # 非 key 字段原样


def test_security_settings_roundtrip():
    cfg = schemas.to_masked_config({
        "llm": {"base_url": "u", "api_key": "sk-abcdef123456", "model": "m"},
        "embedding": {"base_url": "u2", "api_key": "sk-zzzz9999", "model": "e"},
        "security": {"default_cwd": "~/.superstar", "allowed_dirs": ["/tmp"],
                     "kb_dir": "", "cmd_whitelist": [], "cmd_blacklist": []},
        "agent": {"max_iters": 10, "temperature": 0.7},
        "rag": {"chunk_size": 500, "overlap": 80, "top_n": 20, "top_k": 5, "rerank_model": "gte-rerank"},
    })
    assert cfg.security.default_cwd == "~/.superstar"
    assert cfg.security.allowed_dirs == ["/tmp"]


def test_test_connection_request_kind_default():
    # kind 默认 llm;可显式指定 embedding
    assert schemas.TestConnectionRequest(base_url="u", api_key="k", model="m").kind == "llm"
    r = schemas.TestConnectionRequest(base_url="u", api_key="k", model="m", kind="embedding")
    assert r.kind == "embedding"


def test_config_update_all_optional():
    # 只传一个字段应能通过校验(局部更新)
    u = schemas.ConfigUpdate(llm=schemas.LLMUpdate(model="ep-x"))
    dumped = u.model_dump(exclude_none=True)
    assert dumped == {"llm": {"model": "ep-x"}}


def test_chat_request_session_id_optional():
    assert schemas.ChatRequest(message="hi").session_id is None      # 不传 → None(向后兼容)
    assert schemas.ChatRequest(message="hi", session_id="ab").session_id == "ab"


def test_session_meta_and_rename():
    m = schemas.SessionMeta(id="ab", created_at="t0", updated_at="t1")
    assert m.title == "" and m.id == "ab"                            # title 默认空
    assert schemas.RenameRequest(title="新名").title == "新名"
