# 更新日志 · CryptoDog（加密狗）跟单站

版本规范见 `README.md`「版本」节。**每次发版必须同步四处**：`VERSION` 文件 → `CHANGELOG.md` → `git tag vX.Y.Z` → `memory/cryptodog_versioning.md` 版本清单。

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
