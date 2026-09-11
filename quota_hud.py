# -*- coding: utf-8 -*-
"""
QuotaHUD 核心引擎：站点配置、凭证存储、余额适配器、本地实时用量、状态聚合。
所有持久化文件在 %APPDATA%/QuotaHUD/ 下；sk-key/cookie 只落本机。
"""
import json
import os
import re
import sqlite3
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta

APP = "QuotaHUD"
CFG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP)
os.makedirs(CFG_DIR, exist_ok=True)
STATIONS_F = os.path.join(CFG_DIR, "stations.json")
CREDS_F = os.path.join(CFG_DIR, "credentials.json")
STATE_F = os.path.join(CFG_DIR, "state.json")
CC_SWITCH_DB = os.environ.get("CC_SWITCH_DB") or os.path.expanduser(r"~/.cc-switch/cc-switch.db")

CST = timezone(timedelta(hours=8))


# ---------------- 配置与凭证 ----------------
def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    if os.path.exists(path):
        try:
            os.replace(path, path + ".bak")
        except OSError:
            pass
    os.replace(tmp, path)


_lock = threading.Lock()


def stations() -> list:
    with _lock:
        return load_json(STATIONS_F, [])


def creds() -> dict:
    with _lock:
        return load_json(CREDS_F, {})


def save_stations(lst):
    with _lock:
        save_json(STATIONS_F, lst)


def save_creds(d):
    with _lock:
        save_json(CREDS_F, d)


def default_stations():
    """内置站点模板（仅示例，可删可改）。凭证一律留空，由设置页填写或自动提取。
    你私有的中转站请在此处或设置页里替换成自己的地址。"""
    return [
        {"id": "deepseek", "name": "DeepSeek", "type": "deepseek",
         "url": "https://api.deepseek.com", "enabled": True, "warn_pct": 50},
        {"id": "bigmodel", "name": "智谱 GLM Coding", "type": "bigmodel",
         "url": "https://bigmodel.cn", "enabled": False, "warn_pct": 80},
        {"id": "volc_agent", "name": "火山 Agent Plan", "type": "volc_agent",
         "url": "https://console.volcengine.com", "enabled": False, "warn_pct": 80},
        {"id": "volc_coding", "name": "火山 Coding Plan", "type": "volc_coding",
         "url": "https://console.volcengine.com", "enabled": False, "warn_pct": 80},
        # 中转站模板：把 url 换成你自己的站点即可（大多数中转站都是 new-api/one-api 系）
        {"id": "relay_a", "name": "中转站 A", "type": "newapi",
         "url": "https://your-relay-site.example", "enabled": False, "warn_pct": 20},
        {"id": "relay_b", "name": "中转站 B（cookie 轮换型）", "type": "newapi_refresh",
         "url": "https://your-refresh-relay.example", "enabled": False, "warn_pct": 20},
        {"id": "gateway", "name": "自建网关", "type": "gateway",
         "url": "https://your-gateway.example", "enabled": False, "warn_pct": 20},
    ]


# ---------------- HTTP ----------------
def http(url, method="GET", headers=None, body=None, timeout=10):
    h = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126", "Accept": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=(body.encode() if isinstance(body, str) else body), headers=h, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read(65536).decode("utf-8", "replace"), r.headers


def jget(url, headers=None, timeout=10, method="GET", body=None, resp_headers=None):
    st, t, hd = http(url, method=method, headers=headers, body=body, timeout=timeout)
    if resp_headers is not None:
        resp_headers.update(hd)
    return st, json.loads(t)


# ---------------- 适配器：每类站点一个 query(station, cred) -> dict ----------------
# 返回统一结构: {ok, main, sub, used, limit, unit, pct, reset_at, raw_err}

def _r(ok, main="—", sub="", used=None, limit=None, unit="", pct=None, reset_at=None, err=None,
       server_tokens_24h=None):
    return {"ok": ok, "main": main, "sub": sub, "used": used, "limit": limit,
            "unit": unit, "pct": pct, "reset_at": reset_at, "err": err,
            "server_tokens_24h": server_tokens_24h}


