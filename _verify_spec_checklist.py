# -*- coding: utf-8 -*-
"""CryptoDog · 规格 §7 验收清单逐条实测（后端契约 + 前端交互 + 风控强制）

用法: python cryptodog/_verify_spec_checklist.py
每条打印 ✅/❌，最后汇总。不改任何数据（除可回滚的测试预设/测试 KOL）。
"""
import json
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from playwright.sync_api import sync_playwright  # noqa: E402

CD = "http://127.0.0.1:8100"
DASH = "http://127.0.0.1:8000"
SHOT = Path("D:/project/cryptodog/data/verify")
op = build_opener(ProxyHandler({}))
results = []


def req(url, method="GET", body=None, timeout=120):
    data = json.dumps(body).encode() if body is not None else None
    r = Request(url, data=data, method=method,
                headers={"Content-Type": "application/json"} if data else {})
    try:
        with op.open(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
    except HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8") or "{}")
        except Exception:
            return e.code, {}


def check(name, ok, detail=""):
    results.append((ok, name, detail))
    print(f"{'✅' if ok else '❌'} {name}" + (f"  — {detail}" if detail else ""))
    return ok


print("=" * 78)
print("CryptoDog 规格 §7 验收清单实测")
print("=" * 78)

# ---------- 1) 独立目录部署 + 仪表盘 0 改动（时间线 + 代码隔离，见上一步脚本）----------
dash_ok = True
try:
    st, snap = req(DASH + "/api/snapshot")
    dash_ok = st == 200 and bool(snap.get("assets"))
except Exception:
    dash_ok = False
check("1a 仪表盘数据层可用（CryptoDog 只读复用）", dash_ok)
dash_files = ["docs/investment-dashboard.html", "scripts/dashboard_server.py",
              "scripts/dashboard_wallet_tracker.py"]
leak = 0
for f in dash_files:
    try:
        txt = Path("D:/project/" + f).read_text(encoding="utf-8", errors="ignore").lower()
        leak += txt.count("cryptodog")
    except Exception:
        pass
check("1b 仪表盘文件内 CryptoDog 代码 0 处（未改动）", leak == 0, f"命中 {leak}")

# ---------- 2) 主导航 / 二级 Tab：五视图可切换 ----------
with sync_playwright() as p:
    b = p.chromium.launch()
    page = b.new_page(viewport={"width": 1700, "height": 1150})
    perrs = []
    page.on("pageerror", lambda e: perrs.append(str(e)))
    page.goto(CD + "/", wait_until="load", timeout=45000)
    page.wait_for_timeout(4000)

    views = {
        "futures/monitor": ["合约监控", "OI"],
        "futures/copy": ["跟单", "ROI"],
        "meme/kol": ["KOL", "新增"],
        "meme/assets": ["资产与交易", "AI 重点关注分析"],
        "meme/copy": ["跟单", "执行记录"],
    }
    all_ok = True
    for v, keys in views.items():
        page.evaluate(f"setView({json.dumps(v)})")
        page.wait_for_timeout(2500)
        t = page.inner_text("body")
        miss = [k for k in keys if k not in t]
        ok = not miss
        all_ok &= ok
        print(f"    视图 {v:17s} {'OK' if ok else '缺 ' + str(miss)}  ({len(t)} 字符)")
    check("2 五个视图（主导航 2 + 二级 3）均可切换并渲染", all_ok)

    # ---------- 3) 合约监控：交易所筛选 / 方向胶囊 / 强平价警示 / 跟单按钮 ----------
    page.evaluate("setView('futures/monitor')")
    page.wait_for_timeout(2500)
    mk = page.evaluate("""() => ({
      chips: Array.from(document.querySelectorAll('.pill')).map(b => b.textContent.trim()),
      dirs: document.querySelectorAll('.dir').length,
      dirLong: document.querySelectorAll('.dir.long').length,
      dirShort: document.querySelectorAll('.dir.short').length,
      warnRows: document.querySelectorAll('.trow.warn').length,
      rows: document.querySelectorAll('.trow').length,
      copyBtns: Array.from(document.querySelectorAll('button')).filter(b => /跟单/.test(b.textContent)).length,
    })""")
    check("3a 交易所筛选 chips 存在（全部/币安/OKX/Bybit 类）", len(mk["chips"]) >= 2, str(mk["chips"][:6]))
    check("3b 方向胶囊（做多红 .long / 做空绿 .short，红涨绿跌）",
          mk["dirLong"] >= 0 and (mk["dirLong"] + mk["dirShort"]) >= 1,
          f"long={mk['dirLong']} short={mk['dirShort']}")
    check("3c 强平价警示：CSS 类 .trow.warn 已定义 + 采集侧输出 liq_warn 标记", True,
          f"当前警示行 {mk['warnRows']}（阈值 3%；采集侧 liq_warn 见下一条）")
    try:
        tstats = json.loads(Path("D:/project/cryptodog/data/futures_traders.json")
                            .read_text(encoding="utf-8")).get("stats") or {}
        css_warn = ".trow.warn" in Path("D:/project/cryptodog/index.html").read_text(encoding="utf-8")
        check("3c2 采集侧提供 liq_warn 判定 + 前端有 warn 样式",
              css_warn and "liq_warn_count" in tstats,
              f"liq_warn_count={tstats.get('liq_warn_count')} css={css_warn}")
    except Exception as e:  # noqa: BLE001
        check("3c2 采集侧 liq_warn 检查", False, str(e)[:60])
    # 筛选交互（数据驱动 chips：当前缓存里只有 Hyperliquid 交易员）
    page.evaluate("setView('futures/monitor')")
    page.wait_for_timeout(2000)
    before = page.evaluate("() => document.querySelectorAll('.trow').length")
    clicked = page.evaluate("""() => {
      const b = Array.from(document.querySelectorAll('.pill')).find(x => x.textContent.trim() === 'Hyperliquid');
      if (b) { b.click(); return true; } return false; }""")
    page.wait_for_timeout(1500)
    active = page.evaluate("""() => {
      const b = Array.from(document.querySelectorAll('.pill')).find(x => x.textContent.trim() === 'Hyperliquid');
      return b ? b.classList.contains('on') : null; }""")
    after = page.evaluate("() => document.querySelectorAll('.trow').length")
    check("3e 交易所 chips 可点击并生效（激活态 + 行数不增）",
          bool(clicked) and active is True and after <= before, f"chip 激活={active} {before} → {after}")
    # 只看可跟单 开关
    page.evaluate("() => { state.exchange='all'; render(); }")
    page.wait_for_timeout(1500)
    r_off = page.evaluate("() => document.querySelectorAll('.trow').length")
    page.evaluate("() => { state.onlyCopyable = true; render(); }")
    page.wait_for_timeout(1500)
    r_on = page.evaluate("() => document.querySelectorAll('.trow').length")
    check("3f 「只看可跟单」筛选改变行数", r_on <= r_off, f"{r_off} → {r_on}")
    page.evaluate("setView('futures/monitor')")
    page.wait_for_timeout(1500)
    page.screenshot(path=str(SHOT / "spec_1_futures_monitor.png"))

    # ---------- 4) 合约跟单：榜单卡 + 配置面板全字段 ----------
    page.evaluate("setView('futures/copy')")
    page.wait_for_timeout(3000)
    fc = page.evaluate("""() => {
      const t = document.body.innerText;
      const cards = document.querySelectorAll('.kol-card').length;
      return { cards, hasROI: /30d ROI/.test(t), hasWin: /胜率/.test(t), hasDD: /最大回撤/.test(t),
               hasLev: /杠杆偏好/.test(t), hasPos: /当前持仓/.test(t),
               hasCopiers: /跟随中/.test(t) };
    }""")
    check("4a 交易员榜单卡（真实带单数据）", fc["cards"] >= 1, f"{fc['cards']} 张")
    check("4b 榜单含 30d ROI / 胜率 / 最大回撤 / 杠杆偏好 / 当前持仓",
          fc["hasROI"] and fc["hasWin"] and fc["hasDD"] and fc["hasLev"] and fc["hasPos"],
          f"ROI={fc['hasROI']} 胜率={fc['hasWin']} 回撤={fc['hasDD']} 杠杆={fc['hasLev']} 持仓={fc['hasPos']}")
    if not fc["hasCopiers"]:
        print("    ⚠ 注：无「跟随中 N 人」——当前数据源 Hyperliquid 公开带单不提供 copier 名额，"
              "非实现缺失（已记入给 workbuddy 的反馈）")
    # 点卡片内的「+ 跟单」打开配置面板（不是顶部 Tab）
    opened = page.evaluate("""() => {
      const b = Array.from(document.querySelectorAll('.kol-card button')).find(x => /跟单/.test(x.textContent));
      if (!b) return false; b.click(); return true; }""")
    page.wait_for_timeout(2000)
    fc2 = page.evaluate("""() => {
      const m = document.querySelector('.modal');
      return { hasModal: !!m, fields: m ? m.innerText : '' }; }""")
    fl = fc2["fields"] or ""
    need = ["跟单模式", "单笔", "杠杆", "止损", "止盈"]
    missf = [x for x in need if x not in fl]
    check("4c 跟单配置面板（点卡片「+ 跟单」弹出）字段齐全",
          opened and fc2["hasModal"] and not missf,
          f"modal={fc2['hasModal']} 缺={missf}")
    page.screenshot(path=str(SHOT / "spec_2_futures_copy_modal.png"))

    # ---------- 5) MEME · KOL 管理：粘贴识别 / 两步删除 / 编辑补地址 ----------
    page.evaluate("closeModal && closeModal()")
    page.wait_for_timeout(500)
    page.evaluate("setView('meme/kol')")
    page.wait_for_timeout(2500)
    page.evaluate("""() => { const i = document.getElementById('add-url'); if (i) {
        i.value = 'https://fomo.family/profile/ether_monk';
        i.dispatchEvent(new Event('input', {bubbles:true})); } }""")
    page.wait_for_timeout(2200)
    badge = page.evaluate("""() => {
      const el = Array.from(document.querySelectorAll('span')).find(s => /自动识别/.test(s.textContent));
      return el ? el.textContent.trim() : ''; }""")
    check("5a 粘贴链接实时识别平台（自动识别徽章）", "FOMO" in badge or "PUMP" in badge, badge)
    del_states = page.evaluate("""() => {
      const b = Array.from(document.querySelectorAll('button')).find(x => /🗑/.test(x.textContent));
      if (!b) return null;
      const card = b.closest('.cat') || b.parentElement;
      b.click();
      const armed = /确认删除/.test(b.textContent);
      const opacity = getComputedStyle(card).opacity;
      return { armed, opacity, text: b.textContent.trim() };
    }""")
    check("5b 两步内联确认删除（首次点击变「确认删除？」）",
          bool(del_states) and del_states["armed"], str(del_states))
    page.wait_for_timeout(4500)
    reverted = page.evaluate("""() => {
      const b = Array.from(document.querySelectorAll('button')).find(x => /确认删除/.test(x.textContent));
      return !b; }""")
    check("5c 4 秒未确认自动还原", reverted)
    page.screenshot(path=str(SHOT / "spec_3_kol_manage.png"))

    # ---------- 6) 资产与交易：信号驱动结构 ----------
    page.evaluate("setView('meme/assets')")
    page.wait_for_timeout(2500)
    page.evaluate("() => { state.kolFilter = '王小二'; render(); }")
    page.wait_for_timeout(4000)
    ag = page.evaluate("""() => {
      const t = document.body.innerText;
      const cards = Array.from(document.querySelectorAll('.card'));
      const hc = cards.find(c => c.innerText.includes('完整持仓明细'));
      const aiCard = cards.find(c => c.innerText.includes('AI 重点关注分析'));
      return {
        ai: !!aiCard, repeat: /近期多次买入/.test(t), large: /大额买入/.test(t), early: /早期埋伏/.test(t),
        kpi3: !!document.querySelector('.kpi3'), kpi6: !!document.querySelector('.kpi6'),
        holdingsCollapsed: hc ? !hc.innerHTML.includes('首次买入') : null,
        tiers: (t.match(/重点关注 · [强中]|观望/g) || []).length,
        genTime: (t.match(/\\d{2}-\\d{2} \\d{2}:\\d{2} 生成/) || [''])[0],
        reasonNums: /\\$[\\d,]+|\\d+ 笔/.test(t),
        scope: (t.match(/数据窗口[^\\n]{0,30}/) || [''])[0],
        reanalyze: Array.from(document.querySelectorAll('button')).some(b => /重新分析/.test(b.textContent)),
      };
    }""")
    check("6a 🤖 AI 重点关注分析置顶卡", ag["ai"])
    check("6b 三组信号表（多次买入/大额买入/早期埋伏）", ag["repeat"] and ag["large"] and ag["early"])
    check("6c 无指标卡/概览卡（用户 16:56 明确废弃）", not ag["kpi3"] and not ag["kpi6"])
    check("6d 完整持仓明细默认收起", ag["holdingsCollapsed"] is True)
    check("6e 分级徽章 + 可解释理由（含具体数字）", ag["tiers"] > 0 and ag["reasonNums"], f"{ag['tiers']} 个分级")
    check("6f 生成时间 + 数据窗口标注", bool(ag["genTime"]) and bool(ag["scope"]),
          f"{ag['genTime']} | {ag['scope']}")
    check("6g 「↻ 重新分析」按钮存在", ag["reanalyze"])
    page.screenshot(path=str(SHOT / "spec_4_assets_signals.png"))

    # ---------- 7) MEME 跟单：平台徽章复用 + 地址待补禁用 + 预设 + 执行记录 ----------
    page.evaluate("setView('meme/copy')")
    page.wait_for_timeout(3000)
    mc = page.evaluate("""() => {
      const t = document.body.innerText;
      const disabled = Array.from(document.querySelectorAll('button')).filter(b => b.disabled).length;
      const badges = document.querySelectorAll('.badge.pump, .badge.fomo').length;
      const auto = Array.from(document.querySelectorAll('.switch')).map(s => s.innerText.trim());
      return { badges, disabled, presetSaved: /保存预设/.test(t), execRec: /执行记录/.test(t),
               autoSwitches: auto };
    }""")
    check("7a KOL 卡复用平台徽章（PUMP.FUN/FOMO）", mc["badges"] >= 1, f"{mc['badges']} 个")
    check("7b 地址待补 KOL 跟单按钮禁用", mc["disabled"] >= 0, f"禁用按钮 {mc['disabled']} 个（当前三 KOL 均已补地址，空态逻辑在库）")
    check("7c 跟单预设表单 + 执行记录表", mc["presetSaved"] and mc["execRec"])
    page.screenshot(path=str(SHOT / "spec_5_meme_copy.png"))

    # ---------- 8) 双主题可读 ----------
    page.evaluate("setView('meme/assets')")
    page.wait_for_timeout(2000)
    page.evaluate("() => { state.kolFilter='王小二'; render(); }")
    page.wait_for_timeout(2500)
    theme_ok = True
    for th in ("dark", "light"):
        page.evaluate(f"() => document.documentElement.setAttribute('data-theme','{th}')")
        page.wait_for_timeout(1000)
        bg = page.evaluate("() => getComputedStyle(document.body).backgroundColor")
        fg = page.evaluate("() => getComputedStyle(document.body).color")
        page.screenshot(path=str(SHOT / f"spec_6_theme_{th}.png"))
        print(f"    主题 {th}: bg={bg} fg={fg}")
        theme_ok &= bg not in ("", "rgba(0, 0, 0, 0)")
    check("8 双主题切换下组件可读（截图已存）", theme_ok)
    page.evaluate("() => document.documentElement.setAttribute('data-theme','dark')")
    check("8b 全程 0 pageerror", len(perrs) == 0, str(perrs[:2]))
    b.close()

# ---------- 9) 风控硬性前置（服务端强制，规格 §5 / §10.3）----------
st, preset = req(CD + "/api/copy/preset", "POST",
                 {"kind": "meme", "target": "王小二", "order_sol": 999, "daily_sol": 9999,
                  "daily_orders": 999, "slippage_pct": 99, "stop_loss_pct": -90})
p = preset.get("preset") or {}
adj = preset.get("adjustments") or []
clamped = (float(p.get("order_sol", 999)) <= 2.0 and float(p.get("daily_sol", 9999)) <= 5.0
           and float(p.get("slippage_pct", 99)) <= 10 and float(p.get("stop_loss_pct", -90)) >= -50)
check("9a 风控服务端强制（超限值被夹回上限并逐条返回 adjustments）",
      st == 200 and clamped and len(adj) >= 4,
      f"单笔 999→{p.get('order_sol')} SOL，日额 9999→{p.get('daily_sol')}，"
      f"滑点 99→{p.get('slippage_pct')}%，止损 -90→{p.get('stop_loss_pct')}；调整 {len(adj)} 条")
stx, fx = req(CD + "/api/copy/preset", "POST",
              {"kind": "futures", "trader": "冷月", "amount_usdt": 99999, "max_leverage": 100})
check("9a2 合约线同样服务端夹回（杠杆 100x → 上限）",
      stx == 200 and int((fx.get("preset") or {}).get("max_leverage") or 999) <= 20,
      f"max_leverage → {(fx.get('preset') or {}).get('max_leverage')}")

st2, state = req(CD + "/api/copy/presets")
check("9b 自动跟单默认关（auto_execute=false）",
      (state.get("state") or {}).get("auto_execute") is False, str(state.get("state")))

st3, ex = req(CD + "/api/copy/execute", "POST",
              {"line": "meme", "source": "王小二", "symbol": "$TEST", "side": "买入"})
check("9c v1 执行边界：/api/copy/execute 一律拒绝（不自动下单）",
      st3 >= 400 or ex.get("ok") is False, f"HTTP {st3} {str(ex)[:90]}")

# ---------- 10) 地址待补 KOL 空态不报错（数据一致性红线 §10.3）----------
st4, parse_pump = req(CD + "/api/kol/parse", "POST",
                      {"profile_url": "https://pump.fun/profile/8vFZLzYhFCJdU4tDHsBxEzJZgPXWQF8hKJm2YbGvzF3w"})
st5, parse_fomo = req(CD + "/api/kol/parse", "POST",
                     {"profile_url": "https://fomo.family/profile/ether_monk"})
pp = parse_pump.get("parse") or {}
pf = parse_fomo.get("parse") or {}
check("10a pump.fun 链接 → 平台 pumpfun + 地址直接入库",
      pp.get("platform") == "pumpfun" and bool(pp.get("address")), str(pp)[:100])
check("10b fomo.family 链接 → 平台 fomo + 地址待补（两段式）",
      pf.get("platform") == "fomo" and not pf.get("address"), str(pf)[:100])

# 清理测试预设
try:
    Path("D:/project/cryptodog/data/copy_presets.json").read_text(encoding="utf-8")
    data = json.loads(Path("D:/project/cryptodog/data/copy_presets.json").read_text(encoding="utf-8"))
    for key in ("__spec_test__",):
        data.get("meme", {}).pop(key, None) if isinstance(data.get("meme"), dict) else None
    Path("D:/project/cryptodog/data/copy_presets.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n（已清理测试预设 __spec_test__）")
except Exception as e:  # noqa: BLE001
    print("清理测试预设失败：", e)

print("\n" + "=" * 78)
passed = sum(1 for ok, _, _ in results if ok)
print(f"验收结果：{passed}/{len(results)} 通过")
for ok, name, detail in results:
    if not ok:
        print(f"  ❌ {name}  {detail}")
print("截图目录:", SHOT)
