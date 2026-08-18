"""P6-2 Obsidian 关联测试：status / link（冲突校验 + 可选复制）/ open（Obsidian / explorer 降级）。

注意：obsidian.py 读写的是模块级 DEFAULT_CONFIG_PATH——测试里 monkeypatch 到
tmp_path，避免污染真实 config/settings.json。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


def _isolate_settings(monkeypatch, tmp_path, raw=None):
    """把 obsidian.py 的 settings.json 读写路径指向 tmp_path。"""
    cfg_path = tmp_path / "settings.json"
    cfg_path.write_text(json.dumps(raw or {
        "server": {"host": "127.0.0.1", "port": 8800},
        "paths": {"data_dir": "data", "vault_dir": str(tmp_path / "vault")},
        "modelscope": {},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    monkeypatch.setattr("app.api.obsidian.DEFAULT_CONFIG_PATH", cfg_path)
    return cfg_path


# ---------------- GET /api/obsidian/status ----------------

def test_status_default(client):
    r = client.get("/api/obsidian/status")
    assert r.status_code == 200
    d = r.json()
    # client fixture 的 vault = tmp/vault（已由 build_state ensure_dirs 创建）
    assert d["exists"] is True
    assert d["vault_dir"] == str(Path(client.app.state.library.cfg.paths.vault_dir).absolute())
    assert d["is_managed"] is False          # tmp vault ≠ 项目内置 vault
    assert d["is_obsidian_vault"] is False   # 没有 .obsidian
    assert d["books_exists"] is False
    assert d["books_is_library"] is True     # 无 books/ → 无冲突
    assert isinstance(d["obsidian_installed"], bool)
    assert d["obsidian_uri"].startswith("obsidian://open?path=")
    assert d["default_vault_dir"]
    assert set(d["content_dirs"]) == {"books", "catalog", "skills", "pending"}


def test_status_managed_vault(client, monkeypatch, tmp_path):
    """vault 指向项目内置 vault（ROOT/vault）→ is_managed True。"""
    import app.api.obsidian as obs
    vault = tmp_path / "vault"
    monkeypatch.setattr(obs, "ROOT_DIR", tmp_path)          # 假装 ROOT_DIR=tmp_path
    client.app.state.library.cfg.paths.vault_dir = vault    # 与 ROOT/vault 相等
    vault.mkdir(parents=True, exist_ok=True)
    d = client.get("/api/obsidian/status").json()
    assert d["is_managed"] is True


def test_status_obsidian_vault_detected(client):
    """目标目录含 .obsidian/ → is_obsidian_vault True。"""
    vault = Path(client.app.state.library.cfg.paths.vault_dir)
    (vault / ".obsidian").mkdir(parents=True, exist_ok=True)
    d = client.get("/api/obsidian/status").json()
    assert d["is_obsidian_vault"] is True


# ---------------- POST /api/obsidian/link ----------------

def test_link_new_dir(client, monkeypatch, tmp_path):
    """关联新目录：目录自动创建、settings.json 写绝对路径、运行中 cfg 同步。"""
    cfg_path = _isolate_settings(monkeypatch, tmp_path)
    target = tmp_path / "my_obsidian_vault"
    r = client.post("/api/obsidian/link", json={"vault_dir": str(target)})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] is True
    assert target.is_dir()
    # settings.json paths.vault_dir 更新（绝对路径）
    raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert raw["paths"]["vault_dir"] == str(target.absolute())
    # 运行中 cfg 同步
    assert Path(client.app.state.library.cfg.paths.vault_dir).absolute() == target.absolute()
    # 响应带新状态
    assert d["status"]["vault_dir"] == str(target.absolute())


def test_link_missing_path(client, monkeypatch, tmp_path):
    _isolate_settings(monkeypatch, tmp_path)
    r = client.post("/api/obsidian/link", json={"vault_dir": "   "})
    assert r.status_code == 400
    assert "请填写" in r.json()["detail"]


def test_link_conflict_foreign_books(client, monkeypatch, tmp_path):
    """目标 books/ 已存在且非本项目结构 → 400 拒绝（避免覆盖主人笔记）。"""
    _isolate_settings(monkeypatch, tmp_path)
    target = tmp_path / "note_vault"
    (target / "books" / "my-notes").mkdir(parents=True)
    r = client.post("/api/obsidian/link", json={"vault_dir": str(target)})
    assert r.status_code == 400
    assert "books" in r.json()["detail"]
    # 配置未变
    assert Path(client.app.state.library.cfg.paths.vault_dir).name == "vault"


def test_link_allow_library_books(client, monkeypatch, tmp_path):
    """目标 books/ 有本项目楼层结构（1F-…）→ 允许关联。"""
    _isolate_settings(monkeypatch, tmp_path)
    target = tmp_path / "obs_vault"
    (target / "books" / "1F-电子书" / "机器学习").mkdir(parents=True)
    r = client.post("/api/obsidian/link", json={"vault_dir": str(target)})
    assert r.status_code == 200, r.text
    assert Path(client.app.state.library.cfg.paths.vault_dir).absolute() == target.absolute()


def test_link_copy_existing(client, monkeypatch, tmp_path):
    """copy_existing=True → 复制 books/catalog/skills/pending；不覆盖目标已有文件。"""
    _isolate_settings(monkeypatch, tmp_path)
    src = Path(client.app.state.library.cfg.paths.vault_dir)
    (src / "books" / "1F-电子书" / "机器学习").mkdir(parents=True)
    (src / "books" / "1F-电子书" / "README.md").write_text("# 电子书\n", encoding="utf-8")
    (src / "books" / "1F-电子书" / "机器学习" / "book.md").write_text("正文", encoding="utf-8")
    (src / "catalog").mkdir()
    (src / "catalog" / "bk_1.md").write_text("card", encoding="utf-8")
    (src / "skills").mkdir()
    (src / "skills" / "demo" / "SKILL.md").parent.mkdir(parents=True)
    (src / "skills" / "demo" / "SKILL.md").write_text("---\nname: demo\n---", encoding="utf-8")

    target = tmp_path / "obs_vault"
    # 目标已有同名文件（主人内容）→ 复制时不应覆盖
    (target / "books" / "1F-电子书").mkdir(parents=True)
    (target / "books" / "1F-电子书" / "README.md").write_text("主人自写", encoding="utf-8")

    r = client.post("/api/obsidian/link", json={
        "vault_dir": str(target), "copy_existing": True})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["copied"]["books"] == 1           # 仅 book.md（README.md 已存在跳过）
    assert d["copied"]["catalog"] == 1
    assert d["copied"]["skills"] == 1
    # 复制结果：新文件出现
    assert (target / "catalog" / "bk_1.md").read_text(encoding="utf-8") == "card"
    assert (target / "skills" / "demo" / "SKILL.md").exists()
    assert (target / "books" / "1F-电子书" / "机器学习" / "book.md").read_text(encoding="utf-8") == "正文"
    # 目标已有文件未被覆盖（AI 不覆盖主人手动摆放）
    assert (target / "books" / "1F-电子书" / "README.md").read_text(encoding="utf-8") == "主人自写"


def test_link_no_copy_by_default(client, monkeypatch, tmp_path):
    """默认不复制：copy_existing 缺省 False。"""
    _isolate_settings(monkeypatch, tmp_path)
    src = Path(client.app.state.library.cfg.paths.vault_dir)
    (src / "books" / "1F-电子书").mkdir(parents=True)
    target = tmp_path / "obs_vault"
    r = client.post("/api/obsidian/link", json={"vault_dir": str(target)})
    assert r.status_code == 200, r.text
    assert r.json()["copied"] == {}
    assert not (target / "books").exists()


# ---------------- POST /api/obsidian/open ----------------

def test_open_obsidian_installed(client, monkeypatch, tmp_path):
    """已注册 obsidian:// → webbrowser.open(URI)，返回 opened=obsidian。"""
    _isolate_settings(monkeypatch, tmp_path)
    import app.api.obsidian as obs
    opened = []
    monkeypatch.setattr(obs, "_obsidian_installed", lambda: True)
    monkeypatch.setattr(obs.webbrowser, "open", lambda url: opened.append(url))
    r = client.post("/api/obsidian/open")
    assert r.status_code == 200
    d = r.json()
    assert d["opened"] == "obsidian"
    assert opened and opened[0].startswith("obsidian://open?path=")