def q_newapi(st, cred):
    """new-api 系：session cookie + New-Api-User 头 -> /api/user/self + /api/data/self(服务器口径用量)"""
    uid, cookie = cred.get("user_id"), cred.get("cookie")
    if not (uid and cookie):
        return _r(False, err="需要在设置里粘贴 Cookie 和用户ID")
    try:
        stt, j = jget(st["url"].rstrip("/") + "/api/user/self",
                      {"Cookie": cookie, "New-Api-User": str(uid)})
        if stt != 200 or not j.get("success", True):
            return _r(False, err=f"HTTP {stt}")
        d = j.get("data", {})
        quota = float(d.get("quota", 0))
        qpu = float(cred.get("quota_per_unit", 500000))
        usd = quota / qpu
        # 服务器口径：全设备 token 统计（近24h，按小时聚合）
        server_tok = _server_side_tokens(st, cred)
        sub_extra = f" · 服务器24h {server_tok}" if server_tok else ""
        return _r(True, main=f"${usd:.2f}", sub=f"已用 ${float(d.get('used_quota',0))/qpu:.2f}{sub_extra}",
                  used=float(d.get("used_quota", 0)) / qpu, limit=usd, unit="USD",
                  server_tokens_24h=_server_side_tokens_raw(st, cred))
    except Exception as e:
        return _r(False, err=str(e)[:120])


def _sum_tokens_from_data_self(st, cred, hours=24):
    """/api/data/self：new-api 标准聚合接口，返回全设备消耗（含其他电脑）"""
    uid, cookie = cred.get("user_id"), cred.get("cookie")
    if not (uid and cookie):
        return None
    try:
        ts = int(time.time())
        stt, j = jget(st["url"].rstrip("/") +
                      f"/api/data/self?start_timestamp={ts - hours*3600}&end_timestamp={ts}&default_time=hour",
                      {"Cookie": cookie, "New-Api-User": str(uid)})
        if stt != 200 or not j.get("success", True):
            return None
        items = j.get("data") or []
        if isinstance(items, dict):
            items = items.get("items") or items.get("data") or []
        total = 0
        for it in items:
            for key in ("token_used", "tokens", "quota_data", "token"):
                if key in it:
                    total += int(it.get(key) or 0)
                    break
        return total if total > 0 else None
    except Exception:
        return None


def _server_side_tokens_raw(st, cred):
    """各类型站点的服务器口径 24h token；拿不到返回 None"""
    t = st.get("type")
    try:
        if t == "newapi":
            return _sum_tokens_from_data_self(st, cred)
        if t == "newapi_refresh":
            cookie = cred.get("cookie")
            if not cookie:
                return None
            rh = {}
            stt, j = jget(st["url"].rstrip("/") + "/api/user/auth/refresh",
                          {"Cookie": cookie}, method="POST", body="", resp_headers=rh)
            if stt != 200:
                return None
            new_ck = rh.get("Set-Cookie") or ""
            if new_ck:
                m = re.search(r"new_api_refresh=([^;]+)", new_ck)
                if m and m.group(1) not in (cookie or ""):
                    sid = st.get("id")
                    c = creds()
                    c.setdefault(sid, {})["cookie"] = f"new_api_refresh={m.group(1)}"
                    save_creds(c)
            at = (j.get("data") or {}).get("access_token")
            if not at:
                return None
            uid = str(json.loads(__import__("base64").urlsafe_b64decode(at.split(".")[1] + "==").decode()).get("sub", ""))
            ts = int(time.time())
            stt2, j2 = jget(st["url"].rstrip("/") +
                            f"/api/data/self?start_timestamp={ts - 24*3600}&end_timestamp={ts}&default_time=hour",
                            {"Authorization": "Bearer " + at, "New-Api-User": uid, "Cookie": cookie})
            items = (j2.get("data") or []) if stt2 == 200 else []
            total = 0
            for it in items:
                for key in ("token_used", "tokens", "quota_data", "token"):
                    if key in it:
                        total += int(it.get(key) or 0)
                        break
            return total if total > 0 else None
        if t == "sub2":
            tok = cred.get("token")
            if not tok:
                return None
            # 该站响应恒被截断在 65120 字节，JSON 不完整 → 用正则抽数完整记录
            stt, t, _ = http(st["url"].rstrip("/") + "/api/v1/usage?limit=100",
                             headers={"Authorization": "Bearer " + tok})
            if stt != 200:
                return None
            matches = re.findall(r'"input_tokens":(\d+),"output_tokens":(\d+),'
                                 r'"cache_creation_tokens":(\d+),"cache_read_tokens":(\d+)', t)
            total = sum(int(a) + int(b) + int(c) + int(d) for a, b, c, d in matches)
            return total if total > 0 else None
    except Exception:
        return None
    return None


