"""
CryptoDog · 合约市场采集（币安 U 本位永续公开 API，免 key）
==========================================================
数据源（全部公开、无需 API key）：
  - GET /fapi/v1/exchangeInfo              合约交易对（筛选 USDT 永续）
  - GET /fapi/v1/ticker/24hr               24h 行情（按成交额取 Top N）
  - GET /futures/data/openInterestHist     OI 历史（period=1d&limit=2 → 现值与 24h 前值，含美元口径）
  - GET /fapi/v1/premiumIndex              资金费率 / 标记价（不带 symbol = 全量，1 次请求）

输出：cryptodog/data/futures_market.json

⚠ 24h 爆仓：币安已下线公开 `allForceOrders` 端点（实测 404，现需 API key），
   因此本采集器**不伪造**爆仓数据，只输出 available=false + 原因，前端据实展示「未接通」。

⚠ 网络：币安主站国内被墙，必须走 Clash 代理（127.0.0.1:7897）。
⚠ NO_PROXY 继承坑：父进程可能设过 NO_PROXY=*（akshare 用），必须 pop 掉，否则代理失效被限流。

用法：
  python cryptodog/collectors/futures_market.py            # 采集（Top 25）
  python cryptodog/collectors/futures_market.py --top 40   # 自定义 Top N
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# NO_PROXY 继承坑：必须在 import requests 使用前清掉
os.environ.pop("NO_PROXY", None)
os.environ.pop("no_proxy", None)

import requests  # noqa: E402

PROXY = {"http": "http://127.0.0.1:7897", "https": "http://127.0.0.1:7897"}
BASE_URL = "https://fapi.binance.com"
HTTP_TIMEOUT = 30
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0"}

CRYPTODOG_ROOT = Path("D:/project/cryptodog")
OUTPUT_FILE = CRYPTODOG_ROOT / "data" / "futures_market.json"

DEFAULT_TOP = 25
# 资金费率异常阈值：|rate| > 0.1% / 8h
FUNDING_ABNORMAL = 0.001


def _get(path: str, params: dict | None = None) -> dict | list:
    """带超时/重试的 GET（3 次退避）"""
    last_err = None
    for attempt in range(3):
        try:
            r = requests.get(BASE_URL + path, params=params, headers=HEADERS,
                             proxies=PROXY, timeout=HTTP_TIMEOUT)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"GET {path} 失败: {last_err}")


def _f(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def get_perp_symbols() -> set[str]:
    """全部正在交易的 USDT 永续合约交易对"""
    info = _get("/fapi/v1/exchangeInfo")
    out = set()
    for s in info.get("symbols", []):
        if (s.get("quoteAsset") == "USDT" and s.get("contractType") == "PERPETUAL"
                and s.get("status") == "TRADING"):
            out.add(s["symbol"])
    return out


def pick_top_symbols(perp: set[str], top_n: int) -> list[str]:
    """按 24h 成交额取 Top N（只取 USDT 永续）"""
    tickers = _get("/fapi/v1/ticker/24hr")
    rows = []
    for t in tickers:
        sym = t.get("symbol")
        if sym not in perp:
            continue
        qv = _f(t.get("quoteVolume"))
        if qv:
            rows.append((sym, qv))
    rows.sort(key=lambda x: x[1], reverse=True)
    return [s for s, _ in rows[:top_n]]


def fetch_oi(symbol: str) -> dict:
    """OI 现值 + 24h 前值（美元口径），period=1d&limit=2"""
    hist = _get("/futures/data/openInterestHist",
                {"symbol": symbol, "period": "1d", "limit": 2})
    if not isinstance(hist, list) or not hist:
        return {}
    latest = hist[-1]
    prev = hist[0] if len(hist) > 1 else {}
    cur = _f(latest.get("sumOpenInterestValue"))
    old = _f(prev.get("sumOpenInterestValue"))
    chg = None
    if cur is not None and old:
        chg = (cur - old) / old * 100
    return {
        "oi_usd": cur,
        "oi_usd_prev": old,
        "oi_change_24h_pct": chg,
        "oi_coins": _f(latest.get("sumOpenInterest")),
    }


def fetch_funding() -> dict:
    """全量资金费率 + 标记价（1 次请求）"""
    rows = _get("/fapi/v1/premiumIndex")
    if isinstance(rows, dict):
        rows = [rows]
    out = {}
    for r in rows:
        sym = r.get("symbol")
        if not sym:
            continue
        out[sym] = {
            "funding_rate": _f(r.get("lastFundingRate")),
            "mark_price": _f(r.get("markPrice")),
            "next_funding_time": r.get("nextFundingTime"),
        }
    return out


def collect(top_n: int = DEFAULT_TOP) -> dict:
    perp = get_perp_symbols()
    symbols = pick_top_symbols(perp, top_n)
    funding = fetch_funding()

    rows, oi_now, oi_prev = [], 0.0, 0.0
    for sym in symbols:
        try:
            oi = fetch_oi(sym)
        except Exception as e:  # noqa: BLE001
            print(f"  [warn] {sym} OI 失败: {e}", file=sys.stderr)
            continue
        f = funding.get(sym, {})
        if oi.get("oi_usd"):
            oi_now += oi["oi_usd"]
        if oi.get("oi_usd_prev"):
            oi_prev += oi["oi_usd_prev"]
        rows.append({
            "symbol": sym,
            "oi_usd": oi.get("oi_usd"),
            "oi_change_24h_pct": oi.get("oi_change_24h_pct"),
            "funding_rate": f.get("funding_rate"),
            "mark_price": f.get("mark_price"),
        })

    total_chg = ((oi_now - oi_prev) / oi_prev * 100) if oi_prev else None

    abnormal = []
    for sym, f in funding.items():
        if sym not in perp:
            continue
        rate = f.get("funding_rate")
        if rate is not None and abs(rate) > FUNDING_ABNORMAL:
            abnormal.append({"symbol": sym, "funding_rate": rate})
    abnormal.sort(key=lambda x: abs(x["funding_rate"]), reverse=True)

    return {
        "updated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "source": "binance-fapi (public, no key)",
        "proxy": "127.0.0.1:7897",
        "oi": {
            "total_usd": oi_now or None,
            "total_usd_prev": oi_prev or None,
            "change_24h_pct": total_chg,
            "symbols_counted": len(rows),
            "scope": f"币安 U 本位成交额 Top {top_n} 合约合计",
        },
        "funding": {
            "threshold": FUNDING_ABNORMAL,
            "abnormal_count": len(abnormal),
            "abnormal": abnormal[:20],
        },
        "liquidations": {
            "available": False,
            "reason": "币安已下线公开 allForceOrders 端点（实测 404，现需 API key）；未接入第三方数据源，不伪造数值",
        },
        "top_symbols": sorted(rows, key=lambda x: x["oi_usd"] or 0, reverse=True),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=DEFAULT_TOP, help="按成交额取前 N 个合约")
    args = ap.parse_args()

    data = collect(args.top)
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    oi = data["oi"]
    print(f"OI 合计 ${oi['total_usd']:,.0f}" if oi["total_usd"] else "OI 合计 -",
          f"· 24h {oi['change_24h_pct']:+.2f}%" if oi["change_24h_pct"] is not None else "· 24h -",
          f"· {oi['symbols_counted']} 个合约")
    print(f"资金费率异常 {data['funding']['abnormal_count']} 个（阈值 {data['funding']['threshold']}）")
    print(f"→ {OUTPUT_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
