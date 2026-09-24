"""C25 未验①的读数：**一整场多角色经典线**在「在途请求被压过厂商上限」的形状下跑不跑得过去。

09-23 那个原始症状是：5 角色经典线跑到 Engineer/QA 阶段整场判 `failed`，error 原文
`RateLimitError: ... current: 6, limit: 5`。C25 修的是「429 进重试判据 + 有界退避」，
但当时只量到**最小请求那一层**（in-flight 6/8 各 6/8 全过），行里就留了这句未验①：
「没拿一整场多角色 SOP 复跑」。本工装补的就是这一格。

形状：一边跑真会话（**生产那副装配 `classic_team` 五角色 + `build_team` 图，真模型真向量**），
一边用一条独立的压腿把在途请求数顶到 4 —— 会话自己的请求再叠上去就过厂商那道 5 的线。
压腿顶到 4 而不是顶到 8：顶过头会把会话自己的请求淹死，量出来的就是另一件事了
（09-25 第一跑现形：顶到 8 时压腿 15 秒打完 600 发、470 次 429，而会话一分钱没花出去）。

为什么走图面而不走 HTTP+runner：runner 那条路第一跑就停在 `awaiting_human`（任务表要人确认），
钱只能靠轮询看（发出去的那一收回不来）。图面能**每一发之前**判闸（`_Gated`），且被量的是同一条
`_acall` 退避链——**没穿的只有 runner 的状态机那一层**，写在下面的边界里。

判据四格（各钉一种坏法）：
  ① **会话侧真被 429 打过**：退避日志行数 > 0，或抛出的是 RateLimitError —— 压腿自己撞了多少次
     都不算（那是「端点被淹」不是「这一场撞上」）；这一格不绿时，②的绿一律不作数；
  ② **整场跑完且有产物**：图走到 StopAsyncIteration、`workspace/<project>` 下有 .py 落盘
     （原始症状是抛穿整场，不是产物少一个）；
  ③ **退避有界**：日志里「第 N 次失败」的最大 N ≤ 3（无界重发是另一个方向的坏）；
  ④ **花费照实**：会话 ¥ + 压腿 ¥ + embedding 真 usage token + 退避行数，三道闸都在工装里
     （会话 `C25_SESSION_GATE_CNY` 默认 ¥0.30、压腿 3000 发/¥0.05、embedding 20,000 token）。

反证一格 `--mutant`：运行时把 `_retryable` 摘成恒 False（＝回到 C25 改前那副形状），同一压法下
**应当被 429 打死**（终态 raised + RateLimit）；若它仍跑完，说明这一场压根没压到会话，
②那一格的绿也就不能算数。摘法是进程内换函数、不改文件 ⇒ 没有「变异没还原」这一族风险，
而①那格会现证它生效（退避行数为 0 而抛的是 RateLimit）。

跑法（真凭据、真花钱；`REDIS__DB=15`）：
  cd /e/Codeharness && PYTHONPATH=/e/Codeharness:/e/Codeharness/logs PYTHONIOENCODING=utf-8 \\
    PYTHONDONTWRITEBYTECODE=1 REDIS__DB=15 LANGFUSE__ENABLED=0 NO_PROXY=127.0.0.1,localhost,::1 \\
    C25_SESSION_GATE_CNY=0.30 F:/anaconda/python.exe -B tests/manual_c25_sop_under_429.py --mutant
  ... 去掉 --mutant 跑原样那一档

**这一份不算门禁**：花真钱、挂真端点，缺席直接 exit 1 说「没跑成」。
**边界（没验到的）**：runner 的状态机那一层（`failed` 是 runner 判的，本工装判的是图抛不抛穿）；
多轮（`n_round>1`）与真人在环的复合形状。跑完删自己的 `workspace/c25_sop_429`，不碰生产集合与 db0。
"""
import argparse
import os
import shutil
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx                                          # noqa: E402
from dotenv import dotenv_values                      # noqa: E402

SESSION_GATE = float(os.environ.get("C25_SESSION_GATE_CNY", "0.30"))
# 在途顶到 **4** 而不是 8：原始症状是「五个角色同时进 `_think`，把在途顶过厂商的 5」。
# 压腿只补到刚过线（4 + 会话自己的那一发发），才是那个形状；顶到 8 会把会话自己的请求淹死，
# 量出来的就不是「退避能不能救一场」，而是「端点被淹死时会话怎么样」（另一件事）。
PRESSURE_INFLIGHT = int(os.environ.get("C25_PRESSURE_INFLIGHT", "4"))
PRESSURE_GAP_S = float(os.environ.get("C25_PRESSURE_GAP_S", "0.4"))
PRESSURE_MAX_CALLS = int(os.environ.get("C25_PRESSURE_MAX_CALLS", "3000"))
PRESSURE_MAX_CNY = float(os.environ.get("C25_PRESSURE_MAX_CNY", "0.05"))
IN_PRICE, OUT_PRICE = 0.7 / 1e6, 2.1 / 1e6            # 元/千 token 口径同 provider/token_costs
PROJECT = "c25_sop_429"                               # 自己的 project，跑完整目录删掉
IDEA = ("做一个命令行 tinycli：只有一个 main.py，提供 --version 打印 0.1.0。"
        "要求极简：不要测试文件、不要 README、不要额外模块。")


