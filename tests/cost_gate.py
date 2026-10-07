"""C176：有界真模型跑的金额闸——判在**记账那一刻**，不判在轮询里。

事故（10-05 02:4x，账在 `plan/model-gateway.md` §1.7 第五棒）：授权 ≤¥0.2 的有界活体实花
**¥0.356093**。闸写在外部轮询里读 `runner.costs[sid]`，而账本的累计只在**每一发结束**时经
`CostManager.update_cost` 增长 ⇒ 轮询永远慢一整发；StepFun 是 thinking 模型，单发就能烧掉
一角钱（`settings.llm.max_token` 经 `gateway.py` 传成 `max_tokens`，对它不封顶——ct 含内部迭代，
这条与 C25② 那句「账面能显出几百元」同源）。轮询第一次读到非零时钱已经花完。所以两件：

① **撞线判定挪到累加的那一刻**。`update_cost` 是全仓唯一让 `cost_usd/cost_cny` 变大的地方，
   网关两条腿都汇到它（`gateway._call` 经 `add_usage`，B8 的 `ChatCompletion` 那一支直接调它），
   所以判它而不是判 `add_usage`——挂在 `add_usage` 上会漏掉后一条腿。撞线即抛：这一发的钱拦不回，
   但**下一发发不出去**。抛的类型刻意是 `BaseException` 的后代：生产的兜底重问住在
   `except Exception:` 里（`roles/role_zero.py::_think` 的 aask 兜底、`roles/agent.py` 的 C76 兜底），
   普通异常会被咽掉并**立刻再花一发**，那正是要拦的东西（判据 t19② 拿真形状钉这条，带阳性对照）。
② **发前先估**：同形状的历史实花（`HISTORY`，每条指得回仓内台账那一行）超过闸就不发；
   **没有记录＝估不动＝不发**。闸读的是哪一档币种也要对上模型的计价币种——对不上就永远撞不了线。

⚠ 闸住在**工装层**，不住产品内核：§7 ⛔「预算强制全家」在册，`tests/s2_gateway.py::t11` 那把反回潮尺
盯着 `CostManager` 上的 `max_budget`/`check_budget` 一类符号。本件产品字段一个没加，
`armed()` 是「装上去 → 用 → `finally` 里摘下来」的类级替换（t19⑤ 钉还原与「字段面零增长」）。

跑法：这是库，自测随门禁走 ——
  `cd /e/Codeharness && PYTHONPATH=/e/Codeharness:/e/Codeharness/logs PYTHONIOENCODING=utf-8 \\
   PYTHONDONTWRITEBYTECODE=1 REDIS__DB=15 LANGFUSE__ENABLED=0 NO_PROXY=127.0.0.1,localhost,::1 \\
   F:/anaconda/python.exe -B tests/s8_runner_meter.py`（组 t19）
用法见 `tests/manual_stop_realmodel_live.py`。
"""
from codeharness.provider import cost as cost_mod
from codeharness.provider.cost import CostManager

# 同形状的历史实花（人民币）。每条第二个字段是**仓内台账里那一行的指针**——不许凭印象加数，
# 也不许把 `E:/tmp` 那种会被清掉的落点当出处。这张表只能靠「读过台账再改代码」长大：
# 安全闸的每一次加宽都必须出现在 diff 里，这正是它住在源码而不在数据文件里的理由
# （`storage/` 被 .gitignore:16 整目录忽略，放那儿等于悄悄改数）。
# ⚠ 2026-10-08 **已按修后真读数重标**（原值来自修前虚高路径：那时非流式支路把「本端点每个 chunk
# 都回的 usage」按块累加，实测同一发厂商回执 312 / 我方 19,038 ⇒ `cost_cny` 虚高 1~2 个数量级；
# 缺陷已修，判据 `tests/s2_gateway.py::t23`）。**旧值已删**——它们指回的那些台账行本身就是虚高读数，
# 留着会让这张表谎报形状的真实花费（后果是闸拒掉本来批得下来的小额场：旧 react 档 0.356093 会让
# 任何 <¥0.36 的闸一律不发）。本次重测的读数与探针在 `plan/model-gateway.md` §1.12.2。
# ponytail: 这张表是**已观测的最大值**，不是尾部风险上界——thinking 模型单发能烧多少不由它兜。
# 尾部靠「撞线判在记账那一刻」的闸 + 用户批的发数/墙钟上限，别指望这张表。
HISTORY: dict[str, list[tuple[float, str]]] = {
    "react": [(0.003461, "plan/model-gateway.md §1.12.2（10-08 修后重测：react/readonly/n_round=1，"
                         "1 发、17.8s、pt 70/ct 1625、awaiting_human）")],
    "dynamic": [(0.003123, "plan/model-gateway.md §1.12.2（10-08 修后重测：dynamic/readonly/n_round=1，"
                           "2 发、10.5s、pt 3580/ct 294、finished）")],
    "classic": [(0.015846, "plan/model-gateway.md §1.12.2（10-08 修后重测：classic/workspace_write/"
                           "n_round=2，**只跑完 1 发**、150s 墙钟到仍在 running、pt 422/ct 7405）"),
                (0.013138, "plan/model-gateway.md §1.12.3（10-08 修后重测·补：同形状 n_round=2，"
                           "**1 发即 finished**、176.8s、pt 427/ct 6114）")],
}


