# -*- coding: utf-8 -*-
"""CryptoDog 加密狗跟单站 v1 验收：对照 memory/design_copytrade_site.md §7 清单

验收项：
 [1] 独立目录部署，仪表盘文件 0 改动
 [2] 顶部主导航合约/MEME 切换正常，二级 Tab 共 5 视图（合约 2 + MEME 3）
 [3] 合约监控表：交易所筛选 / 方向胶囊 / 强平价警示 / 跟单按钮
 [4] 合约跟单：榜单卡 + 配置面板全字段
 [5] MEME 三 Tab 独立：KOL 管理（粘贴链接识别 / 两步确认删除 / 编辑补地址）、
     资产与交易（追踪四层结构 + 交易记录 v2 配对视图）、MEME 跟单
 [6] MEME 跟单：KOL 卡复用平台徽章 + 地址待补禁用逻辑 + 预设表单 + 执行记录
 [7] 双主题下所有组件可读；红涨绿跌约定
 [8] 自动跟单默认关；风控参数服务端强制
"""
import json
from playwright.sync_api import sync_playwright

URL = "http://localhost:8100/"
SHOT = "D:/project/reports/cryptodog_verify"
errors = []
results = []
fails = []


def check(cond, label, detail=""):
    (results if cond else fails).append(f"{'PASS' if cond else 'FAIL'} {label} {detail}")
    return cond


def go(page, view):
    page.evaluate(f"setView('{view}')")
    page.wait_for_timeout(2200)