def _die(msg: str) -> None:
    sys.exit(f"exit 1：{msg}")


class Pressure:
    """一条独立事件循环的压腿：把在途请求顶到 N，直到会话结束。计量与闸都在自己手里。"""

    def __init__(self, base: str, key: str, model: str, inflight: int):
        self.url, self.key, self.model = base.rstrip("/") + "/chat/completions", key, model
        self.inflight, self.stop = inflight, threading.Event()
        self.calls = self.limited = self.cny = 0
        self.err = ""

    def run(self) -> None:
        import asyncio

        async def worker(client):
            """常驻工人 = 一个在途名额：发一发、等回来、歇一下再发。

            上一版是「一次 gather 八发、打完立刻再来一发」＝连发火蟒（实测 15 秒打完 600 发、
            470 次 429）。那是「淹死端点」，不是 09-23 那个「五角色并发把在途顶过 5」的形状——
            火蟒会把会话自己的第一发也淹掉，跑出来的读数就不属于 C25 那一问了。"""
            while not self.stop.is_set():
                if self.calls >= PRESSURE_MAX_CALLS or self.cny >= PRESSURE_MAX_CNY:
                    self.stop.set()
                    return
                try:
                    r = await client.post(
                        self.url, json={"model": self.model, "max_tokens": 8,
                                        "messages": [{"role": "user", "content": "只回一个字：好"}]},
                        headers={"Authorization": f"Bearer {self.key}"}, timeout=30)
                except Exception as e:                        # 压腿挂了不算全场失败，但要现证
                    self.err = f"{type(e).__name__}: {e}"
                    self.stop.set()
                    return
                self.calls += 1
                if r.status_code == 429:
                    self.limited += 1
                elif r.status_code != 200:
                    self.err = f"HTTP {r.status_code} {r.text[:80]}"
                    self.stop.set()
                    return
                else:
                    u = (r.json().get("usage") or {})
                    self.cny += (u.get("prompt_tokens", 0) * IN_PRICE
                                 + u.get("completion_tokens", 0) * OUT_PRICE)
                await asyncio.sleep(PRESSURE_GAP_S)

        async def loop():
            async with httpx.AsyncClient() as client:
                await asyncio.gather(*[worker(client) for _ in range(self.inflight)])
        try:
            asyncio.run(loop())
        except Exception as e:
            self.err = f"{type(e).__name__}: {e}"