class CapHit(BaseException):
    """撞线。抛在**这一发记完账之后**：已花的拦不回，但异常顺着调用栈把这场掐掉，下一发发不出去。

    继承 `BaseException` 是有意的（见模块 docstring ①）：`except Exception:` 兜底重问会把普通异常
    咽掉并立刻产生第二笔花费。读数挂在异常对象上——账本随后会被 `runner._forget(terminal=True)`
    pop 掉（「清场前取数」那条坑），闸自己得留一份。
    """

    def __init__(self, cny: float, usd: float, calls: int, cap_cny: float, cap_usd: float):
        self.cny, self.usd, self.calls = cny, usd, calls
        self.cap_cny, self.cap_usd = cap_cny, cap_usd
        super().__init__(f"第 {calls} 笔记账后 ¥{cny:.6f} / ${usd:.6f} 撞闸"
                         f"（¥{cap_cny} / ${cap_usd}）")


def armed(cap_cny: float, cap_usd: float = 0.0):
    """把撞线判定装到累加点上。返回 `(uninstall, state)`，**必须在 `finally` 里调 uninstall**——
    泄漏的闸会让同进程里后面的任何会话莫名其妙地掐掉。

    `state`：`calls`（记账笔数，含零用量那笔）·`peak_cny`/`peak_usd`（撞线前的累计峰值）·
    `hit`（撞线那次抛出的 `CapHit`，没撞则 None）。
    ⚠ `TokenCostManager`（本地/免费档）自己覆盖了 `update_cost`，不走这把闸——它本来就不算钱；
    「配了金额闸却选了不计价模型」这种瞎档由 `precheck` 第 ③ 条拒掉。
    """
    orig = cost_mod.CostManager.update_cost
    state = {"calls": 0, "peak_cny": 0.0, "peak_usd": 0.0, "hit": None}

    def gated(self, pt, ct, model):
        orig(self, pt, ct, model)                      # 先记账再判：判在账之后才拦得住下一发
        state["calls"] += 1
        state["peak_cny"] = max(state["peak_cny"], float(self.cost_cny))
        state["peak_usd"] = max(state["peak_usd"], float(self.cost_usd))
        if (cap_cny and self.cost_cny > cap_cny) or (cap_usd and self.cost_usd > cap_usd):
            state["hit"] = CapHit(float(self.cost_cny), float(self.cost_usd),
                                  state["calls"], cap_cny, cap_usd)
            raise state["hit"]

    cost_mod.CostManager.update_cost = gated

    def uninstall():
        cost_mod.CostManager.update_cost = orig

    return uninstall, state


def estimate(paradigm: str):
    """该形状历史最贵一场的人民币读数与出处；没记录回 `(None, 理由)`。"""
    rows = HISTORY.get(paradigm)
    if not rows:
        return None, f"HISTORY 里没有 {paradigm!r} 这一档"
    return max(rows, key=lambda r: r[0])       # 取**最贵**那一场：估便宜了等于没估


def precheck(paradigm: str, cap_cny: float, model: str = "") -> str:
    """发前闸。回 `""` 表示可以发；非空是**不许发**的理由（有界活体在起跑前调它）。

    三条拒发，缺一不可（`HISTORY` 只有人民币档，所以这一闸只判 CNY 桶；USD 价的模型走 ③）：
    ① 该形状没有历史读数＝估不动就不发（第一次跑某个形状不该由闸说了算，由点头说了算）；
    ② 历史最贵一场 > 闸＝这一档闸罩不住这场（事故之后 `react` 那档的下界就是 ¥0.356093）；
    ③ 模型的计价币种不是人民币＝闸读的那本账永远撞不了线（拿 `$` 的账去比 `¥` 的闸，形同虚设）。
    """
    est, src = estimate(paradigm)
    if est is None:
        return f"估不动就不发：{src}"
    if model:
        cc = CostManager().currency_of(model)
        if cc != "CNY":
            return (f"闸与账不同币种：{model} 计价 {cc or '（不在价目表＝不计价，账恒 0）'}，"
                    f"而 HISTORY 与闸都是人民币档 ⇒ 撞线判定永不响，不发")
    if est > cap_cny:
        return f"同形状历史最贵 ¥{est:.6f}（{src}）> 闸 ¥{cap_cny} ⇒ 闸罩不住，不发"
    return ""