def _server_side_tokens(st, cred):
    v = _server_side_tokens_raw(st, cred)
    return f"{v/1e4:.0f}万" if v and v >= 1e4 else (str(v) if v else None)


# ---------------- 服务器口径：按天 token（全设备统计，含其他电脑） ----------------
# 站点 id -> 本机 cc-switch 里的 provider 名（用于去重与兜底）。
# 若你的站点与本地 provider 名不一致，可在 stations.json 给该站加 "local_names": ["名字"] 覆盖。
STATION_LOCAL_NAMES = {
    "bigmodel": ["Zhipu GLM", "智谱"],
    "volc_agent": ["火山 Agent Plan"],
    "volc_coding": ["火山 Coding Plan"],
    "deepseek": ["DeepSeek"],
}

_daily_cache = {}      # sid -> {"ts": epoch, "days": {date: tokens}}
_daily_lock = threading.Lock()
DAILY_TTL = 600
_zk_lock = threading.Lock()      # 中科类 refresh 串行化（轮换 cookie）
_zk_token = {}                   # 短命 JWT 缓存


def _bearer_newapi_refresh(st, cred, force=False):
    """中科类：refresh 换短命 JWT，返回 (headers, cookie_now)。
    加锁 + 120s 缓存：refresh 会轮换 cookie，并发调用会互相作废。"""
    cookie = cred.get("cookie")
    if not cookie:
        return None, None
    with _zk_lock:
        if (not force and _zk_token.get("hdrs") and _zk_token.get("sid") == st.get("id")
                and time.time() - (_zk_token.get("ts") or 0) < 120):
            return _zk_token["hdrs"], _zk_token.get("cookie")
        try:
            rh = {}
            stt, j = jget(st["url"].rstrip("/") + "/api/user/auth/refresh",
                          {"Cookie": cookie}, method="POST", body="", resp_headers=rh)
            if stt != 200:
                return None, cookie
            new_ck = rh.get("Set-Cookie") or ""
            if new_ck:
                m = re.search(r"new_api_refresh=([^;]+)", new_ck)
                if m and m.group(1) not in cookie:
                    cookie = "new_api_refresh=" + m.group(1)
                    c = creds()
                    c.setdefault(st["id"], {})["cookie"] = cookie
                    save_creds(c)
            at = (j.get("data") or {}).get("access_token")
            if not at:
                return None, cookie
            try:
                import base64
                uid = str(json.loads(base64.urlsafe_b64decode(at.split(".")[1] + "==").decode()).get("sub", ""))
            except Exception:
                uid = ""
            hdrs = {"Authorization": "Bearer " + at, "New-Api-User": uid, "Cookie": cookie}
            _zk_token.update({"ts": time.time(), "hdrs": hdrs, "cookie": cookie, "sid": st.get("id")})
            return hdrs, cookie
        except Exception:
            return None, cookie


def _slice_newapi(st, hdrs, ts0, ts1):
    """new-api 单日窗口的 token 合计；失败返回 None"""
    try:
        stt, j = jget(st["url"].rstrip("/") +
                      f"/api/data/self?start_timestamp={ts0}&end_timestamp={ts1}&default_time=day", hdrs)
        if stt != 200 or not j.get("success", True):
            _log(f"slice {st.get('id')} http={stt} msg={str(j.get('message'))[:60]}")
            return None
        rows = j.get("data")
        if not isinstance(rows, list):
            return None
        return sum(int(r.get("token_used") or 0) for r in rows)
    except Exception as e:
        _log(f"slice {st.get('id')} err {e!r}")
        return None


