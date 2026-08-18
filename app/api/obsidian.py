"""Obsidian 关联 API（P6-2，设计文档 §10 P6-2 A+B）。

- A. vault 指向任意 Obsidian vault：
    GET  /api/obsidian/status —— 当前 vault 状态（路径/是否 Obsidian vault/是否内置/协议注册/URI）
    POST /api/obsidian/link   —— 关联新 vault 目录（冲突校验 + 可选复制内容，写入 paths.vault_dir）
- B. 「📂 在 Obsidian 中打开」一键入口：
    POST /api/obsidian/open   —— 已装 Obsidian 走 obsidian:// URI；否则降级资源管理器打开文件夹

职责分离（设计约束）：软件数据（SQLite/LanceDB/secrets）留在 data/，只有知识内容
（books/ 楼层目录、catalog/ 卡片、skills/ 技能、pending/ 候选）进用户 vault。
图书馆只在 vault 的子目录操作，与用户已有笔记共存；复制/关联绝不覆盖已存在文件
（AI 不覆盖主人手动摆放）。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import urllib.parse
import webbrowser
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.config import DEFAULT_CONFIG_PATH, ROOT_DIR

router = APIRouter(prefix="/obsidian", tags=["obsidian"])

# 图书馆在 vault 内操作的子目录（与用户笔记共存，绝不碰其他文件）
LIBRARY_CONTENT_DIRS = ["books", "catalog", "skills", "pending"]
# 本项目楼层目录命名：books/1F-电子书/…（用于判定 books/ 是否为本图书馆结构）
FLOOR_DIR_RE = re.compile(r"^\d+F-")


# ---------------- 内部工具 ----------------

def _load_settings_json() -> dict:
    return json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")) \
        if DEFAULT_CONFIG_PATH.exists() else {}


def _save_settings_json(raw: dict) -> None:
    DEFAULT_CONFIG_PATH.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _same_path(a: Path, b: Path) -> bool:
    """Windows 大小写不敏感地比较两个绝对路径。"""
    return os.path.normcase(str(Path(a).absolute())) == \
        os.path.normcase(str(Path(b).absolute()))


def _obsidian_uri(vault: Path) -> str:
    """obsidian://open?path=<url-encoded 绝对路径>（Obsidian 官方 URI）。

    Windows 路径转正斜杠（D:/foo/vault）再编码；Obsidian 会自动解码。
    """
    p = str(Path(vault).absolute()).replace("\\", "/")
    return "obsidian://open?path=" + urllib.parse.quote(p)


def _obsidian_installed() -> bool:
    """检测 obsidian:// 协议是否已注册（Windows 注册表 HKCR）。"""
    try:
        import winreg  # noqa: PLC0415
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "obsidian"):
            return True
    except Exception:  # noqa: BLE001 —— 非 Windows / 未注册 / 无权限
        return False


def _books_is_library(books_dir: Path) -> bool:
    """books/ 是否为本图书馆结构（存在 ≥1 个数字楼层前缀目录，如 1F-电子书）。

    - 不存在 / 空目录 → True（无冲突）
    - 非空且无楼层目录 → False（疑似他人笔记，拒绝关联）
    """
    if not books_dir.exists():
        return True
    try:
        names = [d.name for d in books_dir.iterdir() if d.is_dir()]
    except OSError:
        return False
    if not names:
        return True
    return any(FLOOR_DIR_RE.match(n) for n in names)


def _copy_tree_skip_existing(src: Path, dst: Path) -> int:
    """复制目录树到 dst，已存在文件不覆盖。返回复制的文件数。"""
    n = 0
    for root, _dirs, files in os.walk(src):
        rel = Path(root).relative_to(src)
        target_dir = dst / rel
        target_dir.mkdir(parents=True, exist_ok=True)
        for f in files:
            dp = target_dir / f
            if not dp.exists():
                shutil.copy2(Path(root) / f, dp)
                n += 1
    return n


