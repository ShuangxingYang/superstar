from app.models import schemas


def test_mask_key():
    assert schemas.mask_key("") == ""
    assert schemas.mask_key("ab") == "****"
    assert schemas.mask_key("sk-abcdef123456") == "sk-***3456"


def test_to_masked_config_hides_keys():
    cfg = {
        "llm": {"base_url": "u", "api_key": "sk-abcdef123456", "model": "m"},
        "embedding": {"base_url": "u2", "api_key": "sk-zzzz9999", "model": "e"},
        "security": {"workspace_dir": "", "kb_dir": "", "cmd_whitelist": [], "cmd_blacklist": []},
        "agent": {"max_iters": 10, "temperature": 0.7},
    }
    out = schemas.to_masked_config(cfg)
    assert out.llm.api_key == "sk-***3456"
    assert out.embedding.api_key == "sk-***9999"
    assert out.llm.model == "m"          # 非 key 字段原样


def test_config_update_all_optional():
    # 只传一个字段应能通过校验(局部更新)
    u = schemas.ConfigUpdate(llm=schemas.LLMUpdate(model="ep-x"))
    dumped = u.model_dump(exclude_none=True)
    assert dumped == {"llm": {"model": "ep-x"}}