def _slice_sub2(st, cred):
    """sub2：响应被服务端截断在 65120 字节，用正则抽 (created_at, tokens) 按天归集"""
    tok = cred.get("token")
    if not tok:
        return None
    try:
        stt, t, _ = http(st["url"].rstrip("/") + "/api/v1/usage?limit=100",
                         headers={"Authorization": "Bearer " + tok})
        if stt != 200:
            return None
        out = {}
        for rec in t.split('{"id":'):
            md = re.search(r'"created_at":"(\d{4}-\d{2}-\d{2})', rec)
            tk = re.search(r'"input_tokens":(\d+),"output_tokens":(\d+),'
                           r'"cache_creation_tokens":(\d+),"cache_read_tokens":(\d+)', rec)
            if md and tk:
                d = md.group(1)
                out[d] = out.get(d, 0) + sum(int(x) for x in tk.groups())
        return out or None
    except Exception:
        return None


def server_daily(st, cred, days=7):
    """站点服务器口径近 N 天 token（含其他电脑）；不支持/失败返回 None"""
    t = st.get("type")
    try:
        if t in ("newapi", "newapi_refresh"):
            if t == "newapi":
                uid, cookie = cred.get("user_id"), cred.get("cookie")
                if not (uid and cookie):
                    return None
                hdrs = {"Cookie": cookie, "New-Api-User": str(uid)}
            else:
                hdrs, _ = _bearer_newapi_refresh(st, cred)
                if not hdrs:
                    return None
            out = {}
            day0 = datetime.now(CST).replace(hour=0, minute=0, second=0, microsecond=0)
            for i in range(days - 1, -1, -1):
                d = day0 - timedelta(days=i)
                ts0 = int(d.timestamp())
                ts1 = ts0 + 86400 if i else int(time.time())
                v = _slice_newapi(st, hdrs, ts0, ts1)
                if v is not None:
                    out[d.strftime("%Y-%m-%d")] = v
                time.sleep(0.08)
            return out or None
        if t == "sub2":
            return _slice_sub2(st, cred)
    except Exception:
        return None
    return None


def refresh_daily_cache(force=False):
    """刷新各站服务器按天数据（TTL 内跳过）；多线程拉取，单站失败不影响其他站"""
    sts = [s for s in stations() if s.get("enabled", True)]
    cr = creds()
    now = time.time()
    todo = []
    for st in sts:
        sid = st["id"]
        with _daily_lock:
            c = _daily_cache.get(sid)
            if not force and c and now - c["ts"] < DAILY_TTL:
                continue
        todo.append(st)
    if not todo:
        return

    def work(st):
        try:
            return st, server_daily(st, cr.get(st["id"], {}))
        except Exception as e:
            _log(f"daily {st.get('id')} err {e!r}")
            return st, None

    with ThreadPoolExecutor(max_workers=4) as ex:
        for st, days in ex.map(work, todo):
            sid = st["id"]
            with _daily_lock:
                if days:
                    _daily_cache[sid] = {"ts": time.time(), "days": days}
                elif sid not in _daily_cache:
                    _daily_cache[sid] = {"ts": time.time(), "days": {}}


def _log(msg):
    try:
        with open(os.path.join(CFG_DIR, "worker.log"), "a", encoding="utf-8") as f:
            f.write(time.strftime("%m-%d %H:%M:%S ") + str(msg) + "\n")
    except Exception:
        pass


def _daily_worker():
    while not _stop:
        try:
            refresh_daily_cache()
        except Exception as e:
            try:
                import traceback
                with open(os.path.join(CFG_DIR, "worker.log"), "a", encoding="utf-8") as f:
                    f.write(time.strftime("%H:%M:%S ") + repr(e) + "\n" + traceback.format_exc() + "\n")
            except Exception:
                pass
        for _ in range(60):
            if _stop:
                break
            time.sleep(1)


