"""零花费只读探针：真历史负载里「进上下文的正文」到底多长——即 `clip` 的标记会不会真出现。

为什么要有这支：R7（`plan/rag-knowledge.md` §1.5）把 16 处按字符截断换成带标记的共享件，判据证的是
**文本形状**；「这些预算在真负载上究竟超不超得过」是另一件事，只能读现网落盘的 state。

09-29 现取读数（1484 个 `(thread, ns)` 对；读到=1484 / 解出失败=0 / 无可测文本=856）：
  · **B 进上下文的正文** n=2118：`min 0 / p50 19 / p90 274 / p99 2673 / max 22889`，**九档预算各有样本越过**
    （500 档 44 条 = 2.08%、2000 档 30 条 = 1.42%、20000 档 2 条 = 0.09%）⇒ 标记在现网真会出现，不是纸上新增噪声；
  · **A 工具产出** n=510：`p50 4 / p90 252 / p99 3059 / max 4000`，**恰好 = 4000 的 3 条**、超 4000 的 0 条。
    ⚠ **R7 之前落盘的那一档被旧截断 censored**：落盘的 `history[].results[].result` 存的本来就是切过的值
    （旧代码 `str(out)[:4000]` 把上限做成了硬顶），所以「超 4000 = 0 条」**读不出**「工具产出不超 4000」；
    那一版立得住的只有 **3 条打满 4000**（下界，不是频率），并写着「要量原始分布得现跑一场在 clip 之前埋计数」。

**第二版（09-29 本轮）改的就是这条界**：R7 之后**不必现跑也能回收原长**——`clip` 的标记结尾写着
`…[已截断，原长 L 字]`，`raw_len()` 据此把 A 分成三档（`marked` 给真值 / `legacy` 恰=预算且无标记、只给下界 /
`plain` 落盘即原值）。env `CH_SINCE=<ISO 时刻>` 只取「**整场**最早一条 checkpoint 晚于该时刻」的 thread ⇒
那一档里的 A 全写在 R7 之后，报出来的就是**原始**分布。R7 落仓 = 主仓 `b098ed7`（09-29 03:03:34 +0800）。

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
import re
import sqlite3
import statistics
import sys
from datetime import datetime
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from codeharness.environment.checkpoint import _serde
from codeharness.utils.text import clip        # 自检里拿真产品字符串造标记，不自己拼（自己拼＝测自己的措辞）

REPO = Path(__file__).resolve().parent.parent
DB = os.environ.get("CH_DB") or str(REPO / "workspace/storage/checkpoints.db")
LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 500
# 16 个站点真实用到的预算档位（tools 2万/1万/8千、sandbox 2万、run_code 5百/1万/3千、role_zero 4千、
# agent 2千/5百、plan_and_act 2千/6千、data_analysis 4千、debug_error 4千）
BUDGETS = (500, 1000, 2000, 3000, 4000, 6000, 8000, 10000, 20000)
# `clip` 的标记（`utils/text.py:147`）：`…[已截断，原长 L 字]`，永远挂在**结尾**。原长就写在标记里，
# 所以 R7 之后落盘的值不必现跑也能回收原始长度——本探针第二版改的就是这件事。
MARK = re.compile("…\\[已截断，原长 (\\d+) 字\\]$")
UUID_EPOCH_100NS = 0x01B21DD213814000     # 1582-10-15 → 1970-01-01 的 100ns 间隔


def raw_len(v):
    """一条落盘的工具产出 → `(原始长度, 类别)`；类别三档，各自的可信范围写在下面。

    · `marked`：结尾带 `clip` 的标记 ⇒ 原长是标记里的数（**R7 之后的真值**）；
    · `legacy`：无标记、且长度恰为某一档预算 ⇒ 疑似 R7 之前 `[:n]` 切的（那种切法不留痕），
      此时只能报「原长 ≥ n」的**下界**，不许当频率用；
    · `plain`：无标记也不是恰等预算 ⇒ 落盘值就是原值。

    假阳性守卫：标记里的数必须**真大于**当前长度。读到的一份正文自己写着这句话（例如它就在讲
    `clip`），按标记算就会把 30 字读成 9 字的截断——那种串走 `plain`。
    """
    m = MARK.search(v)
    if m:
        n = int(m.group(1))
        if n > len(v):
            return n, "marked"
    return len(v), ("legacy" if len(v) in BUDGETS else "plain")


def cutoff_id(iso):
    """把时刻换成 `checkpoint_id` 的下界串（langgraph 自带 uuid6 的反推，见 `id.py:89-105`）。

    布局：高 48 位 = time_low(32)<<16 | time_mid(16)，低 12 位 = time_hi 去掉版本位；十六进制串定宽，
    所以字典序＝时间序，SQL 里可以直接 `>=` 比。自检拿它做往返（换出去再换回来必须等于输入时刻）。
    """
    t100 = int(datetime.fromisoformat(iso).timestamp() * 1e7) + UUID_EPOCH_100NS
    hi48 = (t100 >> 12) & 0xFFFFFFFFFFFF
    return "%08x-%04x-6%03x-8000-000000000000" % ((hi48 >> 16) & 0xFFFFFFFF, hi48 & 0xFFFF, t100 & 0xFFF)


def id_time(cid):
    """`checkpoint_id` → 本地时刻（`cutoff_id` 的逆，自检用它证往返）。"""
    h = cid.replace("-", "")
    t100 = (int(h[:12], 16) << 12) | (int(h[12:15], 16) & 0xFFF)
    return datetime.fromtimestamp((t100 - UUID_EPOCH_100NS) / 1e7).astimezone()


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))] if xs else 0


def harvest(vals):
    """取两类文本长度：A=工具产出（history[].results[].result），B=消息正文。认不准的形状一律跳过。

    A 这一档另走 `raw_len`：返回的是**原始**长度（标记里的原长 / 落盘值本身），同时把三档类别数出来，
    这样「legacy 下界」不会混在真值里冒充频率。
    """
    a, b, cats = [], [], {"marked": 0, "legacy": 0, "plain": 0}
    hist = vals.get("history")
    if isinstance(hist, list):
        for step in hist:
            res = step.get("results") if isinstance(step, dict) else None
            for r in (res if isinstance(res, list) else []):
                if isinstance(r, dict) and isinstance(r.get("result"), str):
                    n, kind = raw_len(r["result"])
                    a.append(n)
                    cats[kind] += 1
    msgs = vals.get("messages")
    for m in (msgs if isinstance(msgs, (list, tuple)) else []):
        content = getattr(m, "content", m.get("content") if isinstance(m, dict) else None)
        if isinstance(content, str):
            b.append(len(content))
        elif isinstance(content, list):                       # 分段 content：只数文本段
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    b.append(len(part["text"]))
    return a, b, cats


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


def selfcheck(conn, iso):
    """尺子自己得先被证明能读：分类四档 + 时刻↔id 往返 + 队列过滤非空转，缺一条就不许报数。

    `marked` 那格用**产品自己的 `clip`** 造标记——手工拼字符串只能证明我在解析我拼的东西。
    过滤那格要求给定的界确实落在库的 min/max 之间，且筛出的 thread 数既不是 0 也不是全部：
    两头都收不住就是「恒绿的空过滤器」（本仓「数到 0 最像结论」那条）。
    """
    ok = []
    v = clip("字" * 5000, 4000)
    ok.append(("marked 用真 clip 造，原长读回 5000 且落盘恰 4000",
               raw_len(v) == (5000, "marked") and len(v) == 4000))
    ok.append(("plain：短值原长=落盘长", raw_len("一句话结论") == (5, "plain")))
    ok.append(("legacy：无标记且恰=某档预算 ⇒ 标下界", raw_len("字" * 4000) == (4000, "legacy")))
    quoted = "正文里引用了这句话 …[已截断，原长 9 字]"
    ok.append(("守卫：引用标记的串按 plain 算（数到 9 就错了）", raw_len(quoted)[1] == "plain"))
    cid = cutoff_id(iso)
    ok.append(("cutoff_id ↔ id_time 往返（1 秒内）", abs((id_time(cid) - datetime.fromisoformat(iso)).total_seconds()) < 1))
    lo, hi, total = conn.execute(
        "select min(checkpoint_id), max(checkpoint_id), count(distinct thread_id) from checkpoints").fetchone()
    n_cohort = conn.execute(
        "select count(*) from (select thread_id from checkpoints group by thread_id "
        "having min(checkpoint_id) >= ?)", (cid,)).fetchone()[0]
    ok.append((f"队列过滤非空转：界落在 {lo[:8]}…{hi[:8]} 之间且 0<{n_cohort}<{total}",
               bool(lo and hi) and lo < cid < hi and 0 < n_cohort < total))
    for name, passed in ok:
        print(f"  自检 {'✅' if passed else '❌'} {name}")
    assert all(p for _, p in ok), "尺子没通过自检 ⇒ 下面任何读数都不许引用"
    return cid


def main() -> int:
    if not Path(DB).exists():
        print(f"没有 {DB}——本探针要现网落盘的 state；用 env CH_DB 指一份副本。零样本 ≠ 不超长。")
        return 3
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, check_same_thread=False)
    iso = os.environ.get("CH_SINCE", "")
    if iso:
        print(f"自检（分类器 + 时刻过滤）：")
        cid0 = selfcheck(conn, iso)
    else:
        cid0 = ""
        print("未给 CH_SINCE ⇒ 全库口径（含 R7 之前的场次，那一档的 A 只能报下界）")
    # **口径（09-29 现证，别再退回只读 ns=""）**：角色子图的 `history`（工具产出就住在这里）落在
    # `checkpoint_ns = "<角色名>:<task uuid>"` 名下（现网分布 PM:/Mike:/Engineer:/QA:/PMManager:/Architect:），
    # 而外层 TeamState 那份 ns="" 里根本没有 history。第一版只取 thread 的 ns="" ⇒ A 档量出**假零样本**。
    pairs = conn.execute(
        "select thread_id, checkpoint_ns, max(checkpoint_id) from checkpoints group by 1,2 "
        "order by max(checkpoint_id) desc limit ?", (LIMIT,)).fetchall()
    if cid0:
        # 队列按**整场**划：一个 thread 的**最早**那条 checkpoint 晚于界，才敢说它所有的 history 都写于 R7 后
        # （否则旧场次 resume 一下就会被算进「post-R7 样本」，里面还躺着 [:4000] 的遗留值）。
        cohort = {t for (t,) in conn.execute(
            "select thread_id from checkpoints group by thread_id having min(checkpoint_id) >= ?", (cid0,))}
        before = len(pairs)
        pairs = [p for p in pairs if p[0] in cohort]
        print(f"CH_SINCE={iso} ⇒ 整场晚于它的 thread={len(cohort)} 个；(thread, ns) 对 {before} → {len(pairs)}")
    print(f"库={DB}\n取样 (thread, ns) 对={len(pairs)}（按最新 checkpoint 排序，只读）")
    saver = SqliteSaver(conn, serde=_serde())
    A, B, got, failed, empty = [], [], 0, 0, 0
    cats = {"marked": 0, "legacy": 0, "plain": 0}
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
        a, b, c = harvest(tup.checkpoint.get("channel_values") or {})
        for k in cats:
            cats[k] += c[k]
        if not (a or b):
            empty += 1
        A += a
        B += b
    print(f"读到={got} 解出失败={failed} 无可测文本的对={empty}")
    print(f"A 分档计数：marked(标记给出原长)={cats['marked']} "
          f"legacy(恰=预算且无标记，只知 ≥)={cats['legacy']} plain(落盘即原值)={cats['plain']}")
    report("A 工具产出·**原始**长度（role_zero 的 4 千、run_code 的 5 百/3 千/1 万同源）", A)
    report("B 进上下文的正文（tools 的 2 万/1 万/8 千、agent 的 2 千/5 百、plan_and_act 的 6 千同源）", B)
    return 0



if __name__ == "__main__":
    main()
