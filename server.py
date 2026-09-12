"""
CryptoDog（加密狗）跟单站 v1 · 后端
====================================
独立新站（非仪表盘内页）。端口 8100，与仪表盘 8000 并存，**不改动仪表盘任何文件**。

设计原则
--------
1. **数据层共用**：仪表盘继续做「研究/监控」，CryptoDog 做「执行准备」。
   链上数据（KOL 持仓/交易流）通过仪表盘既有 API 读取（只读代理），
   不复制采集逻辑、不写仪表盘文件。
2. **只读优先（v1 安全边界）**：本服务**没有任何真实下单/签名/广播能力**。
   `POST /api/copy/execute` 一律拒绝执行并落库为 blocked —— 这是刻意的，
   对应规格 §5「v1 默认推送提醒 + 人工确认，自动跟单总开关默认关闭」。
3. **风控服务端强制**：任何预设/执行请求都先过 `validate_*`，前端无法绕过。
4. KOL 增删改只写 `cryptodog/data/kol_registry.json`（本站自己的文件）。

对外依赖
--------
- 仪表盘后端 `http://127.0.0.1:8000`（读 /api/wallets/positions、/api/wallets/trades）
  未运行时相关接口返回明确错误，其余页面仍可用。
- 采集脚本（需手动或 cron 运行）：
  `cryptodog/collectors/futures_market.py`、`futures_traders.py`

用法：D:/project/.venv/Scripts/python.exe cryptodog/server.py
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from fastapi import FastAPI, Body
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

CRYPTODOG_ROOT = Path("D:/project/cryptodog")
DATA_DIR = CRYPTODOG_ROOT / "data"
ASSETS_DIR = CRYPTODOG_ROOT / "assets"
INDEX_FILE = CRYPTODOG_ROOT / "index.html"

MARKET_FILE = DATA_DIR / "futures_market.json"
TRADERS_FILE = DATA_DIR / "futures_traders.json"
KOL_REGISTRY_FILE = DATA_DIR / "kol_registry.json"
PRESETS_FILE = DATA_DIR / "copy_presets.json"
COPY_LOG_FILE = DATA_DIR / "copy_log.json"
COPY_STATE_FILE = DATA_DIR / "copy_state.json"

# 仪表盘（只读依赖）
DASHBOARD_API = "http://127.0.0.1:8000"
DASHBOARD_WALLET_CONFIG = Path("D:/project/data/config/wallet_addresses.json")
HTTP_TIMEOUT = 30

PORT = 8100

# KOL 信号统计 + 可解释研判（规格 §3.1b ②③④；本文件同目录）
sys.path.insert(0, str(CRYPTODOG_ROOT))
import signals as sg  # noqa: E402

# ============================================================
# 风控硬性上限（服务端强制，前端不可绕过；规格 §5）
# ============================================================
RISK_LIMITS = {
    "futures": {
        "max_leverage": 20,          # 杠杆上限（超过则放弃该信号）
        "max_order_usdt": 2000,      # 单笔跟单金额
        "max_symbol_usdt": 5000,     # 单币最大持仓
        "max_daily_usdt": 10000,     # 每日金额上限
        "max_daily_orders": 20,      # 每日笔数上限
        "min_stop_loss_pct": -20,    # 止损线不得低于 -20%
    },
    "meme": {
        "max_order_sol": 2.0,        # 单笔跟单金额
        "max_daily_sol": 5.0,        # 每日金额上限
        "max_daily_orders": 10,      # 每日笔数上限
        "max_slippage_pct": 10,      # 滑点上限
        "min_stop_loss_pct": -50,    # 止损线不得低于 -50%
    },
}

app = FastAPI(title="CryptoDog API", version="1.0")
if ASSETS_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")


# ============================================================
# 工具
# ============================================================
def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 读取 {path} 失败: {e}", file=sys.stderr)
        return default


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _dash_get(path: str, params: dict | None = None):
    """只读代理仪表盘 API；失败返回 (None, 错误说明)"""
    try:
        r = requests.get(DASHBOARD_API + path, params=params, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        return r.json(), None
    except Exception as e:  # noqa: BLE001
        return None, (f"仪表盘后端不可用（{DASHBOARD_API}）：{type(e).__name__}。"
                      f"请先启动 D:/project/scripts/dashboard_server.py")


def _num(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# ============================================================
# 主页链接识别（规格 3.1a：粘贴链接式，两段式登记）
# ============================================================
def parse_profile_url(url: str) -> dict:
    """识别平台与地址：
    - pump.fun/profile/<SOL地址>  → platform=pumpfun，地址直接入库
    - fomo.family/profile/<handle> → platform=fomo，SOL 地址待补
    """
    u = (url or "").strip()
    if not u:
        return {"ok": False, "error": "主页链接不能为空"}
    if not re.match(r"^https?://", u, re.I):
        u = "https://" + u

    m = re.search(r"pump\.fun/profile/([A-Za-z0-9]+)", u, re.I)
    if m:
        addr = m.group(1)
        # Solana 地址为 base58、长度 32-44
        has_addr = 32 <= len(addr) <= 44
        return {
            "ok": True, "platform": "pumpfun", "address": addr if has_addr else "",
            "profile_url": u,
            "note": "pump.fun 主页路径即 SOL 地址，地址已直接入库" if has_addr else "路径不像 SOL 地址，请手工补地址",
        }

    m = re.search(r"fomo\.family/profile/([^/?#]+)", u, re.I)
    if m:
        return {
            "ok": True, "platform": "fomo", "address": "", "handle": m.group(1),
            "profile_url": u,
            "note": "FOMO 主页只暴露 handle，不暴露 SOL 地址 → 地址待补（两段式）",
        }

    return {"ok": False, "error": "无法识别平台：仅支持 pump.fun/profile/… 或 fomo.family/profile/…"}


def _seed_registry_from_dashboard() -> list[dict]:
    """首次运行：从仪表盘配置只读导入已有 KOL（不写仪表盘文件）"""
    cfg = _read_json(DASHBOARD_WALLET_CONFIG, {})
    out = []
    for w in (cfg.get("wallets") or []):
        name = w.get("name")
        if not name:
            continue
        url = w.get("profile_url") or ""
        plat = w.get("platform") or ("fomo" if "fomo.family" in url else "pumpfun" if "pump.fun" in url else "")
        out.append({
            "name": name,
            "platform": plat,
            "address": w.get("address") or "",
            "profile_url": url,
            "source": "imported_from_dashboard",
            "synced_to_dashboard": True,
            "created_at": _now(),
        })
    return out


def load_registry(seed: bool = True) -> dict:
    reg = _read_json(KOL_REGISTRY_FILE, None)
    if reg is None:
        kols = _seed_registry_from_dashboard() if seed else []
        reg = {"updated_at": _now(), "kols": kols}
        if kols:
            _write_json(KOL_REGISTRY_FILE, reg)
    reg.setdefault("kols", [])
    return reg


def save_registry(reg: dict) -> None:
    reg["updated_at"] = _now()
    _write_json(KOL_REGISTRY_FILE, reg)


def _find_kol(reg: dict, name: str) -> dict | None:
    for k in reg.get("kols", []):
        if k.get("name") == name:
            return k
    return None


# ============================================================
# 风控校验
# ============================================================
def validate_futures_preset(p: dict) -> dict:
    L = RISK_LIMITS["futures"]
    fields, errors = [], []

    def chk(key, label, val, lo, hi, unit=""):
        v = _num(val)
        if v is None:
            errors.append(f"{label} 必须是数字")
            return None
        if hi is not None and v > hi:
            fields.append({"field": key, "level": "clamped", "from": v, "to": hi,
                           "msg": f"{label} 超过服务端上限 {hi}{unit}，已按上限执行"})
            return hi
        if lo is not None and v < lo:
            fields.append({"field": key, "level": "clamped", "from": v, "to": lo,
                           "msg": f"{label} 低于服务端下限 {lo}{unit}，已按下限执行"})
            return lo
        return v

    out = {
        "mode": p.get("mode") or "fixed",
        "order_usdt": chk("order_usdt", "单笔跟单金额", p.get("order_usdt", 500), 1, L["max_order_usdt"], " USDT"),
        "max_leverage": chk("max_leverage", "杠杆上限", p.get("max_leverage", 20), 1, L["max_leverage"], "x"),
        "max_symbol_usdt": chk("max_symbol_usdt", "单币最大持仓", p.get("max_symbol_usdt", 2000), 1, L["max_symbol_usdt"], " USDT"),
        "stop_loss_pct": chk("stop_loss_pct", "止损线", p.get("stop_loss_pct", -8), L["min_stop_loss_pct"], -1, "%"),
        "take_profit_mode": p.get("take_profit_mode") or "follow",
        "whitelist_only": bool(p.get("whitelist_only")),
        "whitelist": [s.strip().upper() for s in (p.get("whitelist") or []) if str(s).strip()],
        "confirm_before_execute": True,   # 服务端强制：v1 必须人工确认
        "auto_execute": False,           # 服务端强制：v1 不允许自动执行
    }
    return {"ok": not errors, "errors": errors, "fields": fields, "preset": out, "limits": L}


def validate_meme_preset(p: dict) -> dict:
    L = RISK_LIMITS["meme"]
    fields, errors = [], []

    def chk(key, label, val, lo, hi, unit=""):
        v = _num(val)
        if v is None:
            errors.append(f"{label} 必须是数字")
            return None
        if hi is not None and v > hi:
            fields.append({"field": key, "level": "clamped", "from": v, "to": hi,
                           "msg": f"{label} 超过服务端上限 {hi}{unit}，已按上限执行"})
            return hi
        if lo is not None and v < lo:
            fields.append({"field": key, "level": "clamped", "from": v, "to": lo,
                           "msg": f"{label} 低于服务端下限 {lo}{unit}，已按下限执行"})
            return lo
        return v

    out = {
        "target": p.get("target") or "",
        "order_sol": chk("order_sol", "单笔跟单金额", p.get("order_sol", 0.5), 0.001, L["max_order_sol"], " SOL"),
        "daily_orders": chk("daily_orders", "每日笔数上限", p.get("daily_orders", 5), 1, L["max_daily_orders"], " 笔"),
        "daily_sol": chk("daily_sol", "每日金额上限", p.get("daily_sol", 3), 0.01, L["max_daily_sol"], " SOL"),
        "slippage_pct": chk("slippage_pct", "买入滑点上限", p.get("slippage_pct", 3), 0.1, L["max_slippage_pct"], "%"),
        "stop_loss_pct": chk("stop_loss_pct", "止损线", p.get("stop_loss_pct", -30), L["min_stop_loss_pct"], -1, "%"),
        "take_profit_mode": p.get("take_profit_mode") or "follow",
        "clear_on_kol_exit": bool(p.get("clear_on_kol_exit", True)),
        "blacklist": [s.strip() for s in (p.get("blacklist") or []) if str(s).strip()],
        # 自动跟单总开关默认关（规格 §5.1）；即使前端传 true，也需人工在服务端开关处确认
        "auto_execute": False,
        "confirm_before_execute": True,
    }
    return {"ok": not errors, "errors": errors, "fields": fields, "preset": out, "limits": L}


# ============================================================
# 读取类接口
# ============================================================
@app.get("/api/health")
def health():
    market = _read_json(MARKET_FILE, {})
    traders = _read_json(TRADERS_FILE, {})
    return {
        "ok": True,
        "service": "cryptodog",
        "port": PORT,
        "now": _now(),
        "data": {
            "futures_market_updated_at": market.get("updated_at"),
            "futures_traders_updated_at": traders.get("updated_at"),
            "trader_count": (traders.get("stats") or {}).get("trader_count", 0),
        },
        "execution_enabled": False,
        "execution_note": "v1 只读：未接入任何交易所下单权限，/api/copy/execute 一律拒绝",
        "risk_limits": RISK_LIMITS,
    }


@app.get("/api/futures/market")
def futures_market():
    d = _read_json(MARKET_FILE, None)
    if d is None:
        return {"error": "尚无市场数据，请先运行 cryptodog/collectors/futures_market.py"}
    return d


@app.get("/api/futures/traders")
def futures_traders():
    d = _read_json(TRADERS_FILE, None)
    if d is None:
        return {"traders": [], "error": "尚无交易员数据，请先运行 cryptodog/collectors/futures_traders.py"}
    return d


@app.get("/api/futures/refresh")
def futures_refresh(kind: str = "market"):
    """触发采集脚本（后台，独立进程）"""
    import subprocess
    script = "futures_market.py" if kind == "market" else "futures_traders.py"
    path = CRYPTODOG_ROOT / "collectors" / script
    if not path.exists():
        return JSONResponse(status_code=404, content={"ok": False, "error": f"脚本不存在: {path}"})
    try:
        subprocess.Popen([sys.executable, str(path)], cwd=str(CRYPTODOG_ROOT),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:  # noqa: BLE001
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})
    return {"ok": True, "message": f"已后台触发 {script}，约 30-60 秒后刷新页面"}


@app.get("/api/meme/kols")
def meme_kols():
    """本站 KOL 注册表 + 仪表盘实时数据（持仓市值/总盈亏/地址待补态）"""
    reg = load_registry()
    positions, err = _dash_get("/api/wallets/positions")
    live = {}
    if positions and not positions.get("error"):
        for k in (positions.get("kols") or []):
            live[k.get("wallet")] = k

    out = []
    for k in reg.get("kols", []):
        lv = live.get(k["name"], {})
        addr = (k.get("address") or "").strip()
        out.append({
            **k,
            "has_address": bool(addr),
            "value_usd": lv.get("value_usd"),
            "buy_usd": lv.get("buy_usd"),
            "pnl_usd": lv.get("pnl_usd"),
            "roi_pct": lv.get("roi_pct"),
            "cash_usd": lv.get("cash_usd"),
            "position_count": lv.get("position_count"),
            "last_active": lv.get("last_active"),
            "followers": lv.get("followers"),
        })
    return {"kols": out, "updated_at": reg.get("updated_at"), "dashboard_error": err}


@app.get("/api/meme/assets")
def meme_assets(wallet: str = "all", limit: int = 300):
    """KOL 资产 + 交易流：只读代理仪表盘（不复制其聚合逻辑）"""
    pos, err1 = _dash_get("/api/wallets/positions")
    tr, err2 = _dash_get("/api/wallets/trades", {"limit": limit})
    return {
        "positions": (pos or {}).get("positions", []) if not err1 else [],
        "kols": (pos or {}).get("kols", []) if not err1 else [],
        "trades": (tr or {}).get("trades", []) if not err2 else [],
        "total": (tr or {}).get("total"),
        "updated_at": (pos or {}).get("updated_at") or (tr or {}).get("updated_at"),
        "dashboard_error": err1 or err2,
    }


@app.get("/api/meme/crypto_hot")
def meme_crypto_hot(limit: int = 12):
    """热门代币：读仪表盘采集产物（只读）"""
    d = _read_json(Path("D:/project/data/dashboard/crypto_hot.json"), {})
    coins = d.get("coins") or []
    return {"coins": coins[:limit], "updated_at": d.get("updated_at")}


# ============================================================
# KOL 信号与「AI 重点关注」（规格 §3.1b ②③④，2026-09-12 16:56 改版）
# 三类信号全部由真实交易流统计：多次买入 / 大额买入 / 早期埋伏；
# 研判为**规则可解释版**（每条理由引用具体数字），未配置 LLM key 时如实标注 source=rules。
# ============================================================
@app.get("/api/meme/signals")
def meme_signals(kol: str = "all", refresh: int = 0):
    """指定 KOL 的信号统计 + 重点关注研判（默认读缓存，refresh=1 重算）"""
    if kol == "all" or not kol:
        return {"error": "请先选择单个 KOL（信号按 KOL 计算）", "watchlist": [], "signals": None}
    if not refresh:
        cached = sg.load_review(kol)
        if cached:
            return cached
    pos, err1 = _dash_get("/api/wallets/positions")
    tr, err2 = _dash_get("/api/wallets/trades", {"limit": 500})
    if err1 and err2:
        return JSONResponse(status_code=503, content={"error": err1})
    pos_rows = [p for p in ((pos or {}).get("positions") or []) if p.get("wallet") == kol]
    trade_rows = [t for t in ((tr or {}).get("trades") or []) if t.get("wallet") == kol]
    return sg.build_review(kol, trade_rows, pos_rows)


@app.post("/api/meme/signals/refresh")
def meme_signals_refresh(body: dict = Body(...)):
    """「↻ 重新分析」：强制重算该 KOL 的信号与研判"""
    kol = (body.get("kol") or "").strip()
    if not kol:
        return JSONResponse(status_code=400, content={"error": "缺少 kol 参数"})
    pos, err1 = _dash_get("/api/wallets/positions")
    tr, err2 = _dash_get("/api/wallets/trades", {"limit": 500})
    if err1 and err2:
        return JSONResponse(status_code=503, content={"error": err1})
    pos_rows = [p for p in ((pos or {}).get("positions") or []) if p.get("wallet") == kol]
    trade_rows = [t for t in ((tr or {}).get("trades") or []) if t.get("wallet") == kol]
    return sg.build_review(kol, trade_rows, pos_rows)


@app.get("/api/logo")
def logo_proxy(u: str):
    """代币 LOGO 代理：转发仪表盘的 /api/logo（浏览器直连不到多数 CDN/IPFS，
    且前端因此只需访问本站同源）。失败时回落 1x1 透明 PNG，前端 onerror 再降级为字母牌。"""
    from fastapi.responses import Response
    blank = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082")
    if not u or not re.match(r"^https?://", u, re.I):
        return Response(content=blank, media_type="image/png")
    try:
        r = requests.get(DASHBOARD_API + "/api/logo", params={"u": u}, timeout=HTTP_TIMEOUT)
        if r.status_code == 200 and r.content:
            return Response(content=r.content, media_type=r.headers.get("content-type", "image/png"))
    except Exception:  # noqa: BLE001
        pass
    return Response(content=blank, media_type="image/png")


# ============================================================
# KOL 管理（只写本站 kol_registry.json）
# ============================================================
@app.post("/api/kol/parse")
def kol_parse(body: dict = Body(...)):
    """只识别不落库（前端「自动识别」实时徽章用；避免预览产生垃圾记录）"""
    return {"ok": True, "parse": parse_profile_url(body.get("profile_url") or "")}


@app.post("/api/kol/add")
def kol_add(body: dict = Body(...)):
    name = (body.get("name") or "").strip()
    url = (body.get("profile_url") or "").strip()
    if not name:
        return JSONResponse(status_code=400, content={"ok": False, "error": "备注名不能为空"})
    parsed = parse_profile_url(url)
    if not parsed.get("ok"):
        return JSONResponse(status_code=400, content={"ok": False, "error": parsed.get("error")})

    reg = load_registry()
    if _find_kol(reg, name):
        return JSONResponse(status_code=409, content={"ok": False, "error": f"KOL「{name}」已存在"})

    address = (body.get("address") or "").strip() or parsed.get("address", "")
    reg["kols"].append({
        "name": name,
        "platform": parsed["platform"],
        "address": address,
        "profile_url": parsed["profile_url"],
        "source": "manual",
        "synced_to_dashboard": False,
        "created_at": _now(),
    })
    save_registry(reg)
    return {"ok": True, "message": f"已添加 {name}（{parsed['platform']}）", "parse": parsed,
            "address_pending": not bool(address)}


@app.post("/api/kol/update")
def kol_update(body: dict = Body(...)):
    name = (body.get("name") or "").strip()
    reg = load_registry()
    kol = _find_kol(reg, name)
    if not kol:
        return JSONResponse(status_code=404, content={"ok": False, "error": f"未找到 KOL「{name}」"})

    new_name = (body.get("new_name") or "").strip()
    if new_name and new_name != name:
        if _find_kol(reg, new_name):
            return JSONResponse(status_code=409, content={"ok": False, "error": f"「{new_name}」已存在"})
        kol["name"] = new_name

    if body.get("profile_url"):
        parsed = parse_profile_url(body["profile_url"])
        if not parsed.get("ok"):
            return JSONResponse(status_code=400, content={"ok": False, "error": parsed.get("error")})
        kol["platform"] = parsed["platform"]
        kol["profile_url"] = parsed["profile_url"]
        if not (body.get("address") or "").strip() and parsed.get("address"):
            kol["address"] = parsed["address"]

    if body.get("address") is not None:
        kol["address"] = (body.get("address") or "").strip()

    kol["updated_at"] = _now()
    save_registry(reg)
    return {"ok": True, "message": f"已更新 {kol['name']}", "kol": kol}


@app.post("/api/kol/delete")
def kol_delete(body: dict = Body(...)):
    name = (body.get("name") or "").strip()
    reg = load_registry()
    before = len(reg.get("kols", []))
    reg["kols"] = [k for k in reg.get("kols", []) if k.get("name") != name]
    if len(reg["kols"]) == before:
        return JSONResponse(status_code=404, content={"ok": False, "error": f"未找到 KOL「{name}」"})
    save_registry(reg)
    return {"ok": True, "message": f"已删除 {name}（本站注册表；仪表盘采集配置不受影响）"}


@app.post("/api/kol/sync")
def kol_sync(body: dict = Body(...)):
    """把本站 KOL 登记到仪表盘采集配置（调用仪表盘官方 API，不直接改其文件）"""
    name = (body.get("name") or "").strip()
    reg = load_registry()
    kol = _find_kol(reg, name)
    if not kol:
        return JSONResponse(status_code=404, content={"ok": False, "error": f"未找到 KOL「{name}」"})
    try:
        r = requests.post(DASHBOARD_API + "/api/wallets/add", json={
            "name": kol["name"], "address": kol.get("address") or "",
            "chains": ["SOL"], "platform": kol.get("platform") or "",
            "profile_url": kol.get("profile_url") or "",
        }, timeout=HTTP_TIMEOUT)
        data = r.json()
    except Exception as e:  # noqa: BLE001
        return JSONResponse(status_code=502, content={
            "ok": False, "error": f"仪表盘后端不可用（{DASHBOARD_API}）：{type(e).__name__}。请先启动它再同步"})
    if r.status_code >= 400 or not data.get("ok"):
        return JSONResponse(status_code=400, content={"ok": False, "error": data.get("error") or f"HTTP {r.status_code}"})
    kol["synced_to_dashboard"] = True
    save_registry(reg)
    return {"ok": True, "message": data.get("message"), "kol": kol}


# ============================================================
# 跟单预设与执行（执行永远拒绝 —— v1 安全边界）
# ============================================================
@app.get("/api/copy/presets")
def copy_presets():
    return {
        "futures": _read_json(PRESETS_FILE, {}).get("futures"),
        "meme": _read_json(PRESETS_FILE, {}).get("meme"),
        "state": _read_json(COPY_STATE_FILE, {"auto_execute": False, "kill_switch": False}),
        "limits": RISK_LIMITS,
    }


@app.post("/api/copy/preset")
def copy_preset_save(body: dict = Body(...)):
    kind = body.get("kind") or "meme"
    if kind not in ("futures", "meme"):
        return JSONResponse(status_code=400, content={"ok": False, "error": "kind 必须是 futures 或 meme"})
    result = validate_futures_preset(body) if kind == "futures" else validate_meme_preset(body)
    if not result["ok"]:
        return JSONResponse(status_code=400, content={"ok": False, "errors": result["errors"]})

    presets = _read_json(PRESETS_FILE, {})
    presets[kind] = {**result["preset"], "saved_at": _now()}
    _write_json(PRESETS_FILE, presets)
    return {
        "ok": True,
        "message": "预设已保存（服务端风控已生效；自动执行仍为关闭）",
        "preset": presets[kind],
        "adjustments": result["fields"],
        "limits": result["limits"],
    }


@app.post("/api/copy/execute")
def copy_execute(body: dict = Body(...)):
    """v1：一律拒绝真实执行。只落库一条 blocked 记录，供全链路追溯。"""
    kind = body.get("kind") or "meme"
    state = _read_json(COPY_STATE_FILE, {"auto_execute": False, "kill_switch": False})
    if state.get("kill_switch"):
        reason = "全局熔断已开启 —— 所有跟单已停止"
    else:
        reason = ("v1 只读安全边界：本服务未接入任何交易所下单权限/签名能力，"
                  "真实下单需人工在交易所执行。已记录本次请求（未产生任何链上或交易所动作）。")

    log = _read_json(COPY_LOG_FILE, {"records": []})
    rec = {
        "time": _now(),
        "kind": kind,
        "source": body.get("source") or "",
        "source_platform": body.get("source_platform") or "",
        "token": body.get("token") or "",
        "direction": body.get("direction") or "",
        "kol_amount": body.get("kol_amount"),
        "copy_amount": body.get("copy_amount"),
        "result": "blocked",
        "reason": reason,
    }
    log.setdefault("records", []).insert(0, rec)
    log["records"] = log["records"][:500]
    _write_json(COPY_LOG_FILE, log)
    return {"ok": False, "executed": False, "result": "blocked", "reason": reason, "record": rec}


@app.post("/api/copy/killswitch")
def copy_killswitch(body: dict = Body(...)):
    on = bool(body.get("on"))
    state = _read_json(COPY_STATE_FILE, {"auto_execute": False, "kill_switch": False})
    state["kill_switch"] = on
    state["auto_execute"] = False  # 熔断/恢复一律回到「自动执行关闭」
    state["updated_at"] = _now()
    _write_json(COPY_STATE_FILE, state)
    return {"ok": True, "kill_switch": on,
            "message": "已开启全局熔断，所有跟单停止" if on else "已解除熔断（自动执行仍为关闭）"}


@app.get("/api/copy/log")
def copy_log():
    return _read_json(COPY_LOG_FILE, {"records": []})


# ============================================================
# 前端
# ============================================================
@app.get("/")
def index():
    if not INDEX_FILE.exists():
        return JSONResponse(status_code=500, content={"error": f"缺少 {INDEX_FILE}"})
    return FileResponse(str(INDEX_FILE))


@app.get("/favicon.ico")
def favicon():
    f = ASSETS_DIR / "cryptodog-logo.svg"
    if f.exists():
        return FileResponse(str(f), media_type="image/svg+xml")
    return JSONResponse(status_code=404, content={"error": "no favicon"})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