def _vault_status(state) -> dict:
    cfg = state.cfg
    vault = Path(cfg.paths.vault_dir).absolute()
    default_vault = Path(ROOT_DIR / "vault").absolute()
    books_dir = vault / "books"
    return {
        "vault_dir": str(vault),
        "default_vault_dir": str(default_vault),
        "exists": vault.exists(),
        "is_obsidian_vault": (vault / ".obsidian").is_dir(),
        "is_managed": _same_path(vault, default_vault),
        "books_exists": books_dir.exists(),
        "books_is_library": _books_is_library(books_dir),
        "content_dirs": {name: (vault / name).exists() for name in LIBRARY_CONTENT_DIRS},
        "obsidian_installed": _obsidian_installed(),
        "obsidian_uri": _obsidian_uri(vault),
    }


# ---------------- 端点 ----------------

@router.get("/status")
def obsidian_status(req: Request) -> dict:
    """当前 vault 关联状态（只读）。"""
    return _vault_status(req.app.state.library)


class LinkVaultBody(BaseModel):
    vault_dir: str
    copy_existing: bool = False    # 可选：复制当前图书馆内容（books/catalog/skills/pending）到新 vault


@router.post("/link")
def link_vault(req: Request, body: LinkVaultBody) -> dict:
    """关联任意 Obsidian vault 目录（写入 paths.vault_dir，绝对路径）。

    - 目标目录不存在 → 自动创建
    - 目标 books/ 已存在且非本项目结构 → 400 拒绝（避免覆盖主人笔记）
    - copy_existing=True → 把当前 vault 的 books/catalog/skills/pending 复制过去
      （已存在文件不覆盖）
    """
    state = req.app.state.library
    target = (body.vault_dir or "").strip()
    if not target:
        raise HTTPException(status_code=400, detail="请填写 Obsidian vault 目录路径")

    target = Path(target).expanduser()
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise HTTPException(status_code=400, detail=f"无法创建目录: {e}")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="目标路径不是目录")

    # 冲突校验：目标 books/ 已存在且非本项目结构 → 拒绝（AI 不覆盖主人手动摆放）
    books_dir = target / "books"
    if books_dir.exists() and not _books_is_library(books_dir):
        raise HTTPException(
            status_code=400,
            detail="目标目录下已存在 books/ 且不是本图书馆的楼层结构——为避免覆盖你的笔记，"
                   "请选择一个空目录 / 新建目录，或先自行确认该 books/ 可合并后再关联",
        )

    # 可选：复制当前图书馆内容到目标（不覆盖已有文件）
    copied: dict[str, int] = {}
    if body.copy_existing:
        src_vault = Path(state.cfg.paths.vault_dir)
        for name in LIBRARY_CONTENT_DIRS:
            src = src_vault / name
            if src.exists():
                copied[name] = _copy_tree_skip_existing(src, target / name)

    # 写入 settings.json（绝对路径，规避安装版 CWD 坑）并同步运行中 cfg
    raw = _load_settings_json()
    raw.setdefault("paths", {})["vault_dir"] = str(target.absolute())
    _save_settings_json(raw)
    state.cfg.paths.vault_dir = target

    if copied:
        msg = "✅ 已关联新 vault，并复制图书馆内容（" + \
              "、".join(f"{k} {n} 个文件" for k, n in copied.items()) + "）"
    else:
        msg = "✅ 已关联新 vault（未复制内容；已上架书正文仍可通过离线副本阅读，Obsidian 中可见的馆藏文件需复制或重新入馆）"
    return {"ok": True, "message": msg, "copied": copied, "status": _vault_status(state)}


@router.post("/open")
def open_vault(req: Request) -> dict:
    """在 Obsidian 中打开当前 vault；未安装 Obsidian → 资源管理器打开文件夹。"""
    state = req.app.state.library
    vault = Path(state.cfg.paths.vault_dir)
    if not vault.exists():
        raise HTTPException(status_code=400, detail="vault 目录不存在，请先在设置页关联或创建")

    uri = _obsidian_uri(vault)
    if _obsidian_installed():
        webbrowser.open(uri)
        return {"opened": "obsidian", "obsidian_uri": uri,
                "message": "已在 Obsidian 中打开 vault"}
    if hasattr(os, "startfile"):
        os.startfile(str(vault))  # explorer 打开文件夹
        return {"opened": "explorer", "obsidian_uri": uri,
                "message": "未检测到 Obsidian，已用资源管理器打开 vault 文件夹。安装 Obsidian 后可获得双链体验。"}
    return {"opened": "uri", "obsidian_uri": uri,
            "message": "当前环境不支持直接打开，请复制链接在浏览器/应用中打开"}
