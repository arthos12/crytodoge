# -*- coding: utf-8 -*-
"""核对合约线两视图渲染真实数据（刚重新采集过行情）"""
import json
import sys

from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
errors = []

with sync_playwright() as p:
    b = p.chromium.launch()
    page = b.new_page(viewport={"width": 1680, "height": 1150})
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto("http://localhost:8100/", wait_until="load", timeout=40000)
    page.wait_for_timeout(4000)

    print("== 合约监控 ==")
    t = page.inner_text("body")
    for k in ["全网", "OI", "资金费率", "爆仓", "监控"]:
        print(f"  含 '{k}': {k in t}")
    for line in t.split("\n")[:18]:
        if line.strip():
            print("   |", line.strip()[:100])

    print("\n== 合约跟单（交易员榜单，真实数据）==")
    page.evaluate("setView('futures/copy')")
    page.wait_for_timeout(4000)
    t2 = page.inner_text("body")
    import re
    rois = re.findall(r"[+-]?\d+\.\d+%", t2)
    print("  含 'ROI':", "ROI" in t2, "| 百分比数值数:", len(rois), "| 样例:", rois[:6])
    print("  含 '胜率':", "胜率" in t2, "| 含 '跟随':", "跟随" in t2)
    cards = page.evaluate("() => document.querySelectorAll('.kol-card').length")
    print("  交易员卡片数:", cards)
    print("  页面字符数:", len(t2))
    b.close()

print("\npageerror:", errors[:3] if errors else "0")
