"""C18 未验②：**真端点**上的「超时不重发」（花真钱，默认不进全量门禁）。

判据原话（`plan/model-gateway.md` C18 行 ② 的未验边界）：「真端点上的『超时不重发』没花真钱验
（ADR-06 的实测依据是上一棒那发 9.0s 的桩读数）」。桩那半边已经钉过「超时 1 发 / 连接失败 3 发」，
缺的是**真 StepFun**：那里才有「SDK 自己会不会替我们重发」这一格（`max_retries=0` 是 09-24 才钉上的，
钉之前真桩现证 9 发）。

三档，出网请求数在 `httpx.AsyncClient.send` 那一层数（不管谁发起，发出去几次就是几次）：

- **A 被测**：真端点 + `timeout=1`（thinking 模型 1s 内不可能回）⇒ 必然客户端超时。
  判据 = 真端点的出网计数 **恰好 1**。多一发就是 ADR-06 被绕过。
- **B 阳性对照**：同一条 gateway，`base_url` 指死端口 ⇒ 连接类失败，`_acall` 该重发。
  判据 = 计数 **3**（`stop_after_attempt(3)`）。少了这格，A 的「1」可以是计数器坏了。
- **C 成功档**：真端点正常一发（`timeout=90`）。判据 = 计数 1 且拿到非零 usage
  ⇒ 证明计数器在「请求真完成了」时也看得见（B 那三发一个都没连上）。

两道闸装在发钱之前（撞闸印「已花/跑到哪一档」并 exit 3，不许自己抬闸）：
`SEND_GATE`（默认 6 次真端点出网：A 1 + C 1，留 4 次余量）、`COST_GATE`（默认 ¥0.10）。
⚠ A 那一发是本件唯一读不到花费的真发请求：客户端超时 ⇒ 没有 usage 回执，账本记 0，
而厂商**可能**仍按已生成的 token 计费。封顶靠的是发数，不是账本，这条别糊过去。
"""
import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from codeharness.configs.settings import settings  # noqa: E402
from codeharness.provider.cost import CostManager  # noqa: E402
from codeharness.provider.gateway import LLMGateway  # noqa: E402

SEND_GATE = int(os.environ.get("SEND_GATE", "6"))
COST_GATE = float(os.environ.get("COST_GATE", "0.10"))
DEAD = "http://127.0.0.1:1/v1"

sent = []            # 每次真出网记一条 (host, path)


def _egress_module():
    """数出网要挂在哪一层：langchain-openai 1.x 的客户端 mro 是
    `_AsyncHttpxClientWrapper → openai._DefaultAsyncHttpxClient → **httpx2**.AsyncClient`
    ——它用的是 httpx 的 fork，不是环境里那个 `httpx`。09-25 现证：挂 `httpx.AsyncClient.send`
    连「真发成功的一发」都数不到（计数 0），差点把「超时不重发」判成「一发都没发＝当然没重发」。
    """
    import httpx2                                   # noqa: F401  (langchain-openai 的实际 HTTP 层)
    return httpx2


def _install_egress_counter():
    """把计数挂在 `AsyncClient.send`：SDK 的重试、我们的 `_acall` 重发，最后都过这一层。"""
    mod = _egress_module()
    if getattr(mod.AsyncClient, "_ch_counted", False):
        return
    orig = mod.AsyncClient.send

    async def counted(self, request, *a, **k):
        sent.append((request.url.host, request.url.path))
        return await orig(self, request, *a, **k)

    mod.AsyncClient.send = counted
    mod.AsyncClient._ch_counted = True


def _gate(stage, spend):
    """发钱前判闸：真端点出网数 + 账本已花。撞闸就印「停在哪一档」并 exit 3。"""
    live = sum(1 for h, _ in sent if h and "stepfun" in h)
    if live >= SEND_GATE or spend >= COST_GATE:
        print(f"🛑 撞闸于 {stage} 之前：真端点已出网 {live}/{SEND_GATE} 发、账本已花 ¥{spend:.5f}"
              f"/{COST_GATE}——停手，别自己抬闸")
        sys.exit(3)


def _gw(cm, **over):
    cfg = settings.llm.model_copy(update=over)
    return LLMGateway(cfg=cfg, cost_manager=cm)


