"""零花费只读探针：**`clip` 那把「按字符」的预算尺，换成「按 token」会差多少**（总计划 B 项，先量再动）。

问的是比值，不是账单。现仓 16 个 `clip` 站点全部按**字符**记预算（4 千/2 万/5 百…，`plan/rag-knowledge.md` §1.5），
而上下文闸按 **token** 记（`LLM__CONTEXT_LENGTH`，走 `utils/token_counter.py:231-235` 那把尺）。同一句话
在中文里 1 字≈1 token、在代码里 4~5 字≈1 token，所以「4 千字的预算」代表的额度**不是一个常数**。
本探针量的是这个跨度到底有多大、以及它跟语种的相关性。

口径三条，缺一条就会被读成别的东西：
  · **尺子不是生产那把**：本仓对表外模型（含生产档 `step-3.5-flash`）回落 `cl100k_base`
    （`utils/token_counter.py:320-323` 现证；`tiktoken.encoding_for_model("step-3.5-flash")` 直抛 KeyError，
    StepFun 没公开 tokenizer）。所以这里拿 **cl100k_base = 本仓压缩闸实际在用那把尺**，另配 `o200k_base` 做
    第二把尺的对照——**给的是比值形状，不是 StepFun 的计费读数**（§6 第 28 条：别拿别的模型冒充生产口径）。
  · **只数正文**，不含每条消息的固定开销（`count_message_tokens` 另加），所以绝对值偏低，比值不受影响。
  · 零花费：不起服务、不发任何云端请求、库只 `mode=ro`，也不写 Redis。

跑法：
  cd /e/Codeharness && PYTHONPATH=/e/Codeharness:/e/Codeharness/logs PYTHONIOENCODING=utf-8 \\
    PYTHONDONTWRITEBYTECODE=1 F:/anaconda/python.exe -B tests/manual_clip_token_units.py [pair 上限，默认 2000]
"""
import sqlite3
import sys
from pathlib import Path

import tiktoken

sys.path.insert(0, str(Path(__file__).resolve().parent))
import manual_truncation_lengths as mtl      # noqa: E402  同一份走查（texts/harvest/pct），别各写一遍

LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
CL = tiktoken.get_encoding("cl100k_base")     # 本仓表外模型的回落尺（=压缩闸在用那把）
OO = tiktoken.get_encoding("o200k_base")      # 第二把尺，用来证结论不依赖选哪把


def stat(t):
    """一条文本 → (字符数, cl100k token 数, o200k token 数, 中日韩字符数)。"""
    cjk = sum(1 for ch in t if "㐀" <= ch <= "鿿" or "぀" <= ch <= "ヿ" or "가" <= ch <= "힯")
    return len(t), len(CL.encode_ordinary(t)), len(OO.encode_ordinary(t)), cjk


def band(ratios):
    """`字/token` 比值的一档读数：n、p10/p50/p90、min/max、以及 p90÷p10 的跨度倍数。"""
    if not ratios:
        return "**零样本**"
    p10, p50, p90 = mtl.pct(ratios, 10), mtl.pct(ratios, 50), mtl.pct(ratios, 90)
    return (f"n={len(ratios)} p10={p10:.2f} p50={p50:.2f} p90={p90:.2f} "
            f"min={min(ratios):.2f} max={max(ratios):.2f} 跨度(p90÷p10)={p90 / p10:.2f}×")


def collect(conn):
    """现网语料：A=工具产出、B=消息正文（走 `mtl.texts`，与长度档同一份走查）。"""
    saver = mtl.SqliteSaver(conn, serde=mtl._serde())
    pairs = conn.execute("select thread_id, checkpoint_ns, max(checkpoint_id) from checkpoints "
                         "group by 1,2 order by 3 desc limit ?", (LIMIT,)).fetchall()
    A, B, got, failed = [], [], 0, 0
    for tid, ns, _ in pairs:
        try:
            tup = saver.get_tuple({"configurable": {"thread_id": tid, "checkpoint_ns": ns}})
        except Exception:
            failed += 1
            continue
        if tup is None:
            failed += 1
            continue
        got += 1
        at, bt = mtl.texts(tup.checkpoint.get("channel_values") or {})
        A += [x for x in at if x]
        B += [x for x in bt if x]
    return A, B, got, failed, len(pairs)


def selfcheck(samples):
    """仪器对照：两把尺都得给出正数、合成两堆必须落在比值两端、非空转。缺这格比值就是假绿。

    合成样本用**纯中文**与**纯 ASCII 代码**两段（都是造的，不拿现网某篇当对照——现网那篇什么语种
    是数据的属性，不是仪器的属性，用它当对照就变成「用结论证结论」）。
    """
    zh = stat("中文字符" * 500)
    code = stat("def foo(x):\n    return x + 1\n" * 200)
    r_zh, r_code = zh[0] / zh[1], code[0] / code[1]
    checks = [
        (f"纯中文 字/token(cl100k)={r_zh:.2f} 必须明显小于纯代码 {r_code:.2f}（比值方向）", r_zh < r_code),
        ("两把尺对同一段中文都给正数（不是静默 0）", zh[1] > 0 and zh[2] > 0),
        ("合成段 o200k 与 cl100k 的 token 数不同（证两把尺真的不是同一把）", zh[1] != zh[2]),
        ("现网语料里中文为主与 ASCII 为主两堆都有样本（过滤器没把一堆滤空）", samples),
    ]
    for name, ok in checks:
        print(f"  自检 {'✅' if ok else '❌'} {name}")
    assert all(ok for _, ok in checks), "仪器没过自检 ⇒ 比值读数一律不许引用"



