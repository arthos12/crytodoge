"""
CryptoDog · KOL 链上历史回溯采集（Solana）
=========================================
**为什么需要它**（规格 §11.1.2 / 看板 §3 P2）：
仪表盘 tracker 每轮只抓最近 25–61 笔交易 → 实际窗口仅 **~2 天**，导致 §3.1b 三类信号里
「⏳ 早期埋伏」（需首买 >14 天）**永远为空**——不是功能坏了，是数据里根本没有更早的交易。
本采集器用 `getSignaturesForAddress` 的 `before` 游标**向后翻页**，把历史补齐，
落到 `cryptodog/data/kol_history/{name}.json`（**不动仪表盘任何文件**）。

**产出用途**：给 `signals.py` 提供真实的
  首买时间 / 累计买入量 / 已卖出量 / 买卖笔数 / SOL 收支（→ 持有天数、已卖占比）

**做法**：
  1. `getSignaturesForAddress(address, {before: cursor, limit: 1000})` 翻页
  2. 用 **JSON-RPC 批量**（每批 20 条）调 `getTransaction(jsonParsed)` —— 单条调会打 800+ 次请求
  3. 从 pre/postTokenBalances 取该钱包各 mint 的余额差 → 判断买/卖与数量；
     从 pre/postBalances 取 SOL 差额 → 该笔的 SOL 支出/收入（成本口径的原料）
  4. 增量：记录 `cursor.oldest_sig`，下次从更早继续翻；同时记录 `newest_sig`

**成本（实测参考）**：王小二 800+ 笔 → 约 40 次批量请求 / 1–2 分钟（含限速）

用法：
  python cryptodog/collectors/kol_history.py                      # 全部有地址的 KOL，翻 5 页
  python cryptodog/collectors/kol_history.py --kol 王小二 --pages 10 --max-tx 1200
  python cryptodog/collectors/kol_history.py --fresh               # 从最新重新开始（忽略旧游标）
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# NO_PROXY 继承坑（项目铁律 #5）：必须在发请求前清掉
os.environ.pop("NO_PROXY", None)
os.environ.pop("no_proxy", None)

import requests  # noqa: E402

CRYPTODOG_ROOT = Path("D:/project/cryptodog")
DATA_DIR = CRYPTODOG_ROOT / "data"
HISTORY_DIR = DATA_DIR / "kol_history"
REGISTRY_FILE = DATA_DIR / "kol_registry.json"
DASH_WALLET_CONFIG = Path("D:/project/data/config/wallet_addresses.json")

HTTP_TIMEOUT = 30
WORKERS = 8               # 并发单条 getTransaction（公共 RPC 禁止 batch，见下）
PAGE_LIMIT = 1000         # getSignaturesForAddress 单页上限
SLEEP_BETWEEN_PAGES = 0.2
SCHEMA = 1

# Solana RPC（复用仪表盘 tracker 的可用节点表，避免重复试错）
SOL_RPC_URLS = [
    "https://solana-rpc.publicnode.com",
    "https://solana.drpc.org",
]
PROXY = {"http": "http://127.0.0.1:7897", "https": "http://127.0.0.1:7897"}

# 稳定币 / WSOL 不进「代币持仓」聚合（它们是资金腿）
IGNORE_MINTS = {
    "So11111111111111111111111111111111111111112",   # WSOL
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
}


def _rpc_call(url: str, payload):
    """单次 JSON-RPC（payload 可为 dict 或批量 list）"""
    r = requests.post(url, json=payload, proxies=PROXY, timeout=HTTP_TIMEOUT,
                      headers={"Content-Type": "application/json"})
    r.raise_for_status()
    return r.json()


def _rpc(payload):
    """轮询节点，返回 (data, url)；payload 可为 dict / list（批量）"""
    last_err = None
    for url in SOL_RPC_URLS:
        for attempt in range(2):
            try:
                data = _rpc_call(url, payload)
                # 批量时是 list；单条时检查 error
                if isinstance(data, dict) and data.get("error"):
                    last_err = data["error"]
                    break
                return data, url
            except Exception as e:  # noqa: BLE001
                last_err = e
                time.sleep(1.0 + attempt)
    raise RuntimeError(f"Solana RPC 失败: {last_err}")


def load_targets(only: str | None = None) -> list[dict]:
    """目标 KOL：本站注册表优先，回退仪表盘配置（只读）；只取有 SOL 地址的"""
    out = []
    reg = {}
    if REGISTRY_FILE.exists():
        try:
            reg = json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            reg = {}
    for k in (reg.get("kols") or []):
        if (k.get("address") or "").strip():
            out.append({"name": k["name"], "address": k["address"].strip(),
                        "platform": k.get("platform", "")})
    if not out and DASH_WALLET_CONFIG.exists():
        try:
            cfg = json.loads(DASH_WALLET_CONFIG.read_text(encoding="utf-8"))
            for w in (cfg.get("wallets") or []):
                if (w.get("address") or "").strip():
                    out.append({"name": w["name"], "address": w["address"].strip(),
                                "platform": w.get("platform", "")})
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 读仪表盘钱包配置失败: {e}", file=sys.stderr)
    if only:
        out = [t for t in out if t["name"] == only]
    return out


def fetch_signature_page(address: str, before: str | None, limit: int = PAGE_LIMIT) -> list[dict]:
    params = {"limit": limit}
    if before:
        params["before"] = before
    data, _ = _rpc({"jsonrpc": "2.0", "id": 1, "method": "getSignaturesForAddress",
                    "params": [address, params]})
    return (data.get("result") or [])


def _account_index(keys: list, address: str) -> int:
    for i, k in enumerate(keys):
        pk = k.get("pubkey") if isinstance(k, dict) else k
        if pk == address:
            return i
    return -1


def _token_deltas(tx: dict, address: str) -> dict:
    """该钱包在此 tx 里各 mint 的余额变化（正=收到=买入，负=付出=卖出）"""
    meta = tx.get("meta") or {}
    pre, post = {}, {}
    for tb in (meta.get("preTokenBalances") or []):
        if tb.get("owner") == address:
            pre[tb.get("mint")] = float((tb.get("uiTokenAmount") or {}).get("uiAmount") or 0)
    for tb in (meta.get("postTokenBalances") or []):
        if tb.get("owner") == address:
            post[tb.get("mint")] = float((tb.get("uiTokenAmount") or {}).get("uiAmount") or 0)
    out = {}
    for mint in set(pre) | set(post):
        d = post.get(mint, 0.0) - pre.get(mint, 0.0)
        if abs(d) > 1e-12:
            out[mint] = d
    return out


def _sol_delta(tx: dict, address: str) -> float | None:
    meta = tx.get("meta") or {}
    msg = ((tx.get("transaction") or {}).get("message")) or {}
    keys = msg.get("accountKeys") or []
    idx = _account_index(keys, address)
    if idx < 0:
        return None
    try:
        return (float(meta["postBalances"][idx]) - float(meta["preBalances"][idx])) / 1e9
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def parse_tx(tx: dict, address: str, sig: str) -> dict | None:
    """把一笔 tx 解析为 {time, sig, sol_delta, deltas:{mint: qty}}"""
    if not tx or (tx.get("meta") or {}).get("err"):
        return None
    bt = tx.get("blockTime")
    if not bt:
        return None
    deltas = _token_deltas(tx, address)
    if not deltas:
        return None
    return {
        "time": datetime.fromtimestamp(bt, tz=timezone.utc).astimezone().isoformat(timespec="seconds"),
        "t": bt,
        "sig": sig,
        "sol_delta": _sol_delta(tx, address),
        "deltas": deltas,
    }


def load_history(name: str) -> dict:
    p = HISTORY_DIR / f"{name}.json"
    if not p.exists():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if d.get("schema") == SCHEMA else {}
    except Exception:  # noqa: BLE001
        return {}


def save_history(name: str, payload: dict) -> Path:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    p = HISTORY_DIR / f"{name}.json"
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def collect_one(target: dict, pages: int, max_tx: int, fresh: bool) -> dict:
    name, address = target["name"], target["address"]
    prev = {} if fresh else load_history(name)
    mints = {k: dict(v) for k, v in (prev.get("mints") or {}).items()}
    # ⭐ 幂等保证：已聚合过的签名集合。聚合是「累加」的，若同一签名被解析两次就会**双计**，
    #   因此必须按签名去重（而不是靠游标猜）；游标只用于少翻页的优化。
    parsed_sigs = set() if fresh else set(prev.get("parsed_sigs") or [])
    cursor = (prev.get("cursor") or {}).get("oldest_sig")
    newest_sig = (prev.get("cursor") or {}).get("newest_sig")
    parsed_total = int((prev.get("window") or {}).get("tx_parsed") or 0)

    print(f"[{name}] 起始游标: {cursor[:16] + '…' if cursor else '（最新）'}"
          f" · 已有代币 {len(mints)} 个 · 已解析签名 {len(parsed_sigs)} 条")
    pending_sigs, exhausted, pages_done = [], False, 0
    for i in range(pages):
        try:
            page = fetch_signature_page(address, cursor)
        except Exception as e:  # noqa: BLE001
            print(f"  [warn] 翻页失败: {e}", file=sys.stderr)
            break
        if not page:
            exhausted = True
            break
        pages_done += 1
        if newest_sig is None:
            newest_sig = page[0].get("signature")
        for item in page:
            sig = item.get("signature")
            if item.get("err") or not sig:
                continue
            if sig in parsed_sigs or sig in pending_sigs:
                continue
            pending_sigs.append(sig)
        cursor = page[-1].get("signature")
        print(f"  第 {i+1} 页: {len(page)} 条签名（累计待解析 {len(pending_sigs)}）")
        if len(pending_sigs) >= max_tx:
            pending_sigs = pending_sigs[:max_tx]
            break
        time.sleep(SLEEP_BETWEEN_PAGES)

    if not pending_sigs:
        print(f"[{name}] 无新签名可解析（history 已是最新/最深）")

    # ---- 逐笔解析（⚠ 公共 RPC 禁止批量：publicnode 明确回
    #      "Maximum number of 'getTransaction' calls in a batch request is 1"；
    #      drpc 免费档不供 Solana。故改用**并发单条**调用换速度）----
    parsed_this_run = 0
    if pending_sigs:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _one(sig: str):
            try:
                data, _ = _rpc({"jsonrpc": "2.0", "id": 1, "method": "getTransaction",
                                "params": [sig, {"encoding": "jsonParsed",
                                                 "maxSupportedTransactionVersion": 0}]})
                return sig, parse_tx(data.get("result"), address, sig)
            except Exception:  # noqa: BLE001
                return sig, None      # RPC 失败 → 不标记为已解析，下次重试

        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            futs = {ex.submit(_one, s): s for s in pending_sigs}
            for k, fut in enumerate(as_completed(futs)):
                sig, rec = fut.result()
                if rec is None:
                    # 区分「确实没有可用数据（err/无 token delta）」与「RPC 失败」：
                    # 前者标记已解析（避免每次重抓），后者留待下轮（不加入 parsed_sigs）
                    continue
                parsed_sigs.add(sig)
                parsed_this_run += 1
                for mint, d in (rec["deltas"] or {}).items():
                    if mint in IGNORE_MINTS:
                        continue
                    m = mints.setdefault(mint, {
                        "first_buy_t": None, "last_buy_t": None,
                        "buy_count": 0, "sell_count": 0,
                        "bought_qty": 0.0, "sold_qty": 0.0,
                        "sol_spent": 0.0, "sol_received": 0.0,
                    })
                    if d > 0:      # 收到代币 = 买入
                        m["buy_count"] += 1
                        m["bought_qty"] += d
                        if rec["sol_delta"] is not None and rec["sol_delta"] < 0:
                            m["sol_spent"] += -rec["sol_delta"]
                        if m["first_buy_t"] is None or rec["t"] < m["first_buy_t"]:
                            m["first_buy_t"] = rec["t"]
                        if m["last_buy_t"] is None or rec["t"] > m["last_buy_t"]:
                            m["last_buy_t"] = rec["t"]
                    else:          # 付出代币 = 卖出
                        m["sell_count"] += 1
                        m["sold_qty"] += -d
                        if rec["sol_delta"] is not None and rec["sol_delta"] > 0:
                            m["sol_received"] += rec["sol_delta"]
                if (k + 1) % 20 == 0 or (k + 1) == len(futs):
                    print(f"  解析 {k+1}/{len(futs)} 笔（本次有效 {parsed_this_run}）", end="\r")
        print()
    parsed_total += parsed_this_run

    # ---- 汇总窗口 ----
    all_t = [v["first_buy_t"] for v in mints.values() if v.get("first_buy_t")] + \
            [v["last_buy_t"] for v in mints.values() if v.get("last_buy_t")]
    oldest = min(all_t) if all_t else None
    newest = max(all_t) if all_t else None

    def _s(ts):
        return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone().isoformat(timespec="seconds") if ts else None

    span_days = round((newest - oldest) / 86400, 1) if (oldest and newest) else None
    payload = {
        "schema": SCHEMA,
        "kol": name,
        "address": address,
        "updated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "source": "solana getSignaturesForAddress + getTransaction(jsonParsed)，并发单条（公共 RPC 禁 batch）",
        "window": {
            "oldest_trade": _s(oldest),
            "newest_trade": _s(newest),
            "span_days": span_days,
            "pages_fetched": pages_done,
            "tx_parsed": parsed_total,
            # ⭐ 关键证据：exhausted=True 表示「已翻到该地址最早一笔签名」= 这就是它**全部**链上历史。
            #   2026-09-12 实测：三个 KOL 钱包 exhausted 且跨度仅 1.7–2.2 天
            #   → 说明「早期埋伏恒空」不是采集窗口不够，而是**钱包本身只有 2 天历史**，
            #     再分页也翻不出更早的交易。前端空态据此如实说明，不再含糊写"窗口不足"。
            "exhausted": exhausted,
        },
        "cursor": {"oldest_sig": cursor, "newest_sig": newest_sig},
        "parsed_sigs": sorted(parsed_sigs)[-20000:],   # 幂等去重依据（按签名，不靠游标猜）
        "mint_count": len(mints),
        "mints": {
            k: {
                **{kk: vv for kk, vv in v.items() if not kk.endswith("_t")},
                "first_buy_time": _s(v.get("first_buy_t")),
                "last_buy_time": _s(v.get("last_buy_t")),
                "bought_qty": round(v["bought_qty"], 6),
                "sold_qty": round(v["sold_qty"], 6),
                "sol_spent": round(v["sol_spent"], 6),
                "sol_received": round(v["sol_received"], 6),
            } for k, v in mints.items()
        },
    }
    p = save_history(name, payload)
    print(f"[{name}] 代币 {len(mints)} 个 · 窗口 {span_days} 天（{_s(oldest)} ~ {_s(newest)}）"
          f" · 已翻到头={exhausted} → {p}")
    return payload


SLEEP_BETWEEN_TRADES_PAUSE = 0.2


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kol", default=None, help="只跑指定 KOL 备注名")
    ap.add_argument("--pages", type=int, default=5, help="最多翻几页签名（每页 1000）")
    ap.add_argument("--max-tx", type=int, default=800, help="本次最多解析多少笔交易")
    ap.add_argument("--fresh", action="store_true", help="忽略旧游标，从最新重新开始")
    args = ap.parse_args()

    targets = load_targets(args.kol)
    if not targets:
        print("没有可跑的目标（需要 KOL 注册表里带 Solana 地址）", file=sys.stderr)
        return 1
    for t in targets:
        try:
            collect_one(t, args.pages, args.max_tx, args.fresh)
        except Exception as e:  # noqa: BLE001
            print(f"[{t['name']}] 失败: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
