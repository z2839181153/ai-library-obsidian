"""技能 API（设计文档 §6.4 人审 / P2 验收）：待审阅队列、详情、批准/拒绝/解除阻塞。

主人主权：批准/拒绝/解除全部经 action ledger 记录且可撤销；
拒绝附原因，连续拒绝 ≥5 次自动阻塞该书蒸馏（主人可解除）。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

router = APIRouter(prefix="/skills", tags=["skills"])


# ---------- 审阅页（P3 Web UI 前的过渡，无 Vue） ----------
# 注意：静态路径 /review 必须声明在 /{skill_id} 之前，避免被动态路由吞掉
@router.get("/review", include_in_schema=False, response_class=HTMLResponse)
def review_page() -> str:
    return REVIEW_HTML

# 技能审阅页（P3 Web UI 前的过渡，无 Vue）
REVIEW_HTML = """<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>技能审阅 · AI 图书馆</title>
<style>
 body{font-family:system-ui,sans-serif;max-width:960px;margin:2rem auto;padding:0 1rem;color:#222}
 .card{border:1px solid #ddd;border-radius:8px;padding:1rem 1.2rem;margin-bottom:1rem}
 .badge{display:inline-block;padding:.15rem .6rem;border-radius:999px;font-size:.8rem;margin-left:.4rem}
 .reviewing{background:#fff3cd}.approved{background:#d4edda}.rejected{background:#f8d7da}
 .blocked{background:#f5c6cb}.installed{background:#cce5ff}
 pre{background:#f6f8fa;padding:1rem;border-radius:6px;overflow:auto;font-size:.85rem}
 button{margin-right:.5rem;padding:.35rem .9rem;border-radius:6px;border:1px solid #999;cursor:pointer}
 .ok{background:#28a745;color:#fff;border-color:#28a745}
 .no{background:#dc3545;color:#fff;border-color:#dc3545}
 .muted{color:#666;font-size:.85rem}
</style>
</head>
<body>
<h1>📚 技能审阅</h1>
<p class="muted">蒸馏产物待主人确认。批准→注册进技能库（问答自动路由）；拒绝→附原因（连续 5 次自动阻塞）。</p>
<div id="list"></div>
<pre id="detail" hidden></pre>
<script>
const ROOT='';
async function api(path,opts){const r=await fetch(ROOT+path,opts);if(!r.ok)throw new Error(await r.text());return r.json()}
async function load(){
 const qs=new URLSearchParams(location.search);
 const status=qs.get('status')||'reviewing';
 const data=await api('/api/skills?status='+status);
 const el=document.getElementById('list');el.innerHTML='';
 data.skills.forEach(s=>{
  const d=document.createElement('div');d.className='card';
  d.innerHTML=`<h3>${s.name} <span class="badge ${s.status}">${s.status}</span></h3>
   <p class="muted">来源：${s.book_title||s.book_id||''} ｜ 拒绝 ${s.reject_count} 次</p>
   <p>${s.description||''}</p>
   <button onclick="view('${s.skill_id}')">查看 SKILL.md + 测试</button>
   <button class="ok" onclick="approve('${s.skill_id}')">批准</button>
   <button class="no" onclick="reject('${s.skill_id}')">拒绝</button>
   ${s.status==='blocked'?`<button onclick="unblock('${s.skill_id}')">解除阻塞</button>`:''}`;
  el.appendChild(d);
 });
}
async function view(id){
 const s=await api('/api/skills/'+id);
 const el=document.getElementById('detail');el.hidden=false;
 el.textContent='';
 el.textContent='SKILL.md:\n'+(s.skill_md||'(无)')+'\n\n---\n\ntest-prompts:\n'+(s.test_prompts_text||'(无)')+'\n\n---\n\ntest-results:\n'+(s.test_results||'(无)');
}
async function approve(id){await api('/api/skills/'+id+'/approve',{method:'POST'});alert('已批准');load()}
async function reject(id){
 const reason=prompt('拒绝原因（将带进下次重蒸 prompt）：');
 if(reason===null)return;
 await api('/api/skills/'+id+'/reject',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({reason})});
 alert('已拒绝');load();
}
async function unblock(id){await api('/api/skills/'+id+'/unblock',{method:'POST'});alert('已解除阻塞');load()}
load();
</script>
</body>
</html>
"""


class RejectRequest(BaseModel):
    reason: str = ""


def _skill_dto(state, s: dict) -> dict:
    book = state.repo.get_book(s["book_id"]) if s.get("book_id") else None
    return {
        "skill_id": s["skill_id"],
        "name": s["name"],
        "slug": s["slug"],
        "book_id": s.get("book_id") or "",
        "book_title": book.get("title") if book else "",
        "description": s.get("description") or "",
        "status": s.get("status"),
        "reject_count": s.get("reject_count") or 0,
        "last_reject_reason": s.get("last_reject_reason") or "",
        "path": s.get("path") or "",
        "created_at": s.get("created_at"),
        "updated_at": s.get("updated_at"),
    }


@router.get("")
def list_skills(req: Request, status: str | None = None,
                book_id: str | None = None) -> dict:
    state = req.app.state.library
    skills = state.repo.list_skills(status=status, book_id=book_id)
    return {"skills": [_skill_dto(state, s) for s in skills], "count": len(skills)}


@router.get("/{skill_id}")
def get_skill(req: Request, skill_id: str) -> dict:
    state = req.app.state.library
    s = state.repo.get_skill(skill_id)
    if not s:
        raise HTTPException(status_code=404, detail="技能不存在")
    out = _skill_dto(state, s)
    # SKILL.md 全文 + test-prompts + test-results（审阅用）
    md_path = state.cfg.paths.vault_dir / (s.get("path") or "")
    out["skill_md"] = md_path.read_text(encoding="utf-8", errors="replace") if md_path.exists() else None
    tp = s.get("test_prompts")
    out["test_prompts_text"] = tp if isinstance(tp, str) else (
        __import__("json").dumps(tp, ensure_ascii=False, indent=2) if tp else None
    )
    tr_path = md_path.parent / "test-results.md" if md_path else None
    out["test_results"] = tr_path.read_text(encoding="utf-8", errors="replace") if tr_path and tr_path.exists() else None
    out["actions"] = state.repo.list_actions(target_type="skill", target_id=skill_id, limit=10)
    return out


@router.post("/{skill_id}/approve")
def approve_skill(req: Request, skill_id: str) -> dict:
    """批准 → installed + 注册技能库向量索引（description embedding）。"""
    state = req.app.state.library
    s = state.repo.get_skill(skill_id)
    if not s:
        raise HTTPException(status_code=404, detail="技能不存在")
    if s["status"] == "blocked":
        raise HTTPException(status_code=409, detail="技能已被阻塞，需先解除")
    old_status = s["status"]
    state.repo.set_skill_status(skill_id, "installed")
    # 技能库索引（无 key 时 embed 抛异常 → 降级：已批准但暂不进索引）
    try:
        doc = f"{s.get('description') or ''} 《{(state.repo.get_book(s['book_id']) or {}).get('title') or ''}》"
        vec = state.embed.embed_one(doc)
        state.skill_index.upsert(skill_id, "installed", doc, vec)
    except Exception:  # noqa: BLE001
        pass
    state.repo.insert_action({
        "agent": "owner", "action_type": "skill_approve",
        "target_type": "skill", "target_id": skill_id,
        "params": {"book_id": s["book_id"]},
        "undo_params": {"old_status": old_status},
        "reason": f"批准技能《{s['name']}》",
    })
    return {"ok": True, "skill_id": skill_id, "status": "installed"}


@router.post("/{skill_id}/reject")
def reject_skill(req: Request, skill_id: str, body: RejectRequest) -> dict:
    """拒绝（附原因）→ rejected + reject_count++；≥5 次自动阻塞该书蒸馏。"""
    state = req.app.state.library
    s = state.repo.get_skill(skill_id)
    if not s:
        raise HTTPException(status_code=404, detail="技能不存在")
    reason = (body.reason or "").strip()
    if not reason:
        raise HTTPException(status_code=400, detail="拒绝必须附原因")
    state.repo.bump_skill_reject(skill_id, reason)
    count = state.repo.get_skill(skill_id)["reject_count"]
    block_at = state.cfg.distill.reject_block
    if count >= block_at:
        state.repo.set_skill_status(skill_id, "blocked")
        state.skill_index.remove(skill_id)
        # 书级蒸馏任务标记 blocked（再次 start 被拦截）
        state.repo.update_book_fields(s["book_id"], {"distill_status": "blocked"})
        new_status = "blocked"
    else:
        state.repo.set_skill_status(skill_id, "rejected")
        state.skill_index.remove(skill_id)
        new_status = "rejected"
    state.repo.insert_action({
        "agent": "owner", "action_type": "skill_reject",
        "target_type": "skill", "target_id": skill_id,
        "params": {"book_id": s["book_id"], "reason": reason, "reject_count": count},
        "reason": f"拒绝技能《{s['name']}》：{reason}",
    })
    return {"ok": True, "skill_id": skill_id, "status": new_status, "reject_count": count}


@router.post("/{skill_id}/unblock")
def unblock_skill(req: Request, skill_id: str) -> dict:
    """主人解除阻塞：重置计数 + 状态回 draft；书若无其他阻塞则恢复 idle。"""
    state = req.app.state.library
    s = state.repo.get_skill(skill_id)
    if not s:
        raise HTTPException(status_code=404, detail="技能不存在")
    if s["status"] != "blocked":
        return {"ok": True, "skill_id": skill_id, "status": s["status"], "noop": True}
    state.repo.reset_skill_reject(skill_id)
    state.repo.set_skill_status(skill_id, "draft")
    remaining_blocked = any(
        x["status"] == "blocked" for x in state.repo.list_skills(book_id=s["book_id"])
    )
    if not remaining_blocked:
        state.repo.update_book_fields(s["book_id"], {"distill_status": "idle"})
    state.repo.insert_action({
        "agent": "owner", "action_type": "skill_unblock",
        "target_type": "skill", "target_id": skill_id,
        "params": {"book_id": s["book_id"]},
        "reason": f"主人解除技能《{s['name']}》阻塞",
    })
    return {"ok": True, "skill_id": skill_id, "status": "draft"}
