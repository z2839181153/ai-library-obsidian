"""楼层/房间/书架 API（设计文档 §9.2；P1 只读 + 种子，CRUD 留 P3 设置页）。"""
from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(tags=["floors"])


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