def build_trend7(sts, local):
    """近 7 天趋势：站点服务器口径优先（全设备），本机 cc-switch 兜底，按站取较大值防漏计"""
    day0 = datetime.now(CST).replace(hour=0, minute=0, second=0, microsecond=0)
    dates = [(day0 - timedelta(days=6 - i)).strftime("%Y-%m-%d") for i in range(7)]
    loc_by_day_prov = local.get("trend7_by_provider") or {}
    prov2sid = {}
    for st in sts:
        for nm in list(STATION_LOCAL_NAMES.get(st["id"], [])) + list(st.get("local_names") or []):
            prov2sid[nm] = st["id"]
    loc_station, other = {}, {}
    for d, provs in loc_by_day_prov.items():
        for nm, tk in provs.items():
            sid = prov2sid.get(nm)
            if sid:
                loc_station.setdefault(sid, {})[d] = loc_station.setdefault(sid, {}).get(d, 0) + tk
            else:
                other.setdefault(d, {})[nm] = other.setdefault(d, {}).get(nm, 0) + tk
    with _daily_lock:
        cache = {k: dict(v.get("days") or {}) for k, v in _daily_cache.items()}
    out = []
    for d in dates:
        by, srv_hit = {}, False
        for st in sts:
            if not st.get("enabled", True):
                continue
            sid, name = st["id"], st.get("name", st["id"])
            srv = (cache.get(sid) or {}).get(d)
            if srv is not None:
                srv_hit = True
            v = max(int(srv or 0), int(loc_station.get(sid, {}).get(d, 0) or 0))
            if v:
                by[name] = v
        for nm, tk in (other.get(d) or {}).items():
            if tk:
                by[nm] = tk
        out.append({"date": d, "tokens": sum(by.values()), "by": by,
                    "source": "server" if srv_hit else "local"})
    return out


def q_newapi_refresh(st, cred):
    """中科改版：refresh cookie 换短命 access_token -> /api/user/self。
    注意：该类站点 refresh 会轮换 cookie（旧的作废），必须把 Set-Cookie 存回凭证。"""
    cookie = cred.get("cookie")
    if not cookie:
        return _r(False, err="需要在设置里粘贴 Cookie")
    if "=" not in cookie:  # 兼容只粘贴裸值的情况
        cookie = f"new_api_refresh={cookie}"
    try:
        hdrs, cookie = _bearer_newapi_refresh(st, {"cookie": cookie})
        if not hdrs:
            return _r(False, err="refresh 失败（cookie 可能过期，请在浏览器重新登录后抓取）")
        stt2, j2 = jget(st["url"].rstrip("/") + "/api/user/self", hdrs)
        if stt2 != 200:
            return _r(False, err=f"self HTTP {stt2}")
        d = j2.get("data", {})
        qpu = float(cred.get("quota_per_unit", 500000))
        bal = float(d.get("quota", 0)) / qpu
        used = float(d.get("used_quota", 0)) / qpu
        server_tok = _server_side_tokens(st, cred)
        sub = f"已用 ${used:.2f} · {d.get('request_count','?')} 次"
        if server_tok:
            sub += f" · 服务器24h {server_tok}"
        return _r(True, main=f"${bal:.2f}", sub=sub,
                  used=used, limit=bal + used, unit="USD")
    except Exception as e:
        return _r(False, err=str(e)[:120])


def q_bigmodel(st, cred):
    """智谱：cookie 里的 JWT 作 Bearer"""
    tok = cred.get("token")
    if not tok:
        return _r(False, err="需要在设置里粘贴 bigmodel_token_production 的值")
    ck = cred.get("cookie", "")
    try:
        stt, j = jget(st["url"].rstrip("/") + "/api/monitor/usage/quota/limit",
                      {"Authorization": "Bearer " + tok, "Cookie": ck})
        if stt != 200 or not j.get("success"):
            return _r(False, err=f"HTTP {stt}（token 可能过期）")
        lim = j["data"]["limits"]
        out, parts = [], []
        for l in lim:
            unit_name = {3: "5h", 6: "周"}.get(l.get("unit"), str(l.get("unit")))
            out.append((l.get("usage", 0), l.get("number", 1) * 1000 if False else l.get("number", 1)))
            parts.append(f"{unit_name} {l.get('remaining','?')}/{l.get('usage','?')}")
        # limits: usage=总量, currentValue=已用, remaining=剩余, number=窗口数(3=>5h,6=>周)
        five = next((l for l in lim if l.get("unit") == 3), None)
        week = next((l for l in lim if l.get("unit") == 6), None)
        main = f"5h剩 {five['remaining']}/{five['usage']}" if five else "额度正常"
        sub = f"周 {week['remaining']}/{week['usage']} · 窗口 {week['percentage']}%" if week else ""
        pct = (five["currentValue"] / five["usage"] * 100) if five and five.get("usage") else None
        reset = five.get("nextResetTime") if five else None
        return _r(True, main=main, sub=sub, used=five.get("currentValue") if five else None,
                  limit=five.get("usage") if five else None, unit="credit", pct=pct,
                  reset_at=(reset / 1000 if reset and reset > 1e12 else reset))
    except Exception as e:
        return _r(False, err=str(e)[:120])


