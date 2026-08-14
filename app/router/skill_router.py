"""技能路由（设计文档 §6.5 ③）：问题 embedding → 技能库向量检索 → 注入 SKILL.md。

- 命中：skill_index 余弦检索（skills.description），cos ≥ 阈值（越大越近）。
- 命中 1 个 → 注入；命中 ≥2 → LLM 裁决（chat_json 选最相关）；无 key 时按分数取 top-1。
- 注入：SKILL.md 全文（截断 max_skill_chars）作为 system prompt 追加段。
"""
from __future__ import annotations

from app.config import AppConfig
from app.db.repo import Repo
from app.llm.chat import ChatClient
from app.router.skill_index import SkillIndex

_ROUTER_JUDGE = (
    "你是技能路由裁决器。根据用户问题与候选技能描述，选出最应该激活的一个技能。"
    "只输出 JSON：{\"skill_id\": \"...\", \"reason\": \"...\"}。若都明显不相关输出 {\"skill_id\": null}。"
)


class SkillRouter:
    def __init__(self, cfg: AppConfig, repo: Repo, embed, skill_index: SkillIndex,
                 llm: ChatClient | None = None):
        self.cfg = cfg
        self.repo = repo
        self.embed = embed
        self.skill_index = skill_index
        self.llm = llm

    def retrieve(self, query: str) -> dict:
        """返回 {skills: [{skill_id, name, description, md_text, distance}], decided_by}。"""
        try:
            vec = self.embed.embed_one(query)
        except Exception:  # noqa: BLE001  -- embedding 不可用则不路由
            return {"skills": [], "decided_by": "embed_unavailable"}
        threshold = self.cfg.distill.route_threshold
        top_k = self.cfg.distill.route_top_k
        try:
            hits = self.skill_index.search(vec, top_k=top_k)
        except Exception:  # noqa: BLE001
            return {"skills": [], "decided_by": "index_unavailable"}

        # 余弦相似度（_distance = cos，越大越近）≥ 阈值才命中
        hits = [h for h in hits if h["_distance"] >= threshold]
        if not hits:
            return {"skills": [], "decided_by": "no_hit"}

        skills = []
        for h in hits:
            s = self.repo.get_skill(h["skill_id"])
            if not s or s.get("status") not in ("approved", "installed"):
                continue
            md = self._load_skill_md(s)
            skills.append({
                "skill_id": s["skill_id"],
                "name": s.get("name") or s["skill_id"],
                "description": s.get("description") or "",
                "md_text": md,
                "distance": h["_distance"],
            })

        if len(skills) <= 1:
            return {"skills": skills, "decided_by": "vector" if skills else "no_hit"}

        # 多技能冲突 → LLM 裁决；无 key/失败 → 按距离取 top-1（不阻塞问答）
        chosen = self._judge(query, skills)
        if chosen is None:
            skills.sort(key=lambda x: x["distance"])
            return {"skills": skills[:1], "decided_by": "vector_top1"}
        picked = [x for x in skills if x["skill_id"] == chosen]
        return {"skills": picked if picked else skills[:1], "decided_by": "llm"}

    def build_system_hint(self, routed: dict) -> str:
        """把命中技能转成 system prompt 追加段（问答注入）。"""
        skills = routed.get("skills", [])
        if not skills:
            return ""
        parts = ["馆内已加载以下技能，请优先按对应框架作答："]
        for s in skills:
            md = (s.get("md_text") or "")[: self.cfg.distill.max_skill_chars]
            parts.append(f"## 技能《{s['name']}》\n{md}")
        return "\n\n".join(parts)

    # ---------- 内部 ----------

    def _load_skill_md(self, s: dict) -> str:
        p = self.cfg.paths.vault_dir / (s.get("path") or "")
        if p.exists():
            return p.read_text(encoding="utf-8", errors="replace")
        return ""

    def _judge(self, query: str, skills: list[dict]) -> str | None:
        if self.llm is None:
            return None
        try:
            cand = "\n".join(
                f"- {s['skill_id']}: {s['description'][:200]}" for s in skills
            )
            data = self.llm.chat_json(
                f"用户问题：{query}\n\n候选技能：\n{cand}\n\n只输出 JSON。",
                system=_ROUTER_JUDGE,
            )
            sid = data.get("skill_id")
            return str(sid) if sid else None
        except Exception:  # noqa: BLE001
            return None
