# -*- coding: utf-8 -*-
"""自检：三个 KOL 的信号统计与研判（真实数据）"""
import json
import sys
from urllib.request import ProxyHandler, Request, build_opener

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, "D:/project/cryptodog")
import signals as sg  # noqa: E402

op = build_opener(ProxyHandler({}))


def g(u, t=120):
    with op.open(Request(u), timeout=t) as r:
        return json.loads(r.read().decode("utf-8"))


pos = g("http://127.0.0.1:8000/api/wallets/positions")
tr = g("http://127.0.0.1:8000/api/wallets/trades?limit=500")
print(f"仪表盘数据：positions={len(pos['positions'])} trades={len(tr['trades'])}\n")

for name in ["王小二", "Ethermonk", "镭射猫"]:
    pr = [p for p in pos["positions"] if p["wallet"] == name]
    trows = [t for t in tr["trades"] if t["wallet"] == name]
    rev = sg.build_review(name, trows, pr)
    s = rev["signals"]
    sc = s["data_scope"]
    print(f"== {name}: trades={len(trows)} counts={s['counts']}")
    print(f"   窗口: {sc['first_trade']} ~ {sc['last_trade']}")
    for r in s["repeat_buys"][:3]:
        print(f"   🔥 多次买入 {r['symbol']:12s} {r['recent_buy_count']}笔 "
              f"≈${r['recent_buy_usd']:,.0f} est={r['recent_usd_estimated']} "
              f"总额${r['total_buy_usd']:,.0f} roi={r['roi_pct']}")
    for b in s["large_buys"][:2]:
        print(f"   💰 大额买入 {b['symbol']:12s} ${b['single_usd']:,.0f} "
              f"x{b['multiple_vs_avg']} est={b['usd_estimated']}")
    for e in s["early_positions"][:2]:
        print(f"   ⏳ 早期埋伏 {e['symbol']:12s} {e['hold_days']}天 "
              f"卖出{e['sold_pct']}% 总额${e['total_buy_usd']:,.0f}")
    for w in rev["watchlist"][:2]:
        print(f"   🤖 [{w['level']}] {w['symbol']} :: {w['reason'][:90]}")
    print()