def q_volc_agent(st, cred):
    """火山 Agent Plan：cookie GET AFPUsage（GET 免 CSRF）"""
    ck = cred.get("cookie")
    if not ck:
        return _r(False, err="需要在设置里粘贴 console.volcengine.com 的 Cookie")
    try:
        stt, j = jget(st["url"].rstrip("/") +
                      "/api/top/ark/cn-beijing/2024-01-01/GetAgentPlanAFPUsage?", {"Cookie": ck})
        r = j.get("Result")
        if stt != 200 or not r:
            return _r(False, err=f"HTTP {stt}（cookie 可能过期）")
        m = r["AFPMonthly"]
        f = r["AFPFiveHour"]
        pct = m["Used"] / m["Quota"] * 100 if m.get("Quota") else None
        reset = f.get("ResetTime")
        return _r(True,
                  main=f"月 {m['Used']:,.0f}/{m['Quota']:,.0f}",
                  sub=f"5h {f['Used']:,.0f}/{f['Quota']:,.0f} · 周 {r['AFPWeekly']['Used']:,.0f}/{r['AFPWeekly']['Quota']:,.0f}",
                  used=m.get("Used"), limit=m.get("Quota"), unit="AFP", pct=pct,
                  reset_at=(reset / 1000 if isinstance(reset, (int, float)) and reset > 1e11 else None))
    except Exception as e:
        return _r(False, err=str(e)[:120])


def q_volc_coding(st, cred):
    """火山 Coding Plan：GetCodingPlanUsage"""
    ck = cred.get("cookie")
    if not ck:
        return _r(False, err="需要在设置里粘贴 console.volcengine.com 的 Cookie")
    try:
        stt, j = jget(st["url"].rstrip("/") +
                      "/api/top/ark/cn-beijing/2024-01-01/GetCodingPlanUsage?", {"Cookie": ck})
        r = j.get("Result")
        if stt != 200 or not r:
            return _r(False, err=f"HTTP {stt}")
        qs = {q["Level"]: q for q in r.get("QuotaUsage", [])}
        mo = qs.get("monthly", {})
        wk = qs.get("weekly", {})
        se = qs.get("session", {})
        pct = mo.get("Percent")
        reset = wk.get("ResetTimestamp")
        return _r(True,
                  main=f"月 {pct:.1f}%" if pct is not None else "—",
                  sub=f"session {se.get('Percent',0):.0f}% · 周 {wk.get('Percent',0):.0f}%",
                  pct=pct, unit="pct",
                  reset_at=(reset if isinstance(reset, (int, float)) and reset > 1e9 else None))
    except Exception as e:
        return _r(False, err=str(e)[:120])


def q_gateway(st, cred):
    """自建网关：Bearer token -> /api/v1/auth/me（部分网关 usage 明细接口）"""
    tok = cred.get("token")
    if not tok:
        return _r(False, err="需要在设置里粘贴 auth_token")
    try:
        stt, j = jget(st["url"].rstrip("/") + "/api/v1/auth/me", {"Authorization": "Bearer " + tok})
        if stt != 200:
            return _r(False, err=f"HTTP {stt}（token 过期则去设置里更新）")
        d = j.get("data", {})
        bal = float(d.get("balance", 0))
        server_tok = _server_side_tokens(st, cred)
        sub = f"冻结 ${float(d.get('frozen_balance',0)):.2f} · 并发 {d.get('concurrency','?')}"
        if server_tok:
            sub = f"服务器24h {server_tok} tok · " + sub
        return _r(True, main=f"${bal:.2f}", sub=sub,
                  used=None, limit=bal, unit="USD")
    except Exception as e:
        return _r(False, err=str(e)[:120])


def q_deepseek(st, cred):
    """DeepSeek 官方：sk-key -> /user/balance"""
    key = cred.get("key")
    if not key:
        return _r(False, err="需要在设置里粘贴 API Key")
    try:
        stt, j = jget(st["url"].rstrip("/") + "/user/balance", {"Authorization": "Bearer " + key})
        if stt != 200:
            return _r(False, err=f"HTTP {stt}")
        infos = j.get("balance_infos", [])
        if not infos:
            return _r(False, err="无余额数据")
        b = infos[0]
        amt = float(b.get("total_balance", 0))
        cur = b.get("currency", "CNY")
        sym = "¥" if cur == "CNY" else "$"
        return _r(True, main=f"{sym}{amt:.2f}", sub="官方余额接口", limit=amt, unit=cur)
    except Exception as e:
        return _r(False, err=str(e)[:120])


