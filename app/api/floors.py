"""楼层/房间/书架 API（设计文档 §9.2）。

- GET /api/floors：楼层树（P1）
- POST /api/floors：新建楼层（P3 设置页）
- PUT /api/floors/{floor_id}：改名/改描述/排序（P3）
- DELETE /api/floors/{floor_id}：删除（有书的楼层拒绝，P3）
- POST /api/rooms、PUT/DELETE /api/rooms/{room_id}
- POST /api/shelves、PUT/DELETE /api/shelves/{shelf_id}
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.db.repo import new_id

router = APIRouter(tags=["floors"])


class FloorCreate(BaseModel):
    name: str
    code: str | None = None
    media_type: str | None = None
    description: str = ""


class FloorUpdate(BaseModel):
    name: str | None = None
    code: str | None = None
    media_type: str | None = None
    description: str | None = None
    ord: int | None = None


class RoomCreate(BaseModel):
    floor_id: str
    name: str
    description: str = ""


class RoomUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    ord: int | None = None


class ShelfCreate(BaseModel):
    room_id: str
    name: str
    description: str = ""


class ShelfUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    ord: int | None = None


@router.get("/floors")
def list_floors(req: Request) -> dict:
    """楼层树：每个楼层含房间，房间含书架。"""
    state = req.app.state.library
    floors = []
    for f in state.repo.list_floors():
        rooms = []
        for rm in state.repo.list_rooms(f["floor_id"]):
            shelves = state.repo.list_shelves(rm["room_id"])
            rooms.append({
                "room_id": rm["room_id"],
                "name": rm["name"],
                "description": rm.get("description") or "",
                "shelves": [
                    {"shelf_id": s["shelf_id"], "name": s["name"],
                     "description": s.get("description") or ""}
                    for s in shelves
                ],
            })
        floors.append({
            "floor_id": f["floor_id"],
            "code": f["code"],
            "name": f["name"],
            "media_type": f["media_type"],
            "description": f.get("description") or "",
            "rooms": rooms,
        })
    return {"floors": floors}


@router.post("/floors")
def create_floor(req: Request, body: FloorCreate) -> dict:
    """新建楼层（主人操作）。code 缺省自动生成 F{n}。"""
    state = req.app.state.library
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="楼层名称不能为空")
    code = (body.code or "").strip()
    if not code:
        n = len(state.repo.list_floors()) + 1
        code = f"{n}F"
    floor_id = new_id("fl")
    state.repo.insert_floor({
        "floor_id": floor_id,
        "name": name,
        "code": code,
        "media_type": body.media_type or "other",
        "description": body.description,
        "ord": len(state.repo.list_floors()) + 1,
        "custom": 1,
    })
    state.repo.insert_action({
        "agent": "owner",
        "action_type": "floor_create",
        "target_type": "floor",
        "target_id": floor_id,
        "params": {"name": name, "code": code},
        "undo_params": {},
        "reason": f"主人新建楼层 {code} {name}",
    })
    return {"floor_id": floor_id, "ok": True}


@router.put("/floors/{floor_id}")
def update_floor(req: Request, floor_id: str, body: FloorUpdate) -> dict:
    state = req.app.state.library
    floor = state.repo.get_floor(floor_id)
    if not floor:
        raise HTTPException(status_code=404, detail="楼层不存在")
    fields = {}
    if body.name is not None:
        fields["name"] = body.name.strip() or floor["name"]
    if body.code is not None:
        fields["code"] = body.code.strip() or floor["code"]
    if body.media_type is not None:
        fields["media_type"] = body.media_type
    if body.description is not None:
        fields["description"] = body.description
    if body.ord is not None:
        fields["ord"] = body.ord
    if fields:
        state.repo.conn.execute(
            "UPDATE floors SET " + ", ".join(f"{k}=?" for k in fields) + " WHERE floor_id=?",
            list(fields.values()) + [floor_id],
        )
        state.repo.commit()
        state.repo.insert_action({
            "agent": "owner", "action_type": "floor_update",
            "target_type": "floor", "target_id": floor_id,
            "params": fields, "undo_params": {},
            "reason": f"主人修改楼层 {floor['name']}",
        })
    return {"floor_id": floor_id, "ok": True}


@router.delete("/floors/{floor_id}")
def delete_floor(req: Request, floor_id: str) -> dict:
    """删除楼层：有书的楼层拒绝（保护馆藏，主人须先移动书）。"""
    state = req.app.state.library
    floor = state.repo.get_floor(floor_id)
    if not floor:
        raise HTTPException(status_code=404, detail="楼层不存在")
    # 有书（vault_path 含该楼层 code 前缀，如 books/1F-电子书/...）→ 拒绝
    code = floor["code"]
    n = state.repo.conn.execute(
        "SELECT COUNT(*) c FROM books WHERE vault_path LIKE ? OR vault_path LIKE ?",
        (f"%/{code}-%", f"%/{code}/%"),
    ).fetchone()["c"]
    if n:
        raise HTTPException(status_code=409, detail=f"楼层 {code} 下还有 {n} 本书，请先移走")
    state.repo.conn.execute("DELETE FROM floors WHERE floor_id=?", (floor_id,))
    state.repo.conn.execute("DELETE FROM rooms WHERE floor_id=?", (floor_id,))
    state.repo.commit()
    state.repo.insert_action({
        "agent": "owner", "action_type": "floor_delete",
        "target_type": "floor", "target_id": floor_id,
        "params": {"name": floor["name"], "code": code}, "undo_params": {},
        "reason": f"主人删除楼层 {code} {floor['name']}",
    })
    return {"floor_id": floor_id, "ok": True, "deleted": True}


@router.post("/rooms")
def create_room(req: Request, body: RoomCreate) -> dict:
    state = req.app.state.library
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="房间名称不能为空")
    if not state.repo.get_floor(body.floor_id):
        raise HTTPException(status_code=404, detail="楼层不存在")
    room_id = state.repo.insert_room({"floor_id": body.floor_id, "name": name,
                                      "description": body.description})
    state.repo.insert_action({
        "agent": "owner", "action_type": "room_create",
        "target_type": "room", "target_id": room_id,
        "params": {"floor_id": body.floor_id, "name": name}, "undo_params": {},
        "reason": f"主人新建房间 {name}",
    })
    return {"room_id": room_id, "ok": True}


@router.put("/rooms/{room_id}")
def update_room(req: Request, room_id: str, body: RoomUpdate) -> dict:
    state = req.app.state.library
    room = state.repo.get_room(room_id)
    if not room:
        raise HTTPException(status_code=404, detail="房间不存在")
    fields = {}
    if body.name is not None:
        fields["name"] = body.name.strip() or room["name"]
    if body.description is not None:
        fields["description"] = body.description
    if body.ord is not None:
        fields["ord"] = body.ord
    if fields:
        state.repo.conn.execute(
            "UPDATE rooms SET " + ", ".join(f"{k}=?" for k in fields) + " WHERE room_id=?",
            list(fields.values()) + [room_id],
        )
        state.repo.commit()
        state.repo.insert_action({
            "agent": "owner", "action_type": "room_update",
            "target_type": "room", "target_id": room_id,
            "params": fields, "undo_params": {},
            "reason": f"主人修改房间 {room['name']}",
        })
    return {"room_id": room_id, "ok": True}


@router.delete("/rooms/{room_id}")
def delete_room(req: Request, room_id: str) -> dict:
    state = req.app.state.library
    room = state.repo.get_room(room_id)
    if not room:
        raise HTTPException(status_code=404, detail="房间不存在")
    n = state.repo.conn.execute(
        "SELECT COUNT(*) c FROM books WHERE vault_path LIKE ?", (f"%/{room['name']}/%",)
    ).fetchone()["c"]
    if n:
        raise HTTPException(status_code=409, detail=f"房间 {room['name']} 下还有 {n} 本书，请先移走")
    state.repo.conn.execute("DELETE FROM rooms WHERE room_id=?", (room_id,))
    state.repo.conn.execute("DELETE FROM shelves WHERE room_id=?", (room_id,))
    state.repo.commit()
    state.repo.insert_action({
        "agent": "owner", "action_type": "room_delete",
        "target_type": "room", "target_id": room_id,
        "params": {"name": room["name"]}, "undo_params": {},
        "reason": f"主人删除房间 {room['name']}",
    })
    return {"room_id": room_id, "ok": True, "deleted": True}


@router.post("/shelves")
def create_shelf(req: Request, body: ShelfCreate) -> dict:
    state = req.app.state.library
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="书架名称不能为空")
    if not state.repo.get_room(body.room_id):
        raise HTTPException(status_code=404, detail="房间不存在")
    shelf_id = state.repo.insert_shelf({"room_id": body.room_id, "name": name,
                                        "description": body.description})
    state.repo.insert_action({
        "agent": "owner", "action_type": "shelf_create",
        "target_type": "shelf", "target_id": shelf_id,
        "params": {"room_id": body.room_id, "name": name}, "undo_params": {},
        "reason": f"主人新建书架 {name}",
    })
    return {"shelf_id": shelf_id, "ok": True}


@router.put("/shelves/{shelf_id}")
def update_shelf(req: Request, shelf_id: str, body: ShelfUpdate) -> dict:
    state = req.app.state.library
    shelf = state.repo.get_shelf(shelf_id)
    if not shelf:
        raise HTTPException(status_code=404, detail="书架不存在")
    fields = {}
    if body.name is not None:
        fields["name"] = body.name.strip() or shelf["name"]
    if body.description is not None:
        fields["description"] = body.description
    if body.ord is not None:
        fields["ord"] = body.ord
    if fields:
        state.repo.conn.execute(
            "UPDATE shelves SET " + ", ".join(f"{k}=?" for k in fields) + " WHERE shelf_id=?",
            list(fields.values()) + [shelf_id],
        )
        state.repo.commit()
        state.repo.insert_action({
            "agent": "owner", "action_type": "shelf_update",
            "target_type": "shelf", "target_id": shelf_id,
            "params": fields, "undo_params": {},
            "reason": f"主人修改书架 {shelf['name']}",
        })
    return {"shelf_id": shelf_id, "ok": True}


@router.delete("/shelves/{shelf_id}")
def delete_shelf(req: Request, shelf_id: str) -> dict:
    state = req.app.state.library
    shelf = state.repo.get_shelf(shelf_id)
    if not shelf:
        raise HTTPException(status_code=404, detail="书架不存在")
    n = state.repo.conn.execute(
        "SELECT COUNT(*) c FROM books WHERE vault_path LIKE ?", (f"%/{shelf['name']}/%",)
    ).fetchone()["c"]
    if n:
        raise HTTPException(status_code=409, detail=f"书架 {shelf['name']} 下还有 {n} 本书，请先移走")
    state.repo.conn.execute("DELETE FROM shelves WHERE shelf_id=?", (shelf_id,))
    state.repo.commit()
    state.repo.insert_action({
        "agent": "owner", "action_type": "shelf_delete",
        "target_type": "shelf", "target_id": shelf_id,
        "params": {"name": shelf["name"]}, "undo_params": {},
        "reason": f"主人删除书架 {shelf['name']}",
    })
    return {"shelf_id": shelf_id, "ok": True, "deleted": True}