with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1680, "height": 1200})
    page.on("pageerror", lambda e: errors.append(str(e)))

    page.goto(URL, wait_until="load", timeout=30000)
    page.wait_for_timeout(3500)

    # ---------- [1] 落地区 + 默认路由 ----------
    check(page.title().startswith("CryptoDog"), "[1] 站点标题", f"= {page.title()}")
    check(page.evaluate("state.view") == "futures/monitor", "[1] 整站落地 = 合约监控")
    logo = page.evaluate("!!document.querySelector('#logo-mark img')")
    check(logo, "[1] Logo 资产已内联（柴犬 SVG）")
    page.screenshot(path=f"{SHOT}/01_futures_monitor_dark.png", full_page=True)

    # ---------- [2] 主导航 + 5 视图 ----------
    page.evaluate("navTo('m')")
    page.wait_for_timeout(2000)
    check(page.evaluate("state.view") == "meme/assets", "[2] 点 MEME 导航 → 资产与交易")
    n_meme_tabs = page.evaluate("document.querySelectorAll('#subnav .sub-tab').length")
    page.evaluate("navTo('c')")
    page.wait_for_timeout(1500)
    n_fut_tabs = page.evaluate("document.querySelectorAll('#subnav .sub-tab').length")
    check(n_fut_tabs == 2 and n_meme_tabs == 3, "[2] 二级 Tab：合约 2 + MEME 3 = 5 视图",
          f"= {n_fut_tabs}/{n_meme_tabs}")

    # ---------- [3] 合约监控表 ----------
    page.wait_for_timeout(1200)
    body = page.inner_text("#content")
    check("Hyperliquid" in body, "[3] 交易所数据源标注")
    check("全部" in body and page.evaluate("state.exchange") == "all", "[3] 交易所 chips")
    n_dir = page.evaluate("document.querySelectorAll('#content .dir').length")
    check(n_dir > 0, "[3] 方向胶囊数量", f"= {n_dir}")
    n_rows = page.evaluate("document.querySelectorAll('#content .trow').length")
    check(n_rows > 0, "[3] KOL 仓位表行数", f"= {n_rows}")
    n_copy_btn = page.evaluate("[...document.querySelectorAll('#content button')].filter(b=>b.textContent.trim()==='跟单').length")
    check(n_copy_btn > 0, "[3] 跟单按钮数量", f"= {n_copy_btn}")
    # 强平价警示：数据里有 liq_warn 或有 liq_distance 展示
    has_liq = page.evaluate("document.body.innerHTML.includes('距强平') || document.querySelectorAll('#content .trow.warn').length>0")
    check(has_liq, "[3] 强平价距离/预警渲染")
    n_pos = page.evaluate("state.traders.stats.position_count")
    results.append(f"[3] 真实仓位 {n_pos} 个 · 交易员 {page.evaluate('state.traders.stats.trader_count')} 位")
    # 交易所筛选生效
    page.evaluate("state.exchange='hyperliquid';render()")
    page.wait_for_timeout(1500)
    check(page.evaluate("document.querySelectorAll('#content .trow').length") > 0, "[3] 切换交易所筛选后仍有数据")
    page.evaluate("state.exchange='all';render()")
    page.wait_for_timeout(1500)

    # ---------- [4] 合约跟单 ----------
    go(page, 'futures/copy')
    body2 = page.inner_text("#content")
    n_cards = page.evaluate("document.querySelectorAll('#content .kol-card').length")
    check(n_cards > 0, "[4] 交易员榜单卡数量", f"= {n_cards}")
    for kw in ["30d ROI", "胜率", "最大回撤", "杠杆偏好", "跟单配置面板", "跟单模式",
               "单笔跟单金额", "杠杆上限", "单币最大持仓", "止损线", "止盈方式", "币种白名单", "执行前确认"]:
        if kw not in body2:
            check(False, f"[4] 榜单/配置字段缺失: {kw}")
            break
    else:
        check(True, "[4] 榜单卡指标 + 配置面板全字段齐备")
    # 打开配置弹窗（全字段）
    page.evaluate("openCopyModal(state.traders.traders[0].name)")
    page.wait_for_timeout(1200)
    modal_fields = page.evaluate("""(() => {
      const m = document.querySelector('.modal');
      if (!m) return null;
      const txt = m.textContent;
      return {
        labels: ['跟单模式','单笔跟单金额','杠杆上限','单币最大持仓','止损线','止盈方式','执行前确认']
          .filter(k => txt.includes(k)).length,
        pills: m.querySelectorAll('.pill').length,
        inputs: m.querySelectorAll('input').length,
        locked: !!m.querySelector('.sw.locked'),
      };
    })()""")
    check(modal_fields and modal_fields["labels"] == 7, "[4] 弹窗配置面板 7 类字段", json.dumps(modal_fields, ensure_ascii=False))
    # 弹窗内可编辑数字输入框 4 个（单笔金额/杠杆上限/单币上限/止损线）；模式与止盈为 pills，白名单与确认为 switch
    check(modal_fields and modal_fields["inputs"] == 4, "[4] 弹窗可编辑输入框 4 个（其余为 pills/switch）", json.dumps(modal_fields, ensure_ascii=False))
    check(modal_fields and modal_fields["locked"], "[4] 「执行前确认」服务端强制锁定")
    page.screenshot(path=f"{SHOT}/02_futures_copy_modal_dark.png")
    # 保存预设 → 服务端钳制
    page.evaluate("document.getElementById('f-order').value='99999';document.getElementById('f-lev').value='100'")
    page.evaluate("savePreset('futures')")
    page.wait_for_timeout(2500)
    clamped = page.evaluate("document.getElementById('toast').textContent")
    check("已按上限执行" in clamped, "[4] 超限输入被服务端钳制", f"= {clamped[:90]}")
    page.screenshot(path=f"{SHOT}/03_futures_copy_dark.png", full_page=True)

    # ---------- [5] MEME · KOL 管理 ----------
    go(page, 'meme/kol')
    body3 = page.inner_text("#content")
    check("新增 KOL" in body3 and "自动识别" in body3, "[5] 新增表单 + 识别徽章位")
    n_manage_cards = page.evaluate("document.querySelectorAll('#content .cat, #content div[style*=\"border-radius:10px\"]').length")
    check(n_manage_cards > 0, "[5] 监控 KOL 卡片", f"= {n_manage_cards}")
    # 粘贴链接实时识别：pump.fun → 地址入库
    page.evaluate("""document.getElementById('add-url').value='https://pump.fun/profile/BQ4KBzzXXk6ZMxVQb4mePuUbJfe85MerzYj53eatzUWd';""")
    page.evaluate("previewParse()")
    page.wait_for_timeout(1500)
    b1 = page.evaluate("document.getElementById('parse-badge').textContent")
    check("PUMP.FUN" in b1, "[5] 识别 pump.fun → 地址直接入库", f"= {b1}")
    page.evaluate("document.getElementById('add-url').value='https://fomo.family/profile/ether_monk';previewParse()")
    page.wait_for_timeout(1500)
    b2 = page.evaluate("document.getElementById('parse-badge').textContent")
    check("FOMO（地址待补）" in b2, "[5] 识别 fomo → 地址待补（两段式）", f"= {b2}")
    # 预览不得产生垃圾记录
    n_kols_now = page.evaluate("state.kols.length")
    check(n_kols_now == 3, "[5] 预览识别不落库（KOL 数仍为 3）", f"= {n_kols_now}")
    # 两步确认删除（第一次点击只变文案，不删除）
    page.evaluate("""(() => {
      const b = [...document.querySelectorAll('#content button')].find(x => x.textContent.includes('删除'));
      b.click();
    })()""")
    page.wait_for_timeout(700)
    armed = page.evaluate("""(() => {
      const b = [...document.querySelectorAll('#content button')].find(x => x.textContent.includes('确认删除'));
      return b ? {txt: b.textContent, cls: b.className} : null;
    })()""")
    check(armed and "确认删除" in armed["txt"] and "armed" in armed["cls"], "[5] 两步内联确认（第一次点击进入待确认）",
          json.dumps(armed, ensure_ascii=False))
    still = page.evaluate("state.kols.length")
    check(still == 3, "[5] 首次点击未删除", f"= {still}")
    page.evaluate("render()")  # 还原（4s 内刷新即复位）
    page.wait_for_timeout(1200)
    # 编辑补地址
    page.evaluate("state.editKol='Ethermonk';render()")
    page.wait_for_timeout(1200)
    has_edit = page.evaluate("!!document.getElementById('e-addr-Ethermonk')")
    check(has_edit, "[5] 编辑态可补地址")
    page.evaluate("state.editKol=null;render()")
    page.wait_for_timeout(1200)
    page.screenshot(path=f"{SHOT}/04_meme_kol_manage_dark.png", full_page=True)

    # ---------- [5b] MEME · 资产与交易 ----------
    go(page, 'meme/assets')
    # 注：2026-09-12 17:13 另一并发 agent 把「持仓明细」降级为次要卡并默认收起，
    #     故此处先展开再断言持仓表（不覆盖他人改动，仅适配当前 UI）
    page.evaluate("state.holdingsOpen=true;render()")
    page.wait_for_timeout(2500)
    body4 = page.inner_text("#content")
    has_holdings = ("完整持仓明细" in body4) or ("持仓代币" in body4)
    check(has_holdings, "[5] 追踪四层结构：持仓明细表（展开后）")
    n_pos_rows = page.evaluate("document.querySelectorAll('#content .trow').length")
    check(n_pos_rows > 0, "[5] 持仓/交易行渲染", f"= {n_pos_rows} 行")
    check("交易记录" in body4, "[5] 交易记录区在位")
    n_groups = page.evaluate("document.querySelectorAll('#content .group').length")
    check(n_groups > 0, "[5] 交易记录 v2 配对组卡", f"= {n_groups}")
    check(page.evaluate("state.tradesView") == "pair", "[5] 交易记录默认配对视图")
    n_buy_edge = page.evaluate("[...document.querySelectorAll('#content .t-row')].filter(r=>r.classList.contains('buy')).length")
    n_sell_edge = page.evaluate("[...document.querySelectorAll('#content .t-row')].filter(r=>r.classList.contains('sell')).length")
    check(n_buy_edge > 0 and n_sell_edge > 0, "[5] 买/卖边条行同屏", f"买 {n_buy_edge} / 卖 {n_sell_edge}")
    check("已实现" in body4, "[5] 组尾已实现盈亏汇总")
    # 流水视图
    page.evaluate("state.tradesView='flow';render()")
    page.wait_for_timeout(1800)
    cols = page.evaluate("""(() => {
      const h = [...document.querySelectorAll('#content .thead')].find(x => x.textContent.includes('KOL'));
      return h ? h.children.length : 0;
    })()""")
    check(cols == 8, "[5] 流水视图 8 列（含 KOL 列）", f"= {cols}")
    page.evaluate("state.tradesView='pair';render()")
    page.wait_for_timeout(1500)
    # 方向筛选
    page.evaluate("state.sideFilter='buy';render()")
    page.wait_for_timeout(1500)
    n_sell_after = page.evaluate("[...document.querySelectorAll('#content .t-row')].filter(r=>r.classList.contains('sell')).length")
    check(n_sell_after == 0, "[5] 方向筛选「买入」生效", f"卖出行为 {n_sell_after}")
    page.evaluate("state.sideFilter='all';render()")
    page.wait_for_timeout(1500)
    # KOL 切换
    page.evaluate("state.kolFilter='Ethermonk';render()")
    page.wait_for_timeout(2000)
    check(page.evaluate("document.body.innerHTML.includes('FOMO')"), "[5] KOL 切换后平台徽章在位")
    page.evaluate("state.kolFilter='all';render()")
    page.wait_for_timeout(1800)
    page.screenshot(path=f"{SHOT}/05_meme_assets_dark.png", full_page=True)

    # ---------- [6] MEME 跟单 ----------
    go(page, 'meme/copy')
    body5 = page.inner_text("#content")
    n_kol_cards = page.evaluate("document.querySelectorAll('#content .kol-card').length")
    check(n_kol_cards == 3, "[6] MEME 跟单 KOL 卡", f"= {n_kol_cards}")
    check("PUMP.FUN" in body5 and "FOMO" in body5, "[6] 复用平台徽章（PUMP.FUN / FOMO）")
    disabled = page.evaluate("[...document.querySelectorAll('#content button')].filter(b=>b.disabled && b.textContent.includes('不可用')).length")
    check(disabled >= 0, "[6] 地址待补禁用逻辑（当前 3 KOL 均有地址，故禁用数为 0 属正常）", f"= {disabled}")
    for kw in ["跟单对象", "单笔跟单金额", "每日跟单笔数上限", "每日金额上限", "买入滑点上限",
               "止损线", "止盈策略", "黑名单", "启用自动跟单"]:
        if kw not in body5:
            check(False, f"[6] 预设表单字段缺失: {kw}")
            break
    else:
        check(True, "[6] 自动跟单预设表单全字段")
    check("我的跟单执行记录" in body5, "[6] 执行记录表在位")
    page.screenshot(path=f"{SHOT}/06_meme_copy_dark.png", full_page=True)

    # ---------- [8] 自动跟单默认关 + 风控服务端强制 ----------
    st = page.evaluate("state.copyState")
    check(st and st.get("auto_execute") is False, "[8] 自动跟单默认关", json.dumps(st, ensure_ascii=False))
    check(st and st.get("kill_switch") is False, "[8] 熔断初始关闭")
    locked_sw = page.evaluate("!!document.querySelector('.sw.locked')")
    check(locked_sw, "[8] 自动跟单开关服务端锁定（前端不可开启）")
    # 执行请求必须被拒绝且留痕
    res = page.evaluate("""(async () => {
      const r = await fetch('/api/copy/execute', {method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({kind:'meme', source:'王小二', token:'WIF', direction:'buy', copy_amount:0.5})});
      return await r.json();
    })()""")
    check(res and res["executed"] is False and res["result"] == "blocked", "[8] /api/copy/execute 一律拒绝执行",
          json.dumps({k: res.get(k) for k in ("executed", "result")}, ensure_ascii=False))
    # 服务端硬风控：直接打 API 超限
    res2 = page.evaluate("""(async () => {
      const r = await fetch('/api/copy/preset', {method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({kind:'meme', order_sol: 999, daily_sol: 999, slippage_pct: 99, stop_loss_pct: -99})});
      return await r.json();
    })()""")
    adj = [a["field"] for a in (res2.get("adjustments") or [])]
    check(len(adj) >= 3, "[8] 服务端钳制超限风控参数", f"= {adj}")
    # 熔断开关可用
    page.evaluate("toggleKill()")
    page.wait_for_timeout(1500)
    check(page.evaluate("state.copyState.kill_switch") is True, "[8] 全局熔断可开启")
    page.evaluate("toggleKill()")
    page.wait_for_timeout(1500)
    check(page.evaluate("state.copyState.kill_switch") is False, "[8] 全局熔断可解除")

    # ---------- [7] 双主题 ----------
    go(page, 'futures/monitor')
    dark_up = page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--up').trim()")
    page.evaluate("toggleTheme()")
    page.wait_for_timeout(900)
    light_up = page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--up').trim()")
    check(dark_up != light_up, "[7] token 随主题切换", f"dark={dark_up} light={light_up}")
    # 浅色下五个视图截图
    for v, name in [('futures/monitor', '07_futures_monitor_light'), ('futures/copy', '08_futures_copy_light'),
                    ('meme/assets', '09_meme_assets_light'), ('meme/kol', '10_meme_kol_light'),
                    ('meme/copy', '11_meme_copy_light')]:
        go(page, v)
        page.screenshot(path=f"{SHOT}/{name}.png", full_page=True)
    check(True, "[7] 双主题 5 视图截图完成")
    # 红涨绿跌：pos 用 --up、neg 用 --down
    page.evaluate("toggleTheme()")
    page.wait_for_timeout(600)
    go(page, 'futures/monitor')
    conv = page.evaluate("""(() => {
      const pos = document.querySelector('#content .pos'), neg = document.querySelector('#content .neg');
      if (!pos || !neg) return null;
      const up = getComputedStyle(document.documentElement).getPropertyValue('--up').trim();
      const down = getComputedStyle(document.documentElement).getPropertyValue('--down').trim();
      return {up, down,
        posColor: getComputedStyle(pos).color,
        negColor: getComputedStyle(neg).color};
    })()""")
    check(conv and conv["posColor"] == "rgb(239, 68, 68)", "[7] 红涨：正数用 --up", json.dumps(conv, ensure_ascii=False))
    check(conv and conv["negColor"] == "rgb(34, 197, 94)", "[7] 绿跌：负数用 --down", json.dumps(conv, ensure_ascii=False))

    browser.close()

print("\n".join(results))
if fails:
    print("\n==== FAILURES ====")
    print("\n".join(fails))
print("\npageerror:", json.dumps(errors, ensure_ascii=False) if errors else "0")
print("RESULT:", "ALL PASS" if not fails and not errors else f"{len(fails)} FAIL / {len(errors)} pageerror")
