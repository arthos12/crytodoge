# -*- coding: utf-8 -*-
"""CryptoDog 采集器统一启动器（给 cron 用，也可手动跑）

跑两件事：
  1. collectors/futures_market.py   合约行情（全网 OI / 资金费率 / 标记价；约 20-30s）
  2. collectors/futures_traders.py  带单交易员 + 持仓（Hyperliquid 公开数据）

设计要点（踩坑预防）：
  · 顺序执行，不并发 —— 两个采集器都打币安/交易所公开接口，并发易触发限流
  · 每个子进程 stdout/stderr 落 cryptodog/data/collector.log（cron 下无终端）
  · 子进程 env 里 pop NO_PROXY —— 否则 Clash TUN 下 requests 走直连被限流（项目既有坑）
  · 退出码：全部成功 0；任一失败 1（cron 可据此告警）
  · 末尾打印**验证信息**（数据文件时间戳 + 关键统计），便于 cron 日志里一眼确认

cron 用法（Windows 计划任务或 Hermes cron 均可）：
  D:/project/.venv/Scripts/python.exe D:/project/cryptodog/run_collectors.py
  建议频率：合约行情每 15 分钟（缓存 TTL 5 分钟，15 分钟足够新鲜）；带单数据每 1 小时
  参数：--only market|traders  只跑其中一个
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path("D:/project/cryptodog")
DATA = ROOT / "data"
LOG = DATA / "collector.log"
PY = sys.executable

# 控制台可能是 GBK（Windows 默认）→ 打印 emoji/中文会 UnicodeEncodeError 崩掉整个采集
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

JOBS = {
    "market": ROOT / "collectors" / "futures_market.py",
    "traders": ROOT / "collectors" / "futures_traders.py",
}


def log(msg: str) -> None:
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line)
    DATA.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def run_one(name: str) -> bool:
    script = JOBS[name]
    if not script.exists():
        log(f"{name}: 脚本不存在 {script}")
        return False
    env = dict(os.environ)
    env.pop("NO_PROXY", None)          # 项目既有坑：NO_PROXY 继承会让请求绕过代理被限流
    env.pop("no_proxy", None)
    t0 = time.time()
    log(f"{name}: 开始 {script.name}")
    try:
        proc = subprocess.run([PY, str(script)], cwd=str(ROOT), env=env,
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=600)
    except subprocess.TimeoutExpired:
        log(f"{name}: ❌ 超时 600s")
        return False
    dt = round(time.time() - t0, 1)
    tail = (proc.stdout or "").strip().splitlines()[-3:]
    if proc.returncode == 0:
        log(f"{name}: ✅ 完成 {dt}s | " + " / ".join(x.strip() for x in tail))
        return True
    log(f"{name}: ❌ 失败 exit={proc.returncode} {dt}s | {(proc.stderr or '')[-300:]}")
    return False


def verify() -> dict:
    """打印验证信息：数据文件是否新鲜 + 关键统计"""
    out = {}
    for fname, label in (("futures_market.json", "合约行情"), ("futures_traders.json", "带单交易员")):
        p = DATA / fname
        if not p.exists():
            out[label] = "缺失"
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            out[label] = f"读取失败 {e}"
            continue
        stats = d.get("stats") or {}
        age = None
        try:
            age = round((datetime.now().astimezone()
                         - datetime.fromisoformat(d["updated_at"])).total_seconds())
        except Exception:
            pass
        bits = [f"updated_at={d.get('updated_at')}"]
        if age is not None:
            bits.append(f"age={age}s" + ("（偏旧）" if age > 3600 else ""))
        for k in ("trader_count", "position_count", "liq_warn_count", "funding_abnormal_count",
                  "pool_oi_usd", "top_symbols"):
            if k in stats:
                bits.append(f"{k}={stats[k]}")
        out[label] = " ".join(str(b) for b in bits)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="CryptoDog 采集器启动器")
    ap.add_argument("--only", choices=sorted(JOBS), help="只跑其中一个采集器")
    args = ap.parse_args()

    names = [args.only] if args.only else ["market", "traders"]
    log(f"=== 开始采集 {names} ===")
    ok_all = True
    for n in names:
        ok_all &= run_one(n)

    v = verify()
    for label, info in v.items():
        log(f"验证 · {label}: {info}")
    log(f"=== 采集结束 {'全部成功' if ok_all else '存在失败'} ===")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