def q_bearer_generic(st, cred):
    """通用自定义：GET url + Bearer key，用 JSONPath 式点路径取值"""
    key = cred.get("key")
    path = cred.get("value_path", "data.balance")
    try:
        stt, j = jget(cred.get("query_url") or st["url"], {"Authorization": "Bearer " + (key or "")})
        if stt != 200:
            return _r(False, err=f"HTTP {stt}")
        cur = j
        for p in path.split("."):
            cur = cur.get(p) if isinstance(cur, dict) else None
            if cur is None:
                break
        if cur is None:
            return _r(False, err=f"路径 {path} 取不到值")
        return _r(True, main=f"{cred.get('unit_prefix','')}{float(cur):.2f}", sub=st.get("name", ""), limit=float(cur), unit=cred.get("unit", ""))
    except Exception as e:
        return _r(False, err=str(e)[:120])


ADAPTERS = {
    "newapi": q_newapi,
    "newapi_refresh": q_newapi_refresh,
    "bigmodel": q_bigmodel,
    "volc_agent": q_volc_agent,
    "volc_coding": q_volc_coding,
    "gateway": q_gateway,       # 自建网关（/api/v1/auth/me 风格）
    "sub2": q_gateway,          # 旧类型名，兼容已有配置
    "deepseek": q_deepseek,
    "bearer_generic": q_bearer_generic,
}

q_sub2 = q_gateway              # 兼容旧引用


