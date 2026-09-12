# -*- coding: utf-8 -*-
"""CryptoDog · KOL 信号统计 + 可解释研判

规格来源：memory/design_copytrade_site.md §3.1b「信号统计规则」。
三类信号（全部由真实交易流计算，不编造）：
  1. 近期多次买入：近 7 天同币 buy 笔数 ≥ 2
  2. 大额买入：单笔 usd ≥ max(large_usd_floor, 该币均单 × large_multiple)
  3. 早期埋伏：首笔 buy 距今 > early_days 天，且卖出数量占比 < early_max_sold_pct

研判（review）：规则可解释版——每条理由必须引用具体数字。若配置了 LLM key
（环境变量 CRYPTODOG_LLM_KEY / DEEPSEEK_API_KEY + CRYPTODOG_LLM_URL），
则在规则结论之上追加 LLM 综合研判；未配置时明确标注 source=rules，绝不伪装成 AI 输出。
"""
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path("D:/project/cryptodog")
DATA_DIR = ROOT / "data"
SIGNAL_DIR = DATA_DIR / "kol_signals"
CONFIG_FILE = DATA_DIR / "signal_config.json"

DEFAULT_CONFIG = {
    "repeat_days": 7,          # 近期多次买入：回看窗口
    "repeat_min_count": 2,     # 近期多次买入：最少笔数
    "large_usd_floor": 5000,   # 大额买入：单笔下限（USD）
    "large_multiple": 3.0,     # 大额买入：相对该币均单倍数
    "early_days": 14,          # 早期埋伏：首买距今最少天数
    "early_max_sold_pct": 50,  # 早期埋伏：已卖出数量占比上限（%）
    "watch_limit": 8,          # 研判卡最多展示条数
}

SCHEMA = 2      # 结果结构版本：升级后旧缓存自动失效（避免前端读到缺字段的旧结果）


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        if CONFIG_FILE.exists():
            cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")) or {})
    except Exception:
        pass
    return cfg


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _dt(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s))
    except ValueError:
        return None


_STABLE_SYMBOLS = {"USDC", "USDT", "USDS", "USDH", "DAI", "PYUSD", "EURC", "USDC.E", "USDT.S",
                   "FDUSD", "USD1", "BUSD"}


def _agg_by_token(trades: list) -> dict:
    """按 contract（或 symbol）聚合买卖，供三类信号使用

    金额口径说明：仪表盘交易流里「买入腿」（代币 IN）常常没有 usd，成本记在
    同一时刻的稳定币流出腿上（tracker 会合成一条 USDC OUT 记录）。因此这里做
    **时间邻近配对**（±180s 内最近的未占用花费腿），配到的金额标记 usd_estimated。
    稳定币本身不作为「持仓信号」统计（与仪表盘 include_stables=false 口径一致）。
    """
    # 花费腿（稳定币/SOL 流出，带 usd）→ 供买入配对
    spends = []
    for t in trades or []:
        side = (t.get("side") or "").lower()
        usd = _f(t.get("usd"))
        if side in ("out", "sell") and usd and usd > 0:
            spends.append({"t": _dt(t.get("time")), "usd": usd, "used": False})

    agg = {}
    for t in trades or []:
        side = (t.get("side") or "").lower()
        if side not in ("buy", "sell"):
            continue
        sym = (t.get("symbol") or t.get("token") or "").strip()
        key = (t.get("contract") or sym or "").strip()
        if not key or key.lower() in ("other",):
            continue
        if sym.upper() in _STABLE_SYMBOLS:
            continue          # 稳定币不是「埋伏/加仓」信号标的（现金口径见 KOL 头部）
        a = agg.setdefault(key, {
            "key": key, "symbol": sym or "-", "name": t.get("token") or "",
            "contract": t.get("contract") or "", "chain": t.get("chain") or "",
            "platform": t.get("platform") or "",
            "buys": [], "sells": [],
        })
        if not a.get("platform") and t.get("platform"):
            a["platform"] = t["platform"]
        rec = {
            "time": t.get("time") or "",
            "t": _dt(t.get("time")),
            "usd": _f(t.get("usd")),
            "amount": _f(t.get("amount")),
            "mcap": _f(t.get("mcap")),
            "price_usd": _f(t.get("price_usd")),
            "hash": t.get("hash") or "",
            "explorer_url": t.get("explorer_url") or "",
            "usd_estimated": False,
        }
        if side == "buy" and not rec["usd"] and rec["t"]:
            # 时间邻近配对：找 ±180s 内最近的未占用花费腿
            best, best_gap = None, None
            for s in spends:
                if s["used"] or not s["t"]:
                    continue
                gap = abs((s["t"] - rec["t"]).total_seconds())
                if gap <= 180 and (best_gap is None or gap < best_gap):
                    best, best_gap = s, gap
            if best:
                best["used"] = True
                rec["usd"] = best["usd"]
                rec["usd_estimated"] = True
        (a["buys"] if side == "buy" else a["sells"]).append(rec)
    return agg


