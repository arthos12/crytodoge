# CryptoDog（加密狗）· 跟单网站 v1

> 独立站，**不属于投资仪表盘**（仪表盘文件 0 改动）。
> 规格：`memory/design_copytrade_site.md`（§1-10 规格 / §11 实现状态 / §12 给 workbuddy 的实现反馈）
> 视觉稿：`docs/design-copytrade-site.html` · Logo：`docs/assets/cryptodog-logo.svg`

## 快速开始

```bash
# 1) 起站（FastAPI:8100；同时需要仪表盘后端 8000 提供 MEME 数据层）
D:/project/.venv/Scripts/python.exe D:/project/scripts/dashboard_server.py      # 数据层
D:/project/.venv/Scripts/python.exe D:/project/cryptodog/server.py              # → http://localhost:8100/

# 2) 采集合约线数据（首次必须跑，否则合约两页为空）
D:/project/.venv/Scripts/python.exe D:/project/cryptodog/run_collectors.py      # 约 35s，含验证输出
```

打开 <http://localhost:8100/>（LAN 也可：`http://<本机IP>:8100/`）。

## 目录

```
cryptodog/
├── server.py                  FastAPI:8100（静态前端 + API + logo 代理 + KOL 注册表）
├── index.html                 SPA（五视图 hash 路由；token 抄仪表盘 Design System v2.1）
├── signals.py                 KOL 信号统计（多次买入/大额买入/早期埋伏）+ 规则可解释研判
├── run_collectors.py          采集器统一启动器（给 cron 用，顺序执行 + 落日志 + 验证输出）
├── collectors/
│   ├── futures_market.py      币安 fapi 公开接口：全网 OI / 资金费率 / 标记价
│   └── futures_traders.py     Hyperliquid 公开带单：交易员指标 + 当前持仓
├── assets/cryptodog-logo.svg  站标（内联使用）
└── data/                      本站自有状态（见下）
```

`data/` 内容：`futures_market.json` `futures_traders.json`（采集产物）、`kol_registry.json`（本站 KOL 登记表）、`copy_presets.json` `copy_log.json` `copy_state.json`（跟单预设/执行记录/熔断状态）、`kol_signals/*.json`（信号与研判缓存）、`signal_config.json`（信号阈值，可选）、`collector.log`、`verify/*.png`（验收截图）。

## 五视图与路由

| 路由 | 视图 | 说明 |
|---|---|---|
| `/futures/monitor` | 📊 合约监控（整站落地） | 全网 OI/资金费率 KPI、渠道 chips、仓位表（方向胶囊/强平价警示/跟单按钮）、30s 自动刷新 |
| `/futures/copy` | 📑 合约跟单 | 交易员榜单卡 + 跟单配置面板（modal）+ 执行记录 |
| `/meme/assets` | 💰 资产与交易（点 MEME 导航落地） | **信号驱动**：🤖 AI 重点关注分析置顶 + 🔥多次买入/💰大额买入/⏳早期埋伏 三组表 + 完整持仓明细（默认收起）+ 交易记录 v2（买卖配对/流水） |
| `/meme/kol` | 👤 KOL 管理 | 粘贴链接识别平台 → 增 / 删（两步内联确认）/ 改（含补地址） |
| `/meme/copy` | ⚡ MEME 跟单 | KOL 卡（地址待补则禁用）+ 预设表单 + 执行记录 |

## 定时采集（cron 交付）

**脚本**：`D:/project/cryptodog/run_collectors.py`（顺序跑两个采集器，日志落 `data/collector.log`，退出码 0/1）
**建议频率**：合约行情 **每 15 分钟**（后端缓存 TTL 5 分钟）；带单数据可 **每 1 小时**（`--only traders`）
**命令**：

```powershell
D:/project/.venv/Scripts/python.exe D:/project/cryptodog/run_collectors.py
# 只跑一个：  ... run_collectors.py --only market
#             ... run_collectors.py --only traders
```

**验证方法**（三选一，均在日志/接口可见）：
1. 看日志尾部：`[时间] 验证 · 合约行情: updated_at=... age=23s` + `带单交易员: trader_count=8 position_count=30`
2. 接口：`GET http://localhost:8100/api/health` → `data.futures_market_updated_at` / `data.futures_traders_updated_at` 时间戳前进
3. 页面：合约监控右上角"更新"时间随采集推进

> ⚠ **注册动作未做**：cron 基础设施在 C 盘（`AppData\Local\hermes\cron`），按项目铁律（C 盘默认不访问）**需你或 Hermes 侧注册**。参数即上面三行。

## 数据源与已知限制

| 数据 | 来源 | 状态 |
|---|---|---|
| 全市场资金费率 + 标记价 | 币安 `fapi/v1/premiumIndex`（不带 symbol） | ✅ 一次拿全 900 合约 |
| 24h 价/涨跌 | 币安 `fapi/v1/ticker/24hr`（不带 symbol） | ✅ 一次拿全 |
| 单币 OI + 24h 变化 | 币安 `futures/data/openInterestHist` | ✅ 逐币 1 请求 |
| 带单交易员 + 当前持仓 | Hyperliquid 公开带单 | ✅ 真实（`roi_month/win_rate/max_drawdown_pct/leverage_pref/position_count`）；**无 copier 名额**，故无"跟随中 N 人" |
| 24h 爆仓（多空拆分） | 币安 REST | ❌ 接口已下线（404）→ UI 如实标注"未接入"，需 WebSocket `forceOrder` |
| MEME KOL 资产/交易流 | **复用仪表盘** `/api/wallets/positions`、`/api/wallets/trades`、`/api/fomo/profiles` | ✅ 只读代理，不复制聚合逻辑 |
| 代币 logo | 仪表盘 `/api/logo` 代理（服务端抓取 + IPFS 换网关 + 磁盘缓存） | ✅ 失败降级首字母圆牌，不留破图 |
| 「⏳ 早期埋伏」信号 | 由交易流统计 | ⚠ 仪表盘 tracker 每轮只抓最近 25–61 笔 ≈ **2 天窗口**，凑不出"首买 >14 天" → 常为空，UI 已加原因说明空态；根治需采集侧分页回溯历史 |

## 安全边界（规格 §5）

- **v1 只读**：`POST /api/copy/execute` **一律拒绝**（未接入任何交易所下单权限），只落一条 `blocked` 记录供追溯
- **自动跟单默认关**：`data/copy_state.json` 的 `auto_execute=false`，UI 显示为服务端锁定的开关
- **风控服务端强制**（前端改不动）：MEME 单笔 ≤2 SOL / 每日 ≤5 SOL、≤10 笔 / 滑点 ≤10% / 止损 ≥-50%；合约 杠杆 ≤20x / 单笔 ≤2000 USDT / 单币 ≤5000 USDT。超限值**被夹回并逐条返回 `adjustments`**（实测：单笔 999→2、日额 9999→5、滑点 99→10、止损 -90→-50、杠杆 100x→20x）
- 全局熔断按钮可用

## 自检 / 验收

```bash
# 规格 §7 验收清单逐条实测（33 项，含前端交互 + 风控强制）
D:/project/.venv/Scripts/python.exe D:/project/cryptodog/_verify_spec_checklist.py
# 信号统计自检（真实数据）
D:/project/.venv/Scripts/python.exe D:/project/cryptodog/_selftest_signals.py
# 采集器验证
D:/project/.venv/Scripts/python.exe D:/project/cryptodog/run_collectors.py
```
最近的验收结果：**33/33 通过**，截图 `data/verify/spec_*.png`。
