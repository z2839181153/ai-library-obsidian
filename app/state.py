"""应用状态：共享 repo / vector store / embedding / chat / indexer / searcher / 编目服务 / 蒸馏。"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.config import AppConfig
from app.core.card_generator import CardGenerator
from app.core.qa import QAService
from app.core.shelving import Shelver
from app.db.repo import Repo
from app.distill.pipeline import DistillPipeline
from app.llm.chat import ChatClient
from app.llm.embeddings import EmbeddingClient
from app.retrieval.indexer import Indexer
from app.retrieval.searcher import Searcher
from app.router.skill_index import SkillIndex
from app.router.skill_router import SkillRouter
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
    distill: DistillPipeline = field(init=False)
    skill_index: SkillIndex = field(init=False)
    router: SkillRouter = field(init=False)
    distill_executor: object = None     # 测试注入 FakeDistiller；None=运行时创建 LLMDistiller

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
        self.distill = DistillPipeline(self.cfg, self.repo, self.llm)
        self.skill_index = SkillIndex(self.cfg.paths.data_dir / "lancedb")
        self.router = SkillRouter(self.cfg, self.repo, self.embed, self.skill_index, self.llm)
        self.qa = QAService(self.repo, self.searcher, self.llm, self.router)


def build_state(cfg: AppConfig | None = None, embed=None, llm=None) -> LibraryState:
    """构建状态；测试可注入 FakeEmbed / FakeLLM。"""
    state = LibraryState(cfg or AppConfig.load())
    if embed is not None:
        state.embed = embed  # type: ignore[assignment]
        state.indexer = Indexer(state.repo, state.vec, state.embed)
        state.searcher = Searcher(state.repo, state.vec, state.embed)
        state.router = SkillRouter(state.cfg, state.repo, state.embed, state.skill_index, state.llm)
        state.qa = QAService(state.repo, state.searcher, state.llm, state.router)
    if llm is not None:
        state.llm = llm  # type: ignore[assignment]
        state.cards = CardGenerator(state.repo, state.llm, state.cfg)
        state.distill = DistillPipeline(state.cfg, state.repo, state.llm)
        state.router = SkillRouter(state.cfg, state.repo, state.embed, state.skill_index, state.llm)
        state.qa = QAService(state.repo, state.searcher, state.llm, state.router)
    return state
