"""零花费只读探针：真历史负载里「进上下文的正文」到底多长——即 `clip` 的标记会不会真出现。

为什么要有这支：R7（`plan/rag-knowledge.md` §1.5）把 16 处按字符截断换成带标记的共享件，判据证的是
**文本形状**；「这些预算在真负载上究竟超不超得过」是另一件事，只能读现网落盘的 state。09-29 现取读数
（333 个 thread / 2109 条消息正文）：min 0 / p50 19 / p90 274 / p99 2673 / max 22889，且
**九个预算档各有样本越过**（500 档 2.09%、2000 档 1.42%、20000 档 0.09%）⇒ 标记不是摆设。

三条姿势都是本仓踩过的，别改：
  · **只读**：`mode=ro` + `uri=True`；绝不走 `environment/checkpoint.py::sqlite_saver` 那工厂，
    因为它对库执行 `PRAGMA journal_mode=WAL`——那是对共享 dev 库（232 MB 量级）的写动作；
  · 反序列化必须用**产品自己的 serde**（`_serde()`，带 msgpack 白名单）。拿默认 serde 会解不出自定义
    类，然后被统计成「没有样本」——那是把取数故障读成结论（本仓「读到全零先怀疑自己」那条）；
  · 逐 thread try/except 并**计数**，失败不许静默跳过；样本为零就印「零样本」，不印「不超长」。

跑法（零花费、不起服务、不碰 Redis）：
  cd /e/Codeharness && PYTHONPATH=/e/Codeharness:/e/Codeharness/logs PYTHONIOENCODING=utf-8 \
    F:/anaconda/python.exe -B tests/manual_truncation_lengths.py [thread 上限，默认 500]
库路径默认 `<仓>/workspace/storage/checkpoints.db`，要量别的副本用 env `CH_DB`。
"""
import os
import sqlite3
import statistics
import sys
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from codeharness.environment.checkpoint import _serde

REPO = Path(__file__).resolve().parent.parent
DB = os.environ.get("CH_DB") or str(REPO / "workspace/storage/checkpoints.db")
LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 500
# 16 个站点真实用到的预算档位（tools 2万/1万/8千、sandbox 2万、run_code 5百/1万/3千、role_zero 4千、
# agent 2千/5百、plan_and_act 2千/6千、data_analysis 4千、debug_error 4千）
BUDGETS = (500, 1000, 2000, 3000, 4000, 6000, 8000, 10000, 20000)


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))] if xs else 0


def harvest(vals):
    """取两类文本长度：A=工具产出（history[].results[].result），B=消息正文。认不准的形状一律跳过。"""
    a, b = [], []
    hist = vals.get("history")
    if isinstance(hist, list):
        for step in hist:
            res = step.get("results") if isinstance(step, dict) else None
            for r in (res if isinstance(res, list) else []):
                if isinstance(r, dict) and isinstance(r.get("result"), str):
                    a.append(len(r["result"]))
    msgs = vals.get("messages")
    for m in (msgs if isinstance(msgs, (list, tuple)) else []):
        content = getattr(m, "content", m.get("content") if isinstance(m, dict) else None)
        if isinstance(content, str):
            b.append(len(content))
        elif isinstance(content, list):                       # 分段 content：只数文本段
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    b.append(len(part["text"]))
    return a, b


def report(label, xs):
    if not xs:
        print(f"  {label}: **零样本**——不能说「不超长」，只能说没量到（换 A 档结论前先修取数口径）")
        return
    print(f"  {label}: n={len(xs)} min={min(xs)} p50={pct(xs, 50)} p90={pct(xs, 90)} "
          f"p99={pct(xs, 99)} max={max(xs)} 均值={round(statistics.mean(xs))}")
    hit = [(n, sum(1 for x in xs if x > n)) for n in BUDGETS]
    for n, over in hit:
        if over:
            print(f"      预算 {n:>6}：超的有 {over:>5} 条（{round(100 * over / len(xs), 2)}%）⇒ 这档会真挂标记")
    if not any(o for _, o in hit):
        print(f"      九档预算（{BUDGETS[0]}…{BUDGETS[-1]}）一条都没超 ⇒ 本批样本里标记一次也不会出现")


def main() -> int:
    if not Path(DB).exists():
        print(f"没有 {DB}——本探针要现网落盘的 state；用 env CH_DB 指一份副本。零样本 ≠ 不超长。")
        return 3
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, check_same_thread=False)
    ids = [t for (t,) in conn.execute(
        "select thread_id from checkpoints group by thread_id order by max(checkpoint_id) desc limit ?",
        (LIMIT,))]
    print(f"库={DB}\n取样 thread={len(ids)}（按最新 checkpoint 排序，只读）")
    saver = SqliteSaver(conn, serde=_serde())
    A, B, got, failed, empty = [], [], 0, 0, 0
    for tid in ids:
        try:
            tup = saver.get_tuple({"configurable": {"thread_id": tid}})
        except Exception as e:
            failed += 1
            if failed <= 3:
                print(f"  解不出（{type(e).__name__}: {str(e)[:90]}）thread={tid[:40]}")
            continue
        if tup is None:
            failed += 1
            continue
        got += 1
        a, b = harvest(tup.checkpoint.get("channel_values") or {})
        if not (a or b):
            empty += 1
        A += a
        B += b
    print(f"读到={got} 解出失败={failed} 无可测文本的 thread={empty}")
    report("A 工具产出（role_zero:473 的 4 千、run_code 的 5 百/3 千/1 万同源）", A)
    report("B 进上下文的正文（tools 的 2 万/1 万/8 千、agent 的 2 千/5 百、plan_and_act 的 6 千同源）", B)
    return 0


if __name__ == "__main__":
    main()