def test_open_fallback_explorer(client, monkeypatch, tmp_path):
    """未装 Obsidian → 降级资源管理器打开文件夹（Windows os.startfile）。"""
    _isolate_settings(monkeypatch, tmp_path)
    import app.api.obsidian as obs
    started = []
    monkeypatch.setattr(obs, "_obsidian_installed", lambda: False)
    monkeypatch.setattr(os, "startfile", lambda p: started.append(p), raising=False)
    r = client.post("/api/obsidian/open")
    assert r.status_code == 200
    d = r.json()
    assert d["opened"] == "explorer"
    assert len(started) == 1
    assert "未检测到 Obsidian" in d["message"]


def test_open_uri_fallback(client, monkeypatch, tmp_path):
    """非 Windows（无 os.startfile）→ 只返回 URI。"""
    _isolate_settings(monkeypatch, tmp_path)
    import app.api.obsidian as obs
    monkeypatch.setattr(obs, "_obsidian_installed", lambda: False)
    monkeypatch.delattr(os, "startfile", raising=False)
    r = client.post("/api/obsidian/open")
    assert r.status_code == 200
    assert r.json()["opened"] == "uri"
    assert r.json()["obsidian_uri"].startswith("obsidian://open?path=")


def test_open_vault_missing(client, monkeypatch, tmp_path):
    """vault 目录不存在 → 400。"""
    _isolate_settings(monkeypatch, tmp_path)
    import app.api.obsidian as obs
    monkeypatch.setattr(obs, "_obsidian_installed", lambda: True)
    gone = tmp_path / "gone"
    client.app.state.library.cfg.paths.vault_dir = gone
    r = client.post("/api/obsidian/open")
    assert r.status_code == 400
    assert "不存在" in r.json()["detail"]
