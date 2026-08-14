"""应用配置：从 config/settings.json 加载，环境变量可覆盖（AI_LIBRARY_ 前缀）。

设计说明（详细设计文档 §2.2 / §3）：
- 配置文件是唯一机器可写配置源；环境变量用于部署覆盖。
- 私密配置（API key 等）不写进仓库，从环境变量或 data/secrets.json 读取。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

# 项目根目录（app/ 的上一级）
ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = ROOT_DIR / "config" / "settings.json"


def _load_json(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8800


@dataclass
class PathsConfig:
    data_dir: Path = ROOT_DIR / "data"
    vault_dir: Path = ROOT_DIR / "vault"

    def __post_init__(self) -> None:
        self.data_dir = Path(self.data_dir)
        self.vault_dir = Path(self.vault_dir)


@dataclass
class ModelScopeConfig:
    base_url: str = "https://api-inference.modelscope.cn/v1"
    api_key: str = ""          # 从环境变量 MODELSCOPE_API_KEY 或 secrets.json 读取
    chat_model: str = "deepseek-ai/DeepSeek-V4-Flash-0731"
    distill_model: str = "ZhipuAI/GLM-5.2"
    embed_model: str = "Qwen/Qwen3-Embedding-0.6B"
    chat_retries: int = 8              # LLM 失败/空响应最大重试次数（可配）
    chat_retry_base: float = 3.0       # 重试间隔基数（s）：3,6,12,... 递增
    chat_retry_max: float = 60.0       # 重试间隔上限（s）
    chat_retry_on_429: bool = False    # 429（限流/余额不足）默认不重试，立即降级


@dataclass
class OllamaConfig:
    base_url: str = "http://127.0.0.1:11434"
    enabled: bool = False      # 私密内容隔离通道，可用时启用
    model: str = "qwen2.5:7b"


@dataclass
class DistillConfig:
    cangjie_skill_dir: Path = ROOT_DIR / "skills" / "cangjie-skill"  # prompt/模板单一事实源
    max_chunk_chars: int = 6000          # 单次喂给 extractor 的正文分块上限
    max_skill_chars: int = 8000          # 问答注入 SKILL.md 的截断上限
    route_threshold: float = 0.6         # 技能路由命中余弦阈值（cos 越大越近，≥0.6 命中）
    route_top_k: int = 5                 # 路由候选数
    reject_block: int = 5                # 连续拒绝 ≥5 自动阻塞

    def __post_init__(self) -> None:
        self.cangjie_skill_dir = Path(self.cangjie_skill_dir)


@dataclass
class AppConfig:
    server: ServerConfig = field(default_factory=ServerConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    modelscope: ModelScopeConfig = field(default_factory=ModelScopeConfig)
    ollama: OllamaConfig = field(default_factory=OllamaConfig)
    distill: DistillConfig = field(default_factory=DistillConfig)

    @classmethod
    def load(cls, path: Path = DEFAULT_CONFIG_PATH) -> "AppConfig":
        raw = _load_json(path)
        cfg = cls()

        srv = raw.get("server", {})
        cfg.server.host = srv.get("host", cfg.server.host)
        cfg.server.port = int(srv.get("port", cfg.server.port))

        p = raw.get("paths", {})
        cfg.paths.data_dir = Path(p.get("data_dir", str(cfg.paths.data_dir)))
        cfg.paths.vault_dir = Path(p.get("vault_dir", str(cfg.paths.vault_dir)))

        ms = raw.get("modelscope", {})
        cfg.modelscope.base_url = ms.get("base_url", cfg.modelscope.base_url)
        cfg.modelscope.chat_model = ms.get("chat_model", cfg.modelscope.chat_model)
        cfg.modelscope.distill_model = ms.get("distill_model", cfg.modelscope.distill_model)
        cfg.modelscope.embed_model = ms.get("embed_model", cfg.modelscope.embed_model)
        cfg.modelscope.chat_retries = int(ms.get("chat_retries", cfg.modelscope.chat_retries))
        cfg.modelscope.chat_retry_base = float(ms.get("chat_retry_base", cfg.modelscope.chat_retry_base))
        cfg.modelscope.chat_retry_max = float(ms.get("chat_retry_max", cfg.modelscope.chat_retry_max))
        cfg.modelscope.chat_retry_on_429 = bool(ms.get("chat_retry_on_429", cfg.modelscope.chat_retry_on_429))

        ol = raw.get("ollama", {})
        cfg.ollama.base_url = ol.get("base_url", cfg.ollama.base_url)
        cfg.ollama.enabled = bool(ol.get("enabled", cfg.ollama.enabled))
        cfg.ollama.model = ol.get("model", cfg.ollama.model)

        ds = raw.get("distill", {})
        cfg.distill.cangjie_skill_dir = Path(ds.get("cangjie_skill_dir", str(cfg.distill.cangjie_skill_dir)))
        cfg.distill.max_chunk_chars = int(ds.get("max_chunk_chars", cfg.distill.max_chunk_chars))
        cfg.distill.max_skill_chars = int(ds.get("max_skill_chars", cfg.distill.max_skill_chars))
        cfg.distill.route_threshold = float(ds.get("route_threshold", cfg.distill.route_threshold))
        cfg.distill.route_top_k = int(ds.get("route_top_k", cfg.distill.route_top_k))
        cfg.distill.reject_block = int(ds.get("reject_block", cfg.distill.reject_block))

        # 密钥：环境变量优先，其次 data/secrets.json（不入库）
        cfg.modelscope.api_key = os.environ.get(
            "MODELSCOPE_API_KEY",
            _load_json(cfg.paths.data_dir / "secrets.json").get("modelscope_api_key", ""),
        )
        return cfg

    def ensure_dirs(self) -> None:
        self.paths.data_dir.mkdir(parents=True, exist_ok=True)
        self.paths.vault_dir.mkdir(parents=True, exist_ok=True)
