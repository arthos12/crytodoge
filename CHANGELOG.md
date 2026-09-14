## [1.2.1] - 2026-09-14

修复「代币显示太大」+ 全面 BUG 回归。

- **修复**：`tokenLogoHtml(logo, sym, cls)` 丢弃第 4 参 px → `<img class="ava">` 无尺寸约束，
  CDN 原图（实测最大 1254px）直接渲染撑爆表格。现签名收 `px=26` 默认值，img 加内联
  `width/height`，`.ava` CSS 补 `max-width/max-height:44px` 兜底；5 处调用点全部显式传参
  （资产主表 26、合约 tokenCell 22、watchlist 26、交易流水 22）。
- **修复**：onerror 回退 HTML 引号转义（单引号嵌套 → `&quot;/&#39;`），回退头像不再破坏属性。
- **回归验证**（playwright，`data/tmp/bugcheck_cryptodog.py`）：14/15 PASS，
  唯一 FAIL 为验收脚本自身选择器笔误（徽章实际渲染 6 处，复测通过）。
  修复后实测：141 个头像全部 ≤48px（max 26×26）、333 行行高 44-49px 均匀、
  无横向溢出、无 pageerror、🔥 徽章与 signalMarks（11 键）链路正常。

## [1.2.0] - 2026-09-14

规格落地 `memory/design_copytrade_site.md` §16 / §19 / §20（前端与数据打通）。

- §16 导航：删除覆盖新短棒下划线的旧通栏渐变规则；副标身份色贯穿（`.sub-tab.on` 顶线 + `--subnav-line`）；nav-m 补 `data-line="meme"`
- §19 移除 MEME 跟单：`LINES.m` 只剩 💰 资产与交易 / 👤 KOL 管理；`meme/copy`、跟单路由与副标文案清零
- §20 资产与交易改版：
  - 资产主表全量罗列持仓（去默认收起、去 120 行截断）
  - 🔥 多次买入 / 💰 大额买入 / ⏳ 早期埋伏 徽章（`signalMarks` + `buildSignalMarks`）
  - 修复半途残渣崩页：删除引用未定义 `rep/big/early` 的三张信号表（选中 KOL 即 ReferenceError）
  - 交易明细默认流水视图；统计口径只含 buy/sell（充提/转账不展示不计入）
  - 早期埋伏空态给出原因（KOL 钱包链上历史仅 0.1–2.2 天，数据源限制）
- server.py：spec 标识更新 §1-20

验收：node --check 通过；/api/meme/signals?kol=王小二 → watchlist=8, repeat_buys=11；
/api/meme/assets → 560 positions / 3 kols / 300 trades（依赖 8000 投研后端在跑）。

# 更新日志 · CryptoDog（加密狗）跟单站

版本规范见 `README.md`「版本」节。**每次发版必须同步四处**：`VERSION` 文件 → `CHANGELOG.md` → `git tag vX.Y.Z` → `memory/cryptodog_versioning.md` 版本清单。

---

## [1.1.0] — 2026-09-12

**主题：链上完整历史回溯 + 「早期埋伏」空态给出有据可依的结论**（规格 §11.1.2 指定的收尾项）

> ⚠ 本版最重要的产出是**推翻了一个错误前提**：此前记录「早期埋伏恒空是因为 tracker 只抓最近 25–61 笔（≈2 天），
> 根治需采集侧分页回溯」。实测证明**分页回溯翻不出任何东西**——见下「实测结论」。

### 新增

- **`collectors/kol_history.py`**：Solana 链上历史回溯采集器
  - `getSignaturesForAddress` 带 `before` 游标翻页 + `getTransaction(jsonParsed)` 逐笔解析
  - ⚠ **公共 RPC 禁止批量**：publicnode 明确回 `"Maximum number of 'getTransaction' calls in a batch request is 1"`，
    drpc 免费档不供 Solana → 改用 **8 并发单条** 调用（原批量方案实测 HTTP 500）
  - **幂等**：按签名集合 `parsed_sigs` 去重（聚合是累加式的，靠游标猜会双计）；RPC 失败不标记已解析，下轮重试
  - 产出 `data/kol_history/{name}.json`：每 mint 的首买/末买时间、买卖笔数、买卖数量、SOL 收支 + 窗口跨度 + `exhausted`
- **`GET /api/meme/history?kol=`**：暴露上述历史与「已翻到头」证据
- **信号接入完整历史**（`signals.py`，`SCHEMA` 2→3 自动失效旧缓存）：
  首买时间/累计买入/已卖出占比优先用**完整链上历史**校正；`data_scope` 新增
  `wallet_span_days` / `wallet_first_tx` / `wallet_history_exhausted` / `early_empty_reason`
- **前端空态改为有据可依**：早期埋伏为空时显示
  「该 KOL 钱包链上历史共 X 天（已翻到最早一笔 YYYY-MM-DD），不足判定…属钱包本身历史长度限制，非采集缺失」，
  数据窗口标注也带上「链上完整历史 N 天（已翻到最早）」

### 实测结论（重要，别再重复试错）