def _backoff_sink():
    """数 gateway 那句「退避后重发」响了几次、最大第几发——判据①③都读它。"""
    from loguru import logger
    hits = {"lines": 0, "max_attempt": 0}

    def grab(message):
        txt = message
        if "退避后重发" in txt:
            hits["lines"] += 1
            import re
            m = re.search(r"第 (\d+) 次失败", txt)
            if m:
                hits["max_attempt"] = max(hits["max_attempt"], int(m.group(1)))
    logger.add(grab, format="{message}", level="WARNING", filter=lambda r: "gateway" in
               str(r["name"]))
    return hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mutant", action="store_true", help="摘掉 429 可重那一档（回到改前形状）")
    args = ap.parse_args()

    env = dotenv_values("E:/Codeharness/.env")
    if not env.get("LLM__API_KEY"):
        _die(".env 里没有 LLM 凭据，本工装不发空请求")
    from codeharness.configs.settings import settings
    if settings.platform.auth_enabled:
        _die("本工装走的是 auth 关的默认档（会话面不带票），先别在 auth 开的配置下跑")
    try:
        assert httpx.get(f"{settings.qdrant.url}/healthz", timeout=3).status_code == 200
    except Exception as e:
        _die(f"Qdrant 不在线（{settings.qdrant.url}）：{type(e).__name__}: {e}")

    if args.mutant:                      # 反证：把 429 从可重那一档摘掉（进程内，不改文件）
        import codeharness.provider.gateway as g
        if not hasattr(g, "_retryable"):
            _die("gateway 里找不到 `_retryable`，这一档的摘法已失效（别照旧文字硬跑）")
        g._retryable = lambda exc: False
        print("  变异：`_retryable` 已摘成恒 False（＝回到 C25 改前那副形状）")

    hits = _backoff_sink()
    p = Pressure(env["LLM__BASE_URL"], env["LLM__API_KEY"], env["LLM__MODEL"], PRESSURE_INFLIGHT)
    threading.Thread(target=p.run, daemon=True).start()
    print(f"  压腿：在途顶到 {PRESSURE_INFLIGHT}（09-24 实测 6 起 1/6 撞、8 起 3/8 撞）｜"
          f"闸 压腿 {PRESSURE_MAX_CALLS} 发 / ¥{PRESSURE_MAX_CNY} ｜会话 ¥{SESSION_GATE}")

    import asyncio

    from codeharness.provider.cost import CostManager
    from codeharness.provider.gateway import LLMGateway
    from codeharness.runtime import CURRENT_PROJECT
    from codeharness.team import classic_team, run_project
    from manual_c24_requery import _Gated
    import manual_c31_auth_e2e as c31                  # 复用那份 embedding 闸，不抄第二份

    c31._install_embedding_gate()    # 向量腿也上一道闸（按端点回的真 usage 计，不是估算）
    bad: list[str] = []
    cm = CostManager()
    gw = _Gated(LLMGateway(cost_manager=cm), SESSION_GATE, "五角色SOP")
    agents = classic_team(gw)        # 生产那副装配（五角色 + 各自的 actions），不另搭一套
    status, err, files_after = "", "", 0

    async def drive():
        tok = CURRENT_PROJECT.set(PROJECT)
        n = 0
        try:
            async for _ in run_project(idea=IDEA, project_id=PROJECT, agents=agents):
                n += 1
        finally:
            CURRENT_PROJECT.reset(tok)
        return n

    try:
        events = asyncio.run(drive())
        status = "completed"
    except SystemExit as e:                       # 撞闸：_Gated 与 embedding 闸都是 sys.exit(3)
        status, events, err = "capped", 0, f"撞金额闸退出（{e}）"
    except BaseException as e:
        events, err, status = 0, f"{type(e).__name__}: {str(e)[:200]}", "raised"
    finally:
        p.stop.set()
        sess_cny = float(cm.get_costs().cost_cny)
        root = Path("workspace") / PROJECT
        files_after = len(list(root.rglob("*.py"))) if root.exists() else 0
        print(f"    产物：workspace/{PROJECT} 下 .py 文件 {files_after} 个")
        shutil.rmtree(root, ignore_errors=True)      # 自起的东西自收（本轮读数已取，产物不必留）

    print(f"\n读数：终态={status}（事件 {events} 条）会话¥{sess_cny:.4f} error={err or '（无）'} ｜ "
          f"压腿 {p.calls} 发 / 自己撞 429 {p.limited} 次 / ¥{p.cny:.4f} "
          f"{('压腿错误 ' + p.err[:90]) if p.err else ''} ｜ "
          f"**会话侧退避 {hits['lines']} 行 / 最大第 {hits['max_attempt']} 发** ｜ "
          f"embedding {c31._emb_calls} 发 / {c31._emb_spent} token（闸 {c31.EMB_GATE:,}）")

    # ①的关键是「会话侧」三个字：压腿自己撞了 470 次而会话一分钱没花＝那一层没碰过 429，
    # 上一版把压腿的计数算进这一格，是一处会让整件作废的假绿（09-25 第一跑现形）。
    sess_hit = hits["lines"] > 0 or "RateLimit" in err or "429" in err
    if not sess_hit:
        bad.append(f"①症状没复现：会话侧一次都没被 429 打过（压腿自己撞的 {p.limited} 次不算）"
                   "⇒ 这一场跑通不能算 C25 未验①的读数")
    if hits["max_attempt"] > 3:
        bad.append(f"③退避没界：最大第 {hits['max_attempt']} 发（应 ≤3）")
    if status == "capped":
        bad.append("②撞会话金额闸停手：这一场不作结论")
    if args.mutant:
        if hits["lines"] > 0:
            bad.append("反证不成立：`_retryable` 摘了还在退避 ⇒ 那一档摘法没生效")
        if status == "raised" and ("RateLimit" in err or "429" in err):
            print("  反证格：摘掉 429 可重后这一场被 429 打死（原始症状复现）"
                  "⇒ 原样档的『跑完』确实是退避换来的")
        elif status == "completed":
            bad.append(f"⑤反证没红：摘掉可重之后仍跑完 ⇒ 会话压根没被 429 打到，"
                       "原样档那一场的 ② 也不能算数")
    else:
        if status != "completed":
            bad.append(f"②整场没救回来：终态 {status}、error={err[:120]}")
        elif files_after == 0:
            bad.append(f"②跑完了但一个 .py 产物都没落盘（workspace/{PROJECT} 是空的）⇒ 这场是空转")
        elif events == 0:
            bad.append("②事件流零条：图根本没走")

    if bad:
        print("判据未绿：" + "；".join(bad))
        return 2
    print("判据：一整场五角色经典线（生产装配 `classic_team`）在「压腿把在途顶到 "
          f"{PRESSURE_INFLIGHT}」的形状下跑到 {status}、产物 {files_after} 个 .py 落盘；"
          f"会话侧退避 {hits['lines']} 行、最大第 {hits['max_attempt']} 发（≤3 有界）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
