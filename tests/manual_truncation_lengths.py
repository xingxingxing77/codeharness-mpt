"""零花费只读探针：真历史负载里「进上下文的正文」到底多长——即 `clip` 的标记会不会真出现。

为什么要有这支：R7（`plan/rag-knowledge.md` §1.5）把 16 处按字符截断换成带标记的共享件，判据证的是
**文本形状**；「这些预算在真负载上究竟超不超得过」是另一件事，只能读现网落盘的 state。

09-29 现取读数（1484 个 `(thread, ns)` 对；读到=1484 / 解出失败=0 / 无可测文本=856）：
  · **B 进上下文的正文** n=2118：`min 0 / p50 19 / p90 274 / p99 2673 / max 22889`，**九档预算各有样本越过**
    （500 档 44 条 = 2.08%、2000 档 30 条 = 1.42%、20000 档 2 条 = 0.09%）⇒ 标记在现网真会出现，不是纸上新增噪声；
  · **A 工具产出** n=510：`p50 4 / p90 252 / p99 3059 / max 4000`，**恰好 = 4000 的 3 条**、超 4000 的 0 条。
    ⚠ **这一档被旧截断 censored**：落盘的 `history[].results[].result` 存的本来就是切过的值（旧代码
    `str(out)[:4000]` 把上限做成了硬顶），所以「超 4000 = 0 条」**读不出**「工具产出不超 4000」；立得住的只有那
    **3 条打满 4000**——原始产出确实更长，否则切不出正好 4000 个字。要量**原始**分布得在 clip 之前拦（现跑一场
    带计数），本探针量不到，别拿它当结论。

四条姿势都是本仓踩过的，别改：
  · **口径（第一版就是错在这条上）**：角色子图的 `history` 住在 `checkpoint_ns="<角色名>:<uuid>"` 名下
    （现网前缀 PM:/Mike:/Engineer:/QA:/PMManager:/Architect:），外层 TeamState 那份 `ns=""` 里没有 ⇒
    必须按 `(thread_id, checkpoint_ns)` 逐对取最新 checkpoint；只读 `ns=""` 会得到 A 档「零样本」的**假结论**；
  · **只读**：`mode=ro` + `uri=True`；绝不走 `environment/checkpoint.py::sqlite_saver` 那工厂，
    因为它对库执行 `PRAGMA journal_mode=WAL`——那是对共享 dev 库（232 MB 量级）的写动作；
  · 反序列化必须用**产品自己的 serde**（`_serde()`，带 msgpack 白名单）。拿默认 serde 会解不出自定义
    类，然后被统计成「没有样本」——那是把取数故障读成结论（本仓「读到全零先怀疑自己」那条）；
  · 逐 pair try/except 并**计数**，失败不许静默跳过；样本为零就印「零样本」，不印「不超长」。

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
    for n in BUDGETS:
        over = sum(1 for x in xs if x > n)
        exact = sum(1 for x in xs if x == n)
        if over or exact:
            print(f"      预算 {n:>6}：超 {over:>5} 条（{round(100 * over / len(xs), 2)}%）"
                  f" / **恰={n} 的 {exact} 条**（打满＝这一档在现网确实被切过）")
    if not any(sum(1 for x in xs if x > n) for n in BUDGETS):
        print(f"      九档预算（{BUDGETS[0]}…{BUDGETS[-1]}）没有样本越过")


def main() -> int:
    if not Path(DB).exists():
        print(f"没有 {DB}——本探针要现网落盘的 state；用 env CH_DB 指一份副本。零样本 ≠ 不超长。")
        return 3
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, check_same_thread=False)
    # **口径（09-29 现证，别再退回只读 ns=""）**：角色子图的 `history`（工具产出就住在这里）落在
    # `checkpoint_ns = "<角色名>:<task uuid>"` 名下（现网分布 PM:/Mike:/Engineer:/QA:/PMManager:/Architect:），
    # 而外层 TeamState 那份 ns="" 里根本没有 history。第一版只取 thread 的 ns="" ⇒ A 档量出**假零样本**。
    pairs = conn.execute(
        "select thread_id, checkpoint_ns, max(checkpoint_id) from checkpoints group by 1,2 "
        "order by max(checkpoint_id) desc limit ?", (LIMIT,)).fetchall()
    print(f"库={DB}\n取样 (thread, ns) 对={len(pairs)}（按最新 checkpoint 排序，只读）")
    saver = SqliteSaver(conn, serde=_serde())
    A, B, got, failed, empty = [], [], 0, 0, 0
    for tid, ns, _cid in pairs:
        try:
            tup = saver.get_tuple({"configurable": {"thread_id": tid, "checkpoint_ns": ns}})
        except Exception as e:
            failed += 1
            if failed <= 3:
                print(f"  解不出（{type(e).__name__}: {str(e)[:90]}）thread={tid[:32]} ns={ns[:28]}")
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
    print(f"读到={got} 解出失败={failed} 无可测文本的对={empty}")
    report("A 工具产出（role_zero:473 的 4 千、run_code 的 5 百/3 千/1 万同源）", A)
    report("B 进上下文的正文（tools 的 2 万/1 万/8 千、agent 的 2 千/5 百、plan_and_act 的 6 千同源）", B)
    return 0


if __name__ == "__main__":
    main()