| KOL | 该地址签名总数 | 最早 | 最新 | 完整历史跨度 | 是否已翻到头 |
|---|---|---|---|---|---|
| 王小二 | 74 | 2026-09-10 18:27 | 2026-09-12 11:51 | **1.7 天**（代币交易 1.0 天） | ✅ `exhausted=true` |
| Ethermonk | 62 | 2026-09-10 14:14 | 2026-09-12 18:19 | **2.2 天**（1.8 天） | ✅ |
| 镭射猫 | 32 | 2026-09-10 17:37 | 2026-09-12 15:27 | **1.9 天**（0.1 天） | ✅ |

⇒ **三个被追踪的钱包本身只有 1–2 天链上历史**（不是 tracker 只抓了 25 笔）。
因此「早期埋伏（需首买 >14 天）」为空是**数据现实**，不是功能缺陷；**分页回溯无收益**（已翻到头，无更早数据）。
要真正出现早期埋伏，需追踪**有更长历史的老钱包**（或换用 KOL 的历史主钱包地址）。

### 变更

- `server.py`：`spec` 标识由 `§1-10` 更正为 `§1-14`（规格文件实际已扩到 §14）
- 前端数据窗口标注、早期埋伏空态文案（见上）

### 验收

- 规格 §7 清单复跑 **33/33 通过**（`_verify_spec_checklist.py`）· 五视图 0 pageerror
- `kol_history.py` 三个 KOL 全部 `exhausted=true`，窗口与上面的实测表一致
- `GET /api/meme/signals?kol=王小二` → `schema=3`、`history.used=true`、`wallet_span_days=1.0`、
  `can_judge_early=false` 且 `early_empty_reason` 为上述有据说明

### 已知限制

- 「24h 爆仓」仍显示「未接入」（币安 `/fapi/v1/allForceOrders` 已下线；需 WebSocket `forceOrder`）
- 「大额买入」阈值 $5k 在 1–2 天窗口内多为 0 条（阈值可在 `data/signal_config.json` 调）
- 早期埋伏要出数需**换更长历史的钱包地址**（当前三个地址均 <2.2 天）——见上实测表

---

## [1.0.0] — 2026-09-12

首个发布版本：加密狗跟单站 v1 上线（本地 `D:/project/cryptodog/`，远端 `arthos12/crytodoge`）。

### 新增

- **五视图 SPA**（hash 路由）：`/futures/monitor`（合约监控·整站落地）· `/futures/copy`（合约跟单）· `/meme/assets`（资产与交易·点 MEME 落地）· `/meme/kol`（KOL 管理）· `/meme/copy`（MEME 跟单）
- **FastAPI 后端** `server.py`（:8100）：静态前端 + 只读 API + 代币 logo 代理（服务端抓取 / IPFS 换网关 / 磁盘缓存 / 首字母降级）+ KOL 注册表
- **采集器**：`collectors/futures_market.py`（币安全网 OI / 资金费率 / 标记价）、`collectors/futures_traders.py`（Hyperliquid 公开带单：交易员指标 + 当前持仓）
- **采集启动器** `run_collectors.py`：顺序执行两采集器 + 落 `data/collector.log` + 末尾验证输出 + 退出码 0/1（给 cron 用）
- **KOL 信号驱动** `signals.py`：多次买入 / 大额买入 / 早期埋伏 三类信号 + 规则可解释研判（配 `CRYPTODOG_LLM_KEY` 后可叠加 LLM 研判），带 `SCHEMA` 缓存版本防读旧结构
- **风控硬性上限**（服务端强制、前端改不动）：MEME 单笔 ≤2 SOL / 每日 ≤5 SOL / ≤10 笔 / 滑点 ≤10% / 止损 ≥-50%；合约 杠杆 ≤20x / 单笔 ≤2000 USDT / 单币 ≤5000 USDT；超限值夹回并逐条返回 `adjustments`
- **安全边界**：v1 只读 —— `POST /api/copy/execute` 一律拒绝（只落 `blocked` 记录）；自动跟单默认关（`data/copy_state.json` 的 `auto_execute=false`）
- **版本标识**：新增 `VERSION` 文件（单一事实来源）+ 顶栏版本徽标 + `GET /api/health` 返回 `version` / `spec`

### 验收

- 规格 §7 验收清单 **33/33 通过**（`_verify_spec_checklist.py`），五视图 0 pageerror，截图 `data/verify/*.png`
- 合约监控真实数据：全网 OI `$17.48B / +0.13% 24h`；合约跟单显示真实交易员 ROI

### 已知限制（诚实记录）

- 「24h 爆仓」显示「未接入」：币安 `/fapi/v1/allForceOrders` 已下线（404），REST 无爆仓数据，需 WebSocket `forceOrder`
- 「⏳ 早期埋伏」常为空：仪表盘 tracker 每轮只抓最近 25–61 笔 ≈ **2 天窗口**，凑不出「首买 >14 天」，根治需采集侧分页回溯历史
- 交易员卡无「跟随中 N 人」：Hyperliquid 数据源不提供 copier 名额
- cron 注册未做：cron 基础设施在 C 盘（`AppData\Local\hermes\cron`），按项目铁律需用户/Hermes 侧注册，参数见 `README.md`「定时采集」

### 版本基线

站点 `1.0.0` ｜ 规格 `memory/design_copytrade_site.md` §1-10 ｜ 信号缓存 schema 见 `signals.py:SCHEMA` ｜ 数据层依赖仪表盘后端（:8000，独立版本轴，不随本站发版）