async def main():
    from openai import APITimeoutError

    cm = CostManager()
    _install_egress_counter()
    rows = []

    # A 真端点 + 5s 超时：期望「抛超时」且「只发一发」。三档都钉 stream=False——被测的是
    # `_acall` 那条重试链，流式分支本就不走它（C18 行未验边界里写明）
    # ⚠ 超时值不取 1s：09-25 实测那一发**根本没出网**（计数 0、`builtins TimeoutError` 来自
    # `wait_for`，请求还没交出去就被取消）——量「不重发」却连发都没发，是空转。所以把请求做长
    # （数到 300 ⇒ thinking 模型必然超 5s），且这一档先要求「至少一发真出去了」再谈重发次数。
    _gate("A", cm.get_costs().cost_cny)
    n0 = len(sent)
    err = None
    t_a = time.time()
    try:
        await _gw(cm, timeout=5, stream=False).ainvoke(
            "从 1 数到 300，每个数字单独一行，不要解释。", tag="c18-timeout")
    except BaseException as exc:                       # noqa: BLE001 —— 形状本身就是被测对象
        err = exc
    a_wall = time.time() - t_a
    a_sent = sum(1 for h, _ in sent[n0:] if h and "stepfun" in h)
    rows.append(("A 真端点超时", a_sent, f"{type(err).__name__}: {str(err)[:60]} · {a_wall:.1f}s"))
    assert isinstance(err, (APITimeoutError, TimeoutError)), \
        f"A 档没等到超时，量不到「不重发」：{type(err).__name__}: {str(err)[:200]}"
    assert a_sent >= 1, (f"A 档的超时发生在**出网之前**（{a_wall:.1f}s，窗口内计数 0）"
                         "⇒ 这一档没量到重试链，读数不作数：把请求做长或把超时调大，别拿 0 当「不重发」")
    assert a_sent == 1, f"A 失效：真端点上超时重发了 {a_sent} 发（ADR-06 要求 1 发）"

    # B 死端口：连接类失败，_acall 该重发满 3 次（证明计数器不是恒 1）
    _gate("B", cm.get_costs().cost_cny)
    n0 = len(sent)
    try:
        await _gw(cm, base_url=DEAD, timeout=8, max_retries=0, stream=False).ainvoke("ping", tag="c18-connfail")
    except BaseException as exc:                       # noqa: BLE001
        err = exc
    b_sent = len(sent[n0:])
    rows.append(("B 死端口对照", b_sent, type(err).__name__))
    assert b_sent == 3, (f"B 失效：连接失败那档只出网 {b_sent} 发（期望 3＝`stop_after_attempt(3)`）"
                         "——这格是 A 的阳性对照，它不成立就说明计数器数不到东西，A 的「1」不算证据")

    # C 真端点正常一发：成功也要只 1 发，且账本要看得见 usage（这是本工装唯一确定花钱的一发）
    _gate("C", cm.get_costs().cost_cny)
    n0 = len(sent)
    msg = await _gw(cm, timeout=90, stream=False).ainvoke("只回两个字：收到", tag="c18-ok")
    c_sent = sum(1 for h, _ in sent[n0:] if h and "stepfun" in h)
    usage = getattr(msg, "usage_metadata", None) or {}
    rows.append(("C 真端点成功", c_sent,
                 f"pt={usage.get('input_tokens')} ct={usage.get('output_tokens')}"))
    assert c_sent == 1, f"C 失效：成功那一发出网 {c_sent} 发 ⇒ 计数器看不见成功的请求，A 不可信"
    assert usage.get("input_tokens", 0) > 0, f"C 失效：真端点没回 usage（{usage}）"

    cs = cm.get_costs()
    for name, n, extra in rows:
        print(f"   {name}: 出网 {n} 发 · {extra}")
    print(f"   账本：pt={cm.total_prompt_tokens} ct={cm.total_completion_tokens} "
          f"cost_cny=¥{cs.cost_cny:.5f}（闸 ¥{COST_GATE}）· 真端点累计出网 "
          f"{sum(1 for h, _ in sent if h and 'stepfun' in h)}/{SEND_GATE} 发")
    print("✅ C18② 真端点读数：超时=1 发不重发、连接失败=3 发、成功=1 发"
          "（A 那一发的真实扣费账本读不到，封顶靠发数）")


if __name__ == "__main__":
    asyncio.run(main())
