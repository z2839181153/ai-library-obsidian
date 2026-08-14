"""应用状态：共享 repo / vector store / embedding / chat / indexer / searcher / 编目服务。"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.config import AppConfig
from app.core.card_generator import CardGenerator
from app.core.qa import QAService
from app.core.shelving import Shelver
from app.db.repo import Repo
from app.llm.chat import ChatClient
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
    llm: ChatClient = field(init=False)
    indexer: Indexer = field(init=False)
    searcher: Searcher = field(init=False)
    cards: CardGenerator = field(init=False)
    shelver: Shelver = field(init=False)
    qa: QAService = field(init=False)

    def __post_init__(self) -> None:
        self.cfg.ensure_dirs()
        self.repo = Repo(self.cfg.paths.data_dir / "library.db")
        self.vec = VectorStore(self.cfg.paths.data_dir / "lancedb")
        self.embed = EmbeddingClient(self.cfg, self.repo)
        self.llm = ChatClient(self.cfg)
        self.indexer = Indexer(self.repo, self.vec, self.embed)
        self.searcher = Searcher(self.repo, self.vec, self.embed)
        self.cards = CardGenerator(self.repo, self.llm, self.cfg)
        self.shelver = Shelver(self.repo, self.cfg)
        self.qa = QAService(self.repo, self.searcher, self.llm)


def build_state(cfg: AppConfig | None = None, embed=None, llm=None) -> LibraryState:
    """构建状态；测试可注入 FakeEmbed / FakeLLM。"""
    state = LibraryState(cfg or AppConfig.load())
    if embed is not None:
        state.embed = embed  # type: ignore[assignment]
        state.indexer = Indexer(state.repo, state.vec, state.embed)
        state.searcher = Searcher(state.repo, state.vec, state.embed)
        state.qa = QAService(state.repo, state.searcher, state.llm)
    if llm is not None:
        state.llm = llm  # type: ignore[assignment]
        state.cards = CardGenerator(state.repo, state.llm, state.cfg)
        state.qa = QAService(state.repo, state.searcher, state.llm)
    return state