def _pos_index(positions: list) -> dict:
    """持仓索引：contract → 持仓行（含 value/pnl/roi/balance）"""
    idx = {}
    for p in positions or []:
        for k in ((p.get("contract") or "").strip(), (p.get("symbol") or "").strip().upper()):
            if k:
                idx.setdefault(k, p)
    return idx


def _pos_of(agg_item: dict, pos_idx: dict):
    return (pos_idx.get(agg_item.get("contract") or "")
            or pos_idx.get((agg_item.get("symbol") or "").upper()))


def compute_signals(trades: list, positions: list, cfg: dict | None = None) -> dict:
    """返回三类信号分组 + 汇总（全部字段可溯源到原始交易）"""
    cfg = cfg or load_config()
    now = datetime.now()
    agg = _agg_by_token(trades or [])
    pos_idx = _pos_index(positions or [])

    repeat, large, early = [], [], []
    for key, a in agg.items():
        if not a["buys"]:
            continue
        pos = _pos_of(a, pos_idx)
        total_buy_usd = sum((b["usd"] or 0) for b in a["buys"])
        bought_amt = sum((b["amount"] or 0) for b in a["buys"])
        sold_amt = sum((s["amount"] or 0) for s in a["sells"])
        sold_usd = sum((s["usd"] or 0) for s in a["sells"])
        first_buy = min((b["t"] for b in a["buys"] if b["t"]), default=None)
        last_buy = max((b["t"] for b in a["buys"] if b["t"]), default=None)
        avg_buy = (total_buy_usd / len(a["buys"])) if a["buys"] else 0
        sold_pct = (sold_amt / bought_amt * 100) if bought_amt > 0 else 0.0

        base = {
            "symbol": a["symbol"], "name": a["name"], "contract": a["contract"],
            "chain": a["chain"], "platform": a["platform"],
            "logo": (pos or {}).get("logo") or "",
            "value_usd": (pos or {}).get("value_usd"),
            "pnl_usd": (pos or {}).get("pnl_usd"),
            "roi_pct": (pos or {}).get("roi_pct"),
            "balance": (pos or {}).get("balance"),
            "total_buy_usd": round(total_buy_usd, 2),
            "buy_count": len(a["buys"]), "sell_count": len(a["sells"]),
            "sold_usd": round(sold_usd, 2),
            "sold_pct": round(sold_pct, 1),
            "avg_buy_usd": round(avg_buy, 2),
            "first_buy_time": first_buy.isoformat() if first_buy else None,
            "last_buy_time": last_buy.isoformat() if last_buy else None,
            "hold_days": ((now - first_buy).days if first_buy else None),
            "last_buy_age_days": ((now - last_buy).days if last_buy else None),
        }

        # ---- ① 近期多次买入 ----
        win_start = now - timedelta(days=cfg["repeat_days"])
        recent = [b for b in a["buys"] if b["t"] and b["t"] >= win_start]
        if len(recent) >= cfg["repeat_min_count"]:
            repeat.append({
                **base,
                "recent_buy_count": len(recent),
                "recent_buy_usd": round(sum((b["usd"] or 0) for b in recent), 2),
                "recent_usd_estimated": any(b.get("usd_estimated") for b in recent),
                "recent_last_time": max((b["time"] for b in recent), default=""),
                "window_days": cfg["repeat_days"],
            })

        # ---- ② 大额买入 ----
        floor = max(cfg["large_usd_floor"], avg_buy * cfg["large_multiple"])
        for b in a["buys"]:
            if b["usd"] and b["usd"] >= floor:
                large.append({
                    **base,
                    "single_usd": round(b["usd"], 2),
                    "usd_estimated": bool(b.get("usd_estimated")),
                    "multiple_vs_avg": round(b["usd"] / avg_buy, 1) if avg_buy else None,
                    "time": b["time"],
                    "mcap_at_time": b["mcap"],
                    "threshold_usd": round(floor, 2),
                    "hash": b["hash"],
                })

        # ---- ③ 早期埋伏 ----
        if (first_buy and (now - first_buy).days > cfg["early_days"]
                and sold_pct < cfg["early_max_sold_pct"]):
            early.append({
                **base,
                "hold_days": (now - first_buy).days,
                "take_profit": sold_pct >= 5,   # 已开始部分止盈
            })

    repeat.sort(key=lambda x: -(x.get("recent_buy_usd") or 0))
    large.sort(key=lambda x: -(x.get("single_usd") or 0))
    early.sort(key=lambda x: -(x.get("hold_days") or 0))

    # 数据范围（诚实标注：信号只覆盖采集到的交易窗口，不是全历史）
    times = sorted([t.get("time") for t in (trades or []) if t.get("time")])
    first_t = _dt(times[0]) if times else None
    last_t = _dt(times[-1]) if times else None
    window_days = round((last_t - first_t).total_seconds() / 86400, 1) if (first_t and last_t) else None
    scope = {
        "trades": len(trades or []),
        "tokens": len(agg),
        "first_trade": times[0] if times else None,
        "last_trade": times[-1] if times else None,
        "window_days": window_days,
        "can_judge_early": bool(window_days is not None and window_days >= cfg["early_days"]),
        "note": ("信号基于采集到的最近交易窗口计算（仪表盘 tracker 每轮只抓最近 N 笔，非全历史）；"
                 "买入金额若交易流未带 USD，则用同刻稳定币流出腿配对估算（标 ≈）"),
    }

    return {
        "generated_at": now.isoformat(),
        "config": cfg,
        "repeat_buys": repeat,
        "large_buys": large,
        "early_positions": early,
        "counts": {"repeat": len(repeat), "large": len(large), "early": len(early),
                   "tokens": len(agg), "trades": len(trades or [])},
        "data_scope": scope,
    }


