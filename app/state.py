"""应用状态：P0 共享 repo / vector store / embedding client / indexer / searcher。"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.config import AppConfig
from app.db.repo import Repo
from app.llm.embeddings import EmbeddingClient
from app.retrieval.indexer import Indexer
from app.retrieval.searcher import Searcher
from app.vec.vector_store import VectorStore


@dataclass
class LibraryState:
    cfg: AppConfig
    repo: Repo = field(init=False)
    vec: VectorStore = field(init=False)
    embed: EmbeddingClient = field(init=False)
    indexer: Indexer = field(init=False)
    searcher: Searcher = field(init=False)

    def __post_init__(self) -> None:
        self.cfg.ensure_dirs()
        self.repo = Repo(self.cfg.paths.data_dir / "library.db")
        self.vec = VectorStore(self.cfg.paths.data_dir / "lancedb")
        self.embed = EmbeddingClient(self.cfg, self.repo)
        self.indexer = Indexer(self.repo, self.vec, self.embed)
        self.searcher = Searcher(self.repo, self.vec, self.embed)


def build_state(cfg: AppConfig | None = None, embed=None) -> LibraryState:
    state = LibraryState(cfg or AppConfig.load())
    if embed is not None:
        state.embed = embed  # type: ignore[assignment]
        state.indexer = Indexer(state.repo, state.vec, state.embed)
        state.searcher = Searcher(state.repo, state.vec, state.embed)
    return state
