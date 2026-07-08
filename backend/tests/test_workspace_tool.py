"""add_workspace / remove_workspace:写白名单去重 + 幂等移除,执行体只读写 config。"""
from app.services import config_store
from app.agent.tools import workspace


def test_add_workspace_appends_dedup(tmp_path, monkeypatch):
    store = {"security": {"default_cwd": str(tmp_path), "allowed_dirs": []}}
    monkeypatch.setattr(config_store, "get", lambda: store)
    monkeypatch.setattr(config_store, "update",
        lambda p: store["security"].__setitem__("allowed_dirs", p["security"]["allowed_dirs"]) or store)
    d = tmp_path / "proj"
    d.mkdir()
    workspace.add_workspace(workspace.AddWorkspaceArgs(path=str(d)))
    assert str(d.resolve()) in store["security"]["allowed_dirs"]
    workspace.add_workspace(workspace.AddWorkspaceArgs(path=str(d)))          # 再加一次 → 去重
    assert store["security"]["allowed_dirs"].count(str(d.resolve())) == 1


def test_remove_workspace_idempotent(tmp_path, monkeypatch):
    d = tmp_path / "proj"
    d.mkdir()
    store = {"security": {"default_cwd": str(tmp_path), "allowed_dirs": [str(d.resolve())]}}
    monkeypatch.setattr(config_store, "get", lambda: store)
    monkeypatch.setattr(config_store, "update",
        lambda p: store["security"].__setitem__("allowed_dirs", p["security"]["allowed_dirs"]) or store)
    workspace.remove_workspace(workspace.RemoveWorkspaceArgs(path=str(d)))
    assert str(d.resolve()) not in store["security"]["allowed_dirs"]
    # 移除不存在的项:幂等,不炸,给出提示
    out = workspace.remove_workspace(workspace.RemoveWorkspaceArgs(path=str(d)))
    assert "无需移除" in out


def test_add_workspace_registered():
    from app.agent.tools import registry
    names = {s["function"]["name"] for s in registry.to_openai_schema()}
    assert "add_workspace" in names and "remove_workspace" in names