def _level_and_reason(a: dict, signals: dict, cfg: dict) -> tuple:
    """规则研判：分级 + 必须引用具体数字的理由 + 跟单建议（可解释，非黑箱）"""
    sym = a.get("symbol") or "-"
    roi = a.get("roi_pct")
    days = a.get("hold_days")
    buys = a.get("buy_count") or 0
    total = a.get("total_buy_usd") or 0
    rep = next((r for r in signals["repeat_buys"] if r["symbol"] == sym), None)
    lg = next((x for x in signals["large_buys"] if x["symbol"] == sym), None)
    er = next((x for x in signals["early_positions"] if x["symbol"] == sym), None)

    bits = []
    score = 0
    if rep:
        pct = (rep["recent_buy_usd"] / total * 100) if total else 0
        approx = "≈" if rep.get("recent_usd_estimated") else ""
        amt = f"{approx}${rep['recent_buy_usd']:,.0f}" if rep["recent_buy_usd"] else "金额未采集"
        bits.append(f"近 {cfg['repeat_days']} 天连续加仓 {rep['recent_buy_count']} 笔共 {amt}"
                    + (f"（占该币建仓总额 {pct:.0f}%）" if rep["recent_buy_usd"] and total else ""))
        score += 3 if rep["recent_buy_count"] >= 3 else 2
    if lg:
        approx = "≈" if lg.get("usd_estimated") else ""
        bits.append(f"单笔最大买入 {approx}${lg['single_usd']:,.0f}"
                    + (f"（均单 {lg['multiple_vs_avg']}×）" if lg.get("multiple_vs_avg") else ""))
        score += 2 if (lg.get("multiple_vs_avg") or 0) >= 3 else 1
    if er:
        cum = f"累计买入 ${total:,.0f}" if total else "累计买入金额未采集"
        bits.append(f"早期埋伏：持有 {er['hold_days']} 天，{cum}，已卖出 {er['sold_pct']:.0f}%")
        if er.get("take_profit"):
            bits.append("已开始部分止盈（KOL 在止盈而非离场）")
            score += 1
        else:
            score += 2
    if roi is not None:
        bits.append(f"当前浮盈 {roi:+.1f}%")
        if roi > 0:
            score += 1
        elif roi < -20:
            score -= 1
    bits.append(f"累计 {buys} 笔买入 / {a.get('sell_count') or 0} 笔卖出")

    if score >= 5:
        level, label, advice = "strong", "重点关注 · 强", "可跟（建议小仓位试探，注意该币建仓时间较短）"
    elif score >= 3:
        level, label, advice = "medium", "重点关注 · 中", "可留观；等回调或加仓信号确认再介入"
    else:
        level, label, advice = "watch", "观望", "信号中性，暂无跟单价值"

    return level, label, "；".join(bits) + "。", advice


