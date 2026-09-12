# -*- coding: utf-8 -*-
"""CryptoDog 验收：信号驱动三块（规格 §3.1b ②③④）+ Playwright 实测

对照规格 §7 验收清单中与本次改动相关的条目：
  · 资产与交易 = 信号驱动结构：AI 重点关注分析置顶 + 三类信号分组表 + 持仓明细默认收起
  · 无指标卡/概览卡（用户 16:56 明确废弃）
  · KOL 信号分析可用：三组统计正确；分级徽章 + 可解释理由（引用数字）+ 生成时间；↻ 重新分析可触发
  · 地址待补 KOL 显示空态不报错
  · 双主题可读
"""
import json
import sys
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener

from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
SHOT = Path("D:/project/cryptodog/data/verify")
op = build_opener(ProxyHandler({}))
errors = []


def api(path):
    with op.open(Request("http://127.0.0.1:8100" + path), timeout=90) as r:
        return json.loads(r.read().decode("utf-8"))


print("=== 1) 后端接口 ===")
kols = api("/api/meme/kols")["kols"]
print(f"  KOL 注册表: {[k['name'] for k in kols]}")
sig = api("/api/meme/signals?kol=%E7%8E%8B%E5%B0%8F%E4%BA%8C&refresh=1")
c = (sig.get("signals") or {}).get("counts") or {}
sc = (sig.get("signals") or {}).get("data_scope") or {}
print(f"  王小二 信号: {c} | 窗口 {sc.get('window_days')} 天 | watchlist {len(sig.get('watchlist') or [])}")
print(f"  source={sig.get('source')} | 早期埋伏可判定={sc.get('can_judge_early')}")
assert c.get("repeat", 0) >= 1, "多次买入信号应有结果"
assert sig.get("watchlist"), "应有 AI 重点关注列表"

print("\n=== 2) Playwright 实测 ===")
SHOT.mkdir(parents=True, exist_ok=True)
with sync_playwright() as p:
    b = p.chromium.launch()
    page = b.new_page(viewport={"width": 1680, "height": 1150})
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto("http://localhost:8100/", wait_until="load", timeout=40000)
    page.wait_for_timeout(3000)
    print("  标题:", page.title(), "| 落地视图:", page.evaluate("() => state.view"))

    # 切到 MEME 线 → 资产与交易（规格：点 MEME = 资产与交易）
    page.evaluate("setView('meme/assets')")
    page.wait_for_timeout(2500)
    # 选 KOL 王小二
    page.evaluate("() => { state.kolFilter = '王小二'; }")
    page.evaluate("render()")
    page.wait_for_timeout(4000)

    dom = page.evaluate("""() => {
      const t = document.body.innerText;
      return {
        hasAI: t.includes('AI 重点关注分析'),
        hasRepeat: t.includes('近期多次买入'),
        hasLarge: t.includes('大额买入'),
        hasEarly: t.includes('早期埋伏'),
        hasKpi3: !!document.querySelector('.kpi3'),
        hasKpi6: !!document.querySelector('.kpi6'),
        holdingsCollapsed: (() => {
          const cards = Array.from(document.querySelectorAll('.card'));
          const hc = cards.find(c => c.innerText.includes('完整持仓明细'));
          return hc ? !hc.innerHTML.includes('首次买入') : null;
        })(),
        reasonHasNumbers: /\\$[\\d,]+|\\d+ 笔/.test(t),
        genTime: (t.match(/\\d{2}-\\d{2} \\d{2}:\\d{2} 生成/) || [''])[0],
        scopeNote: (t.match(/数据窗口[^\\n]{0,40}/) || [''])[0],
        earlyNote: (t.match(/采集窗口仅[^\\n]{0,50}/) || [''])[0],
        tiers: (t.match(/重点关注 · [强中]/g) || []).length,
      };
    }""")
    for k, v in dom.items():
        print(f"  {k:18s} {v}")
    assert dom["hasAI"] and dom["hasRepeat"] and dom["hasLarge"] and dom["hasEarly"], "信号三块未渲染"
    assert not dom["hasKpi3"] and not dom["hasKpi6"], "废弃的指标卡/概览卡仍在"
    assert dom["holdingsCollapsed"], "完整持仓明细未默认收起"
    assert dom["tiers"] > 0, "没有分级徽章"
    page.screenshot(path=str(SHOT / "cryptodog_assets_signals_dark.png"))

    # ↻ 重新分析
    page.evaluate("refreshSignals()")
    page.wait_for_timeout(6000)
    print("  重新分析后 watchlist:", page.evaluate("() => (state.signals && (state.signals.watchlist||[]).length) || 0"))

    # 持仓明细展开
    page.evaluate("() => { state.holdingsOpen = true; render(); }")
    page.wait_for_timeout(1500)
    print("  展开持仓后含'首次买入':", page.evaluate("() => document.body.innerHTML.includes('首次买入')"))
    page.screenshot(path=str(SHOT / "cryptodog_assets_holdings_open.png"))

    # 地址待补 KOL 空态（若存在）
    page.evaluate("() => { state.kolFilter = '镭射猫'; }")
    page.evaluate("render()")
    page.wait_for_timeout(3500)
    print("  镭射猫 资产与交易 OK（无异常）:", page.evaluate("() => document.body.innerText.includes('资产')"))

    # 五个视图逐个点一遍
    views = ["futures/monitor", "futures/copy", "meme/assets", "meme/kol", "meme/copy"]
    for v in views:
        page.evaluate(f"setView({json.dumps(v)})")
        page.wait_for_timeout(2200)
        txt = page.evaluate("() => document.body.innerText.length")
        print(f"  视图 {v:18s} 渲染字符数={txt}")

    # 双主题
    page.evaluate("setView('meme/assets')")
    page.wait_for_timeout(2000)
    page.evaluate("() => { state.kolFilter='王小二'; render(); }")
    page.wait_for_timeout(3000)
    page.evaluate("() => { document.documentElement.setAttribute('data-theme','light'); }")
    page.wait_for_timeout(1200)
    page.screenshot(path=str(SHOT / "cryptodog_assets_signals_light.png"))
    page.evaluate("() => { document.documentElement.setAttribute('data-theme','dark'); }")
    page.wait_for_timeout(600)
    b.close()

print("\npageerror:", errors[:5] if errors else "0")
print("截图目录:", SHOT)
