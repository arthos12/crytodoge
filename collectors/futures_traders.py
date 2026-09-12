"""
CryptoDog · 合约交易员采集（Hyperliquid 公开 API，免 key）
========================================================
数据源（全部公开、无需 API key）：
  - POST /info {"type":"metaAndAssetCtxs"}      全市场上下文（funding/OI/markPx）
  - POST /info {"type":"allMids"}               全币种中间价
  - POST /info {"type":"clearinghouseState"}    指定地址的永续仓位（含开仓价/强平价/杠杆/未实现盈亏）
  - POST /info {"type":"userFills"}             成交明细（closedPnl → 胜率；dir=Open * → 开仓时间）
  - POST /info {"type":"portfolio"}             账户价值历史（→ 最大回撤）
  - GET  stats-data.hyperliquid.xyz/Mainnet/leaderboard   交易员榜单（30d ROI）

输出：cryptodog/data/futures_traders.json

⚠ 诚实边界（不编造）：
  - Hyperliquid 榜单**不提供粉丝数**（规格原写粉丝数）→ 改展示「账户规模 + 30d 成交额」并标注口径
  - 开仓时间取自 userFills（最多返回最近 2000 笔）→ 找不到对应 Open 成交时显示 —
  - 榜单 ROI 会被小账户刷高 → 用「账户规模 ≥ 阈值 + 有持仓」双重过滤后再排序

⚠ 网络：走 Clash 代理；必须 pop NO_PROXY（继承坑）。

用法：
  python cryptodog/collectors/futures_traders.py                 # 默认 Top 8
  python cryptodog/collectors/futures_traders.py --top 12 --min-account 100000
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

os.environ.pop("NO_PROXY", None)
os.environ.pop("no_proxy", None)

import requests  # noqa: E402

PROXY = {"http": "http://127.0.0.1:7897", "https": "http://127.0.0.1:7897"}
HL_INFO = "https://api.hyperliquid.xyz/info"
HL_LEADERBOARD = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
HTTP_TIMEOUT = 30
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0"}

CRYPTODOG_ROOT = Path("D:/project/cryptodog")
OUTPUT_FILE = CRYPTODOG_ROOT / "data" / "futures_traders.json"

DEFAULT_TOP = 8
DEFAULT_MIN_ACCOUNT = 50_000.0
# 强平价接近标记价的警戒线（%）
LIQ_WARN_PCT = 3.0


def _post(payload: dict, timeout: int = HTTP_TIMEOUT):
    last = None
    for attempt in range(3):
        try:
            r = requests.post(HL_INFO, json=payload, headers=HEADERS, proxies=PROXY, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"HL POST {payload.get('type')} 失败: {last}")


def _get_json(url: str, timeout: int = 90):
    last = None
    for attempt in range(2):
        try:
            r = requests.get(url, headers=HEADERS, proxies=PROXY, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2.0)
    raise RuntimeError(f"GET {url} 失败: {last}")


def _f(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _window(row: dict, key: str) -> dict:
    for k, v in (row.get("windowPerformances") or []):
        if k == key and isinstance(v, dict):
            return v
    return {}


def fetch_leaderboard(top_n: int, min_account: float) -> list[dict]:
    """榜单 → 按 30d ROI 排序，过滤账户规模后取 Top N（要求有持仓的会在后面筛）"""
    data = _get_json(HL_LEADERBOARD)
    rows = data.get("leaderboardRows") or []
    cands = []
    for r in rows:
        acct = _f(r.get("accountValue"))
        if acct is None or acct < min_account:
            continue
        m = _window(r, "month")
        roi = _f(m.get("roi"))
        if roi is None:
            continue
        cands.append({
            "address": r.get("ethAddress"),
            "name": (r.get("displayName") or "").strip() or (r.get("ethAddress") or "")[:10],
            "account_value": acct,
            "roi_month": roi,
            "roi_week": _f(_window(r, "week").get("roi")),
            "roi_day": _f(_window(r, "day").get("roi")),
            "vlm_month": _f(_window(r, "month").get("vlm")),
        })
    cands.sort(key=lambda x: x["roi_month"] or -9, reverse=True)
    return cands[: max(top_n * 3, top_n)]  # 多取一些，后面筛「有持仓」


def fetch_mids() -> dict:
    try:
        mids = _post({"type": "allMids"})
        if isinstance(mids, dict):
            return {k: _f(v) for k, v in mids.items()}
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] allMids 失败: {e}", file=sys.stderr)
    return {}


def fetch_positions(address: str, mids: dict) -> list[dict]:
    cs = _post({"type": "clearinghouseState", "user": address})
    out = []
    for ap in (cs.get("assetPositions") or []):
        p = ap.get("position") or {}
        coin = p.get("coin")
        szi = _f(p.get("szi"))
        if not coin or szi is None or szi == 0:
            continue
        lev = p.get("leverage") or {}
        mark = mids.get(coin)
        liq = _f(p.get("liquidationPx"))
        liq_dist = None
        if mark and liq:
            liq_dist = abs(mark - liq) / mark * 100
        out.append({
            "coin": coin,
            "side": "long" if szi > 0 else "short",
            "szi": szi,
            "size_abs": abs(szi),
            "leverage": lev.get("value"),
            "leverage_type": lev.get("type"),
            "position_value_usd": _f(p.get("positionValue")),
            "entry_px": _f(p.get("entryPx")),
            "mark_px": mark,
            "liquidation_px": liq,
            "liq_distance_pct": liq_dist,
            "liq_warn": bool(liq_dist is not None and liq_dist < LIQ_WARN_PCT),
            "unrealized_pnl": _f(p.get("unrealizedPnl")),
            "roe_pct": (_f(p.get("returnOnEquity")) or 0) * 100 if p.get("returnOnEquity") is not None else None,
            "margin_used": _f(p.get("marginUsed")),
            "open_time": None,  # 由 fills 回填
        })
    return out


def fetch_fills_stats(address: str) -> dict:
    """胜率（closedPnl 口径）+ 各币最早开仓时间"""
    try:
        fills = _post({"type": "userFills", "user": address})
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] userFills 失败 {address[:10]}: {e}", file=sys.stderr)
        return {"win_rate": None, "closed_count": 0, "open_times": {}}
    if not isinstance(fills, list):
        return {"win_rate": None, "closed_count": 0, "open_times": {}}

    wins = losses = 0
    open_times: dict[str, int] = {}
    for f in fills:
        cp = _f(f.get("closedPnl"))
        if cp is not None and cp != 0:
            if cp > 0:
                wins += 1
            else:
                losses += 1
        d = str(f.get("dir") or "")
        if d.startswith("Open"):
            coin = f.get("coin")
            t = f.get("time")
            if coin and t:
                open_times[coin] = min(open_times.get(coin, t), t)
    total = wins + losses
    return {
        "win_rate": (wins / total * 100) if total else None,
        "closed_count": total,
        "open_times": open_times,
    }


def fetch_max_drawdown(address: str) -> float | None:
    """账户价值历史 → 最大回撤 %（allTime 窗口）"""
    try:
        pf = _post({"type": "portfolio", "user": address})
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] portfolio 失败 {address[:10]}: {e}", file=sys.stderr)
        return None

    # 兼容 dict / list 两种返回形态
    hist = None
    if isinstance(pf, dict):
        for k, v in pf.items():
            if isinstance(v, dict) and isinstance(v.get("accountValueHistory"), list):
                hist = v["accountValueHistory"]
                break
    elif isinstance(pf, list):
        for item in pf:
            if isinstance(item, (list, tuple)) and len(item) == 2 and isinstance(item[1], dict):
                if isinstance(item[1].get("accountValueHistory"), list):
                    hist = item[1]["accountValueHistory"]
                    break
    if not hist:
        return None

    peak, max_dd = None, 0.0
    for pt in hist:
        val = None
        if isinstance(pt, (list, tuple)) and len(pt) >= 2:
            val = _f(pt[1])
        elif isinstance(pt, dict):
            val = _f(pt.get("value") or pt.get("v"))
        if val is None or val <= 0:
            continue
        peak = val if peak is None else max(peak, val)
        if peak:
            dd = (peak - val) / peak * 100
            max_dd = max(max_dd, dd)
    return round(max_dd, 2) if peak else None


def collect(top_n: int = DEFAULT_TOP, min_account: float = DEFAULT_MIN_ACCOUNT) -> dict:
    print("拉取 Hyperliquid 榜单…", file=sys.stderr)
    cands = fetch_leaderboard(top_n, min_account)
    mids = fetch_mids()

    traders = []
    for c in cands:
        if len(traders) >= top_n:
            break
        try:
            positions = fetch_positions(c["address"], mids)
        except Exception as e:  # noqa: BLE001
            print(f"  [warn] {c['name']} 仓位失败: {e}", file=sys.stderr)
            continue
        if not positions:  # 无持仓的交易员不进监控表
            continue
        stats = fetch_fills_stats(c["address"])
        for p in positions:
            t = (stats.get("open_times") or {}).get(p["coin"])
            if t:
                p["open_time"] = datetime.fromtimestamp(t / 1000, tz=timezone.utc).astimezone().isoformat(timespec="seconds")
        levs = [p["leverage"] for p in positions if p.get("leverage")]
        traders.append({
            **{k: v for k, v in c.items() if k != "address"},
            "address": c["address"],
            "win_rate": stats.get("win_rate"),
            "closed_count": stats.get("closed_count"),
            "max_drawdown_pct": fetch_max_drawdown(c["address"]),
            "leverage_pref": (f"{min(levs)}-{max(levs)}x" if levs else "—"),
            "position_count": len(positions),
            "positions": positions,
        })
        print(f"  ✓ {c['name']} 持仓 {len(positions)} 个", file=sys.stderr)

    all_pos = [p for t in traders for p in t["positions"]]
    return {
        "updated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "source": "hyperliquid public API (no key)",
        "scope": f"Hyperliquid 永续 · 30d ROI 榜 Top {top_n}（账户规模 ≥ ${min_account:,.0f} 且当前有持仓）",
        "notes": [
            "榜单不提供粉丝数 → 以「账户规模 / 30d 成交额」替代并标注口径",
            "开仓时间取自最近成交明细（userFills 上限 2000 笔），找不到则显示 —",
            "胜率 = 盈亏非零的平仓成交中盈利笔数占比（closedPnl 口径）",
        ],
        "liq_warn_pct": LIQ_WARN_PCT,
        "traders": traders,
        "stats": {
            "trader_count": len(traders),
            "position_count": len(all_pos),
            "liq_warn_count": sum(1 for p in all_pos if p.get("liq_warn")),
            "exchange_count": 1,
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=DEFAULT_TOP)
    ap.add_argument("--min-account", type=float, default=DEFAULT_MIN_ACCOUNT)
    args = ap.parse_args()

    data = collect(args.top, args.min_account)
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    s = data["stats"]
    print(f"交易员 {s['trader_count']} 位 · 仓位 {s['position_count']} 个 · 强平预警 {s['liq_warn_count']} 个")
    print(f"→ {OUTPUT_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