def build_review(name: str, trades: list, positions: list, cfg: dict | None = None) -> dict:
    """生成该 KOL 的「重点关注分析」：规则研判逐币打分（可选 LLM 增强）"""
    cfg = cfg or load_config()
    signals = compute_signals(trades, positions, cfg)
    pos_idx = _pos_index(positions or [])
    agg = _agg_by_token(trades or [])

    rows = []
    for key, a in agg.items():
        if not a["buys"]:
            continue
        pos = _pos_of(a, pos_idx) or {}
        merged = {
            "symbol": a["symbol"], "name": a["name"], "contract": a["contract"],
            "platform": a["platform"], "chain": a["chain"],
            "logo": pos.get("logo") or "",
            "value_usd": pos.get("value_usd"), "pnl_usd": pos.get("pnl_usd"),
            "roi_pct": pos.get("roi_pct"), "balance": pos.get("balance"),
            "total_buy_usd": round(sum((b["usd"] or 0) for b in a["buys"]), 2),
            "buy_count": len(a["buys"]), "sell_count": len(a["sells"]),
            "first_buy_time": min((b["time"] for b in a["buys"] if b["time"]), default=None),
        }
        fb = _dt(merged["first_buy_time"])
        merged["hold_days"] = (datetime.now() - fb).days if fb else None
        level, label, reason, advice = _level_and_reason(merged, signals, cfg)
        rows.append({**merged, "level": level, "level_label": label,
                     "reason": reason, "advice": advice})

    order = {"strong": 0, "medium": 1, "watch": 2}
    rows.sort(key=lambda r: (order.get(r["level"], 9), -(r.get("value_usd") or 0)))

    payload = {
        "schema": SCHEMA,
        "kol": name,
        "generated_at": datetime.now().isoformat(),
        "source": "rules",
        "source_note": "规则研判：由真实交易流统计生成，理由均引用具体数字（未配置 LLM key）",
        "signals": signals,
        "watchlist": rows[:cfg["watch_limit"]],
        "watchlist_total": len(rows),
    }

    llm = _llm_enhance(name, payload)
    if llm:
        payload["llm"] = llm
        payload["source"] = "rules+llm"
        payload["source_note"] = "规则统计 + LLM 综合研判（两者同屏展示，不用黑箱评分）"

    SIGNAL_DIR.mkdir(parents=True, exist_ok=True)
    out = SIGNAL_DIR / f"{name}.json"
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(out)
    return payload


def load_review(name: str) -> dict | None:
    """读缓存结果；结构版本不匹配（缺 data_scope 等新字段）则视为过期，返回 None 触发重算"""
    p = SIGNAL_DIR / f"{name}.json"
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    if data.get("schema") != SCHEMA:
        return None
    if not (data.get("signals") or {}).get("data_scope"):
        return None
    return data


def _llm_enhance(name: str, payload: dict) -> dict | None:
    """可选：配置了 key 才调用 LLM；未配置返回 None（前端如实显示 source=rules）"""
    key = os.environ.get("CRYPTODOG_LLM_KEY") or os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        return None
    url = os.environ.get("CRYPTODOG_LLM_URL", "https://api.deepseek.com/chat/completions")
    model = os.environ.get("CRYPTODOG_LLM_MODEL", "deepseek-chat")
    try:
        import requests
        digest = [{"symbol": w["symbol"], "level": w["level"], "reason": w["reason"],
                   "roi_pct": w.get("roi_pct"), "buy_count": w.get("buy_count")}
                  for w in payload["watchlist"]]
        prompt = ("你是加密持仓分析助手。基于以下 KOL 交易信号统计（真实链上数据），"
                  "给出每个代币一行简报，必须引用数字，不要编造数据：\n"
                  + json.dumps(digest, ensure_ascii=False))
        r = requests.post(url, timeout=30,
                          headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                          json={"model": model, "messages": [{"role": "user", "content": prompt}],
                                "max_tokens": 800})
        r.raise_for_status()
        return {"model": model, "text": r.json()["choices"][0]["message"]["content"]}
    except Exception as e:  # noqa: BLE001
        return {"error": f"LLM 调用失败（已保留规则研判）: {str(e)[:150]}"}