# ---------------- 本地实时用量（cc-switch.db 只读） ----------------
def local_usage():
    out = {"available": False, "today_cost": None, "today_requests": None, "today_tokens": None,
           "rpm": 0, "last_req_ago": None, "last_model": None, "last_provider": None,
           "trend14": [], "trend7_tokens": [], "per_provider_today": []}
    if not os.path.exists(CC_SWITCH_DB):
        return out
    try:
        db = sqlite3.connect(f"file:{CC_SWITCH_DB}?mode=ro", uri=True, timeout=3)
        db.row_factory = sqlite3.Row
        now = int(time.time())
        today0 = int(datetime.now(CST).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
        r = db.execute("SELECT COUNT(*), COALESCE(SUM(CAST(total_cost_usd AS REAL)),0), "
                       "COALESCE(SUM(input_tokens+output_tokens+cache_read_tokens+cache_creation_tokens),0) "
                       "FROM proxy_request_logs WHERE created_at >= ?", (today0,)).fetchone()
        out.update({"available": True, "today_cost": r[1], "today_requests": r[0], "today_tokens": r[2]})
        r = db.execute("SELECT COUNT(*) c FROM proxy_request_logs WHERE created_at >= ?", (now - 60,)).fetchone()
        out["rpm"] = r["c"]
        r = db.execute("SELECT created_at, model, provider_id FROM proxy_request_logs ORDER BY created_at DESC LIMIT 1").fetchone()
        if r:
            out["last_req_ago"] = now - r["created_at"]
            out["last_model"] = r["model"]
            pid = r["provider_id"]
            row = db.execute("SELECT name FROM providers WHERE id=?", (pid,)).fetchone()
            out["last_provider"] = row["name"] if row else pid
        # 近 7 天（本地今日为终点，向前 7 天，缺日补零）
        day0 = datetime.now(CST).replace(hour=0, minute=0, second=0, microsecond=0)
        start = int((day0 - timedelta(days=6)).timestamp())
        q = ("SELECT date(created_at,'unixepoch','+8 hours') d, "
             "SUM(input_tokens+output_tokens+cache_read_tokens+cache_creation_tokens) tk "
             "FROM proxy_request_logs WHERE created_at>=? GROUP BY d")
        byday = {r["d"]: (r["tk"] or 0) for r in db.execute(q, (start,))}
        for i in range(7):
            d = (day0 - timedelta(days=6 - i)).strftime("%Y-%m-%d")
            out["trend7_tokens"].append({"date": d, "tokens": byday.get(d, 0)})
        # 近 7 天 × 各站 token（交互柱图的悬浮占比用）
        q2 = ("SELECT date(l.created_at,'unixepoch','+8 hours') d, p.name n, "
              "SUM(l.input_tokens+l.output_tokens+l.cache_read_tokens+l.cache_creation_tokens) tk "
              "FROM proxy_request_logs l JOIN providers p ON p.id=l.provider_id "
              "WHERE l.created_at>=? GROUP BY d, l.provider_id")
        bydayprov = {}
        for r in db.execute(q2, (start,)):
            bydayprov.setdefault(r["d"], {})[r["n"]] = (r["tk"] or 0)
        out["trend7_by_provider"] = bydayprov
        for r in db.execute("SELECT p.name n, SUM(CAST(l.total_cost_usd AS REAL)) c, COUNT(*) k "
                            "FROM proxy_request_logs l JOIN providers p ON p.id=l.provider_id "
                            "WHERE l.created_at>=? GROUP BY l.provider_id ORDER BY c DESC LIMIT 8", (today0,)):
            out["per_provider_today"].append({"name": r["n"], "cost": r["c"], "requests": r["k"]})
        db.close()
    except Exception:
        pass
    return out


# ---------------- 聚合轮询 ----------------
_poll_thread = None
_stop = False


def poll_once():
    sts = stations()
    cr = creds()
    state = load_json(STATE_F, {"stations": {}})
    results = {}
    last_change = state.get("last_change") or {}
    for st in sts:
        if not st.get("enabled", True):
            continue
        sid = st["id"]
        fn = ADAPTERS.get(st["type"])
        if not fn:
            results[sid] = _r(False, err=f"未知类型 {st['type']}")
            continue
        res = fn(st, cr.get(sid, {}))
        prev = state.get("stations", {}).get(sid, {})
        res["ts"] = int(time.time())
        res["name"] = st.get("name", sid)
        res["warn_pct"] = st.get("warn_pct", 20)
        res["prev_main"] = prev.get("main")
        # ---- 余额变化检测（迷你胶囊数据源）----
        # 取"第一个不以 h/% 结尾的数字"作为该站特征值（跳过 "5h" 前缀）；
        # 上一次的特征值随 state 存储（prev.num），不再从旧文本反解
        mm = re.search(r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?![h])", res.get("main") or "") if res.get("ok") else None
        cur_num = float(mm.group(0).replace(",", "")) if mm else None
        prev_num = prev.get("num")
        try:
            prev_num = float(prev_num) if prev_num is not None else None
        except (TypeError, ValueError):
            prev_num = None
        res["num"] = cur_num
        if res.get("ok") and cur_num is not None and (prev_num is None or abs(cur_num - prev_num) > 1e-9):
            lc_prev = last_change.get("num")
            if prev_num is not None and (lc_prev is None or res["ts"] >= (last_change.get("ts") or 0)):
                last_change = {
                    "sid": sid, "name": res["name"], "main": res["main"],
                    "num": cur_num, "prev": prev_num, "ts": res["ts"],
                    "up": cur_num > prev_num,
                }
        results[sid] = res
        time.sleep(0.15)
    state["stations"] = results
    state["local"] = local_usage()
    if not _daily_cache:            # 首次：先补齐服务器按天数据，避免柱图先显示本机兜底值
        try:
            refresh_daily_cache()
        except Exception as e:
            _log(f"first daily fill err {e!r}")
    try:
        state["trend7"] = build_trend7(sts, state["local"])
    except Exception:
        state["trend7"] = None
    state["last_change"] = last_change
    state["updated_at"] = int(time.time())
    save_json(STATE_F, state)
    return state


def _poll_loop(interval=180):
    while not _stop:
        try:
            poll_once()
        except Exception:
            pass
        for _ in range(int(interval)):
            if _stop:
                break
            time.sleep(1)


def start_poller(interval=180):
    global _poll_thread, _stop
    _stop = False
    if _poll_thread and _poll_thread.is_alive():
        return
    _poll_thread = threading.Thread(target=_poll_loop, args=(interval,), daemon=True)
    _poll_thread.start()
    threading.Thread(target=_daily_worker, daemon=True).start()


def stop_poller():
    global _stop
    _stop = True
