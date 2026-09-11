# -*- coding: utf-8 -*-
"""FastAPI：给悬浮窗前端供数据 + 站点/凭证管理接口。"""
import quota_hud as Q
from fastapi import FastAPI, Body
from fastapi.responses import FileResponse, JSONResponse
import os

app = FastAPI(title="QuotaHUD", docs_url=None, redoc_url=None)
WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")


@app.get("/")
def index():
    return FileResponse(os.path.join(WEB, "index.html"))


@app.get("/api/state")
def state():
    s = Q.load_json(Q.STATE_F, {})
    if not s:
        s = Q.poll_once()
    return s


@app.get("/api/stations")
def list_stations():
    # 不返回凭证值，只返回有哪些 key
    cs = Q.creds()
    return {"stations": Q.stations(), "cred_keys": {k: list(v.keys()) for k, v in cs.items()},
            "has_default": bool(Q.stations())}


@app.post("/api/stations")
def upsert_station(st: dict = Body(...)):
    lst = [s for s in Q.stations() if s.get("id") != st.get("id")]
    lst.append(st)
    Q.save_stations(lst)
    return {"ok": True, "stations": lst}


@app.delete("/api/stations/{sid}")
def del_station(sid: str):
    Q.save_stations([s for s in Q.stations() if s.get("id") != sid])
    c = Q.creds()
    c.pop(sid, None)
    Q.save_creds(c)
    return {"ok": True}


@app.post("/api/creds/{sid}")
def set_creds(sid: str, body: dict = Body(...)):
    c = Q.creds()
    c[sid] = body
    Q.save_creds(c)
    return {"ok": True}


@app.post("/api/test/{sid}")
def test_station(sid: str):
    sts = {s["id"]: s for s in Q.stations()}
    if sid not in sts:
        return JSONResponse({"ok": False, "err": "站点不存在"}, status_code=404)
    res = Q.ADAPTERS.get(sts[sid]["type"], lambda s, c: Q._r(False, err="未知类型"))(sts[sid], Q.creds().get(sid, {}))
    return res


@app.post("/api/refresh")
def refresh():
    Q.start_poller()  # 幂等
    s = Q.poll_once()
    return {"ok": True, "updated_at": s.get("updated_at")}


@app.post("/api/seed_defaults")
def seed_defaults():
    if not Q.stations():
        Q.save_stations(Q.default_stations())
    return {"ok": True, "stations": Q.stations()}