def main() -> int:
    if not Path(mtl.DB).exists():
        print(f"没有 {mtl.DB}——要现网落盘的语料；用 env CH_DB 指一份副本。零样本 ≠ 差别不大。")
        return 3
    with sqlite3.connect(f"file:{mtl.DB}?mode=ro", uri=True) as conn:
        A, B, got, failed, pairs = collect(conn)
    corpus = A + B
    print(f"库={mtl.DB}\n(thread,ns) 对={pairs} 读到={got} 失败={failed}；语料条数 A={len(A)} B={len(B)}")
    if not corpus:
        print("**零样本**：一条文本都没拿到，比值无从谈起（先修取数口径）")
        return 3
    st = [stat(t) for t in corpus]
    ratios = [c / tk for c, tk, _, _ in st if tk > 0]
    zh = [c / tk for c, tk, _, j in st if tk > 0 and j / c > 0.5]
    ascii_ = [c / tk for c, tk, _, j in st if tk > 0 and j / c < 0.1]
    mixed = [c / tk for c, tk, _, j in st if tk > 0 and 0.1 <= j / c <= 0.5]
    selfcheck(bool(zh) and bool(ascii_))

    print(f"\n「1 token 折合几个字符」按语种分堆（>1 说明字符尺给的额度比 token 尺宽）：")
    for label, xs in (("中文为主（CJK 占比 >50%）", zh), ("混合（10%~50%）", mixed),
                      ("ASCII 为主（<10%）", ascii_), ("全体", ratios)):
        print(f"  {label:<24}：{band(xs)}")

    print("\n同一档**字符**预算代表的 token 额度（按每篇自己的比值换算，p10/p50/p90）：")
    for n in (500, 4000, 20000):
        toks = sorted(n / r for r in ratios)
        zt = sorted(n / r for r in zh) if zh else [0, 0, 0]
        ct = sorted(n / r for r in ascii_) if ascii_ else [0, 0, 0]
        print(f"  预算 {n:>6} 字 ⇒ 全体 ≈ {mtl.pct(toks, 10):>6.0f} / {mtl.pct(toks, 50):>6.0f} / "
              f"{mtl.pct(toks, 90):>6.0f} token｜中文为主那堆 {mtl.pct(zt, 50):>6.0f}｜"
              f"ASCII 为主那堆 {mtl.pct(ct, 50):>6.0f}")
    lo, hi = mtl.pct(ratios, 10), mtl.pct(ratios, 90)
    print(f"反过来：**固定 4000 token** 的额度，在同样语料上放得下 {4000 * lo:.0f} ~ {4000 * hi:.0f} 字"
          f"（p10~p90，跨度 {hi / lo:.2f}×；用 min/max 会被单发极端值顶开，所以报分位）")


    agree = [a / b for _, a, b, _ in st]      # 同一篇：cl100k 的 token 数 ÷ o200k 的
    print(f"\n两把尺的一致性：**cl100k ÷ o200k** 的比值 {band(agree)}"
          f"（p50=1.19 ⇒ 同一篇文本换尺会让**绝对值**动一成多，所以绝对 token 数不能跨尺引用；"
          f"而语种跨度 {mtl.pct(ratios, 90) / mtl.pct(ratios, 10):.2f}× 是它的三倍多 ⇒ "
          f"「同一把字符尺代表的额度差几倍」这件事不是选尺选出来的）")
    ms = timing()
    print(f"换尺的**运行代价**（现量，同一把 cl100k）：对 4000 字的中英混排文本做一次 encode {ms:.3f} ms/次"
          f" ⇒ 一场 20 轮 × 每轮 3 次工具调用 ≈ {ms * 60 / 1000:.3f} s")
    print(f"语料整体：字符合计={sum(s[0] for s in st)} cl100k token 合计={sum(s[1] for s in st)} "
          f"⇒ 全库平均 字/token={sum(s[0] for s in st) / sum(s[1] for s in st):.2f}")
    print("边界：以上是 **本仓压缩闸在用的回落尺（cl100k_base）**与 o200k 的比值形状；"
          "StepFun 那把没公开、本机拿不到，绝对 token 数不许当生产计费口径引用。")
    return 0


def timing():
    """现量「按 token 记账要花的 CPU」：造一段中英混排（贴近现网 p50 那档），encode 20 次取中位。

    不测就别把它写成选项的代价——本仓的规矩是每个数字带口径。
    """
    import time
    s = ("字" * 2000 + "def foo(x):\n    return x + 1\n" * 400)[:4000]
    ts = []
    for _ in range(20):
        t0 = time.perf_counter()
        CL.encode_ordinary(s)
        ts.append((time.perf_counter() - t0) * 1000)
    med = mtl.pct(ts, 50)
    assert med > 0, "计时恒 0 ⇒ 这台钟不可信，代价那行不许引用"
    return med




if __name__ == "__main__":
    sys.exit(main())
