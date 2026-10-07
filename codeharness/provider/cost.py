"""用量计量：token 与成本的**只读**累计。

`Costs`/`CostManager.update_cost`/`TokenCostManager` 是计量本体；`add_usage` 是 LangChain 适配层；
`records` 供 trace 与前端用量面板读取。

这里没有任何预算判断：不设上限、不抛异常、不预测"这一问值不值得发"。
限额属于计费域，将来落在 HTTP 入口或网关外壳，不进 agent 内核。
"""
from typing import NamedTuple

from pydantic import BaseModel

from codeharness.logs import logger
from codeharness.provider.token_costs import CNY_MODELS, TOKEN_COSTS

# P0：校准系数 k 的合法域（闭合夹取边界）——已定口径，不是可调参数。
CALIBRATION_K_MIN, CALIBRATION_K_MAX = 0.5, 4.0


class Costs(NamedTuple):
    """用量快照。**没有单一 total_cost 字段**——那是 C12 修掉的口径错误：两种币价的数字
    加在一个无单位 float 上，报出来的数既不是美元也不是人民币。要总额请先各看各的桶。"""
    total_prompt_tokens: int
    total_completion_tokens: int
    cost_usd: float
    cost_cny: float


class CostManager(BaseModel):
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    cost_usd: float = 0
    cost_cny: float = 0
    token_costs: dict = TOKEN_COSTS
    cny_models: frozenset = CNY_MODELS        # 可注入，门禁靠它造「同场混两种币种」的读数
    records: list = []
    # B8：被输出上限截断了几笔调用。**只是计数**，不参与任何计量口径。
    # 为什么记在账本上而不是记在事件翻译里：每笔调用落账时都把自己的 `response_metadata` 带到这里，
    # 而 `_translate` 的 `on_chat_model_end` 在最伤的那种截断上（JSON 被切半→解析失败→走 repair）
    # 拿不到 finish_reason——活体实测三次 length 收尾零条提示。截断要说话就得站在不漏的口上。
    truncated_calls: int = 0
    # T4-③：模型吐「本轮工具面里没有的命令」被回喂了几笔。与 truncated_calls 同族——都是
    # "这一发白花了"的只读计数，不参与金额口径。为什么要落在账本上：日志行只能让人 grep
    # 单场，而用量页要的是跨会话求和，那个出口只能有一个（另起一处就长出第二个游标）。
    # 注入链今天证过了（09-25 新格 `tests/s8_runner_meter.py::t10`，零花费）：起本机 OpenAI 兼容桩
    # 跑一整场 dynamic 线会话到 `finished`，从 **GET 出口**读到 pt=411/ct=87/¥0.00047 而 $ 保持 0
    # ——「角色手上的 manager 就是 runner `self.costs[sid]` 那一份」不再只是一条注释（B8 的截断计数
    # 同吃这条）。反证也跑了：把 `server/runner.py::_prepare` 里的 `_make_llm(cost_manager, …)` 换成
    # `_make_llm(None, …)`，t10① 当场读到全零账本，正是当年「用量恒 0」的双账本形状。
    unknown_command_calls: int = 0
    # C19：正文空的调用（模型说完话但一个字没吐）。与上面两笔同族——只是计数，不参与金额口径。
    # 为什么要第三笔：2026-09-23 真云端实测 14 发里最贵那一发（¥0.914、24.5 万 completion token、
    # 10.1 秒）正文是**空串**，厂商自己认为说完了 ⇒ 没有 `finish_reason=length`（不进截断），
    # 也没走到命令派发（不进未知命令）。那种"钱花了、产出为零"的形态在账面上原本隐形。
    # ⚠ 只调工具不说话的那一笔**不算**浪费（`tool_calls` 非空即合法），所以判据两边都要有对照。
    empty_output_calls: int = 0
    # C122：模型给的 args 没过 schema 校验被回喂了几笔（C137 放宽口径到实现的真实形状：
    # 计数点在 role_zero._act 的泛 except 里按 ValidationError 认——工具的 args_schema 违例
    # 与 plan 命令的 Plan/Task 模型构造违例**都落进这一笔**，按窄口径解释读数会错账）。
    # 与上面三笔同族——只是计数，不参与金额口径。为什么要第四笔：
    # 「args spec 进 prompt」与「原生 function-calling」两件立项与否原来全凭感觉，这个数是它们的
    # 读数出口——重开条件写死在 PLAN §4 C122 行：活体读数持续为 0 ⇒ 两件都不立项；> 0 ⇒ 拿读数谈。
    invalid_args_calls: int = 0
    # R1（09-28 普查批）：召回链（读腿）的三个只读计数。与上面三笔同族——只是计数，不参与金额口径。
    # 为什么要装表：普查现证 09-27 全天 kb 腿 1 次成功 / 595 次连接失败，09-28 是 6/299，而失败只有
    # 一行 warning（`logger.warning` 里那句「按无资料继续」），界面上与「知识库里真没资料」同一个长相；
    # memory 腿更彻底——`RECALL_FLOOR__MEMORY_MODE=off` 之后连 floor 那条 debug 行都不发，零痕迹。
    # 三个数就是 hit-rate 的分子分母：成功率 = returned/(returned+zero_hits)，可用性 = failures 那笔。
    # ⚠ `recall_returned` 数的是**召回回来的切片条数**（不是调用次数），一次召 3 条就 +3。
    recall_failures: int = 0
    recall_zero_hits: int = 0
    recall_returned: int = 0
    # R4（09-29）：**写腿**两笔。为什么单独两笔而不并进上面三笔：`overflow` 的异常刻意留在 caller
    # （`agent._ltm_flush` 的游标只在成功时进位＝C34 的「失败留到下轮重试」语义，R2 因此不收 catch），
    # 但「不收 catch」不等于「不计数」——数在 `overflow` 内部点、异常照旧外抛，两边语义一字未动。
    # 这两笔配成对才读得出账：`overflow_written` 是存进去的**点数**，`overflow_failed` 是**次数**；
    # 没有前者，后者为零也可能是「压根没写过东西」。
    overflow_failed: int = 0
    overflow_written: int = 0
    # C184（10-07）：窗口占用三笔。与上面各笔同族——只是计数，不参与金额口径。
    # ⚠ 口径钉死：`last_prompt_tokens` 是**这一发请求的输入总量**＝发那一刻的窗口占用，
    # 不是累计消耗（累计那半住在 `total_prompt_tokens` 里，两个数别互相冒充——界面的
    # 「上下文容量」卡按前者对预算算百分比）。厂商每笔只回一个总 input_tokens，没有分类归因；
    # `last_system_tokens` 是末笔 system 段的网关侧计数（`gateway._apply_compression` 每笔都数，
    # 闸关着也数——分类行要的是这个事实，不是闸的副产品），「其余」＝last 减 system，纯派生。
    # 三笔都随快照持久化并在 resume 时播种（C78 形状：漏播种＝重启后峰值静默归 0，0 是合法读数看不出）。
    last_prompt_tokens: int = 0
    peak_prompt_tokens: int = 0
    last_system_tokens: int = 0
    # P0（10-07）：校准系数 k——「上下文预算契约」的一半（P1 的 ContextBudget 按它换算 clip 额度、
    # P2 按它校正水位），住在**会话级账本**上（per-sid 单例、随快照持久化、resume 播种）。
    # 口径已定不重议：初值 1、夹 [0.5,4]、切模型即重置（唯一写入口 `note_calibration`）；
    # P0 期零消费者（默认档行为逐字不变，s13 t15 钉住）。`calibration_model` 是「切模型」的
    # 判定依据，必须与 k 同批持久化——漏播种的形状：resume 后第一笔读数把 "" 当成「换了模型」误重置。
    calibration_k: float = 1.0
    calibration_model: str = ""
    # P0（10-07）：截窗静默丢失的消息**条数**——消息离开工作窗口（超出 `memory_k`）时既无摘要(L1)
    # 也无逐字归档(L2)去路的条数（招募成员装出来的形状最典型）。PLAN 唯一硬指标「静默丢失计数恒为 0」
    # 数的是它；P3 给招募成员补挂 brain/ltm 之前，这个数是那条缺口的暴露面。
    silent_lost: int = 0

    def currency_of(self, model: str) -> str:
        """该模型的记账币种。未登记的模型回 ""（不计价），别让未知模型冒充 USD。"""
        if model not in self.token_costs:
            return ""
        return "CNY" if model in self.cny_models else "USD"

    def update_cost(self, prompt_tokens, completion_tokens, model):
        if prompt_tokens + completion_tokens == 0 or not model:
            return
        self.total_prompt_tokens += prompt_tokens
        self.total_completion_tokens += completion_tokens
        cc = self.currency_of(model)
        if not cc:
            logger.warning(f"Model {model} not found in TOKEN_COSTS, cost not counted.")
            return
        rate = self.token_costs[model]
        cost = (prompt_tokens * rate["prompt"] + completion_tokens * rate["completion"]) / 1000
        if cc == "CNY":
            self.cost_cny += cost
        else:
            self.cost_usd += cost
        # 日志也分符号：这一行原先硬写 `$`（`Total running cost: $…`），而表里本来就有人民币行
        logger.info(f"Total running cost: ${self.cost_usd:.3f} / ¥{self.cost_cny:.3f} | "
                    f"Current: {cost:.6f} {cc}, pt={prompt_tokens}, ct={completion_tokens}")

    def note_window_usage(self, pt: int):
        """C184：窗口占用观测的唯一定义点（`add_usage` 与 structured 的 ChatCompletion 回执支路都调它）。
        pt=0 的那一发是「没有回执」不是「窗口空了」——不更新，别让卡片把缺账读成 0%。"""
        if pt > 0:
            self.last_prompt_tokens = pt
            self.peak_prompt_tokens = max(self.peak_prompt_tokens, pt)

    def note_calibration(self, k: float, model: str = "") -> None:
        """P0：校准系数的唯一写入口（口径已定：初值 1、夹 [0.5,4]、会话级、切模型即重置）。

        切模型的语义 = **先回 1 再应用本笔读数**：`model` 非空且与上次不同时先重置——本笔有合法
        读数（夹取后）就更新，坏读数/无读数就停在 1。这样旧模型的校准不会残留到新模型上
        （tokenizer 的近似偏差是按模型变的），也不会把「没读数」读成新读数
        （0/负值不是读数：把 0 夹成 0.5 是在编数据，与 `note_window_usage` 的 pt=0 同一条纪律）。
        P0 期零调用者；P2 的 `_compress` 校准写它，P1 的 ContextBudget 读它。"""
        m = str(model or "")
        if m and m != self.calibration_model:
            self.calibration_model, self.calibration_k = m, 1.0
        if k and k > 0:
            self.calibration_k = min(max(float(k), CALIBRATION_K_MIN), CALIBRATION_K_MAX)

    def get_costs(self) -> Costs:
        return Costs(self.total_prompt_tokens, self.total_completion_tokens,
                     self.cost_usd, self.cost_cny)

    def add_usage(self, resp, model: str = "", tag: str = ""):
        """取一次响应的用量，两个来源按新栈口径排优先级：

        1. `usage_metadata` —— LangChain 1.x 标准字段。`streaming=True` 建出来的 ChatOpenAI
           （本仓 `cfg.stream` 默认即 True）只往这里写，`token_usage` 是 None；
        2. `response_metadata.token_usage` —— OpenAI 原始口径，非流式构造时才有。

        只读第 2 条会让整条线跑完账上是 0（2026-09-15 真模型实测）。
        流式还须给底层带 `stream_usage=True`，否则末块连 usage 都不回。"""
        um = getattr(resp, "usage_metadata", None) or {}
        if str((getattr(resp, "response_metadata", None) or {}).get("finish_reason") or "") == "length":
            self.truncated_calls += 1                 # B8：截断计数，与钱/token 口径无关
        # C19：正文空。多模态 content 是块列表，取不到文本就当空；只调工具不说话的不算浪费。
        body = getattr(resp, "content", "")
        if isinstance(body, list):
            body = "".join(str(b.get("text", "")) if isinstance(b, dict) else str(b) for b in body)
        if not str(body).strip() and not (getattr(resp, "tool_calls", None) or []):
            self.empty_output_calls += 1
        if um:
            pt, ct = um.get("input_tokens", 0) or 0, um.get("output_tokens", 0) or 0
        else:
            usage = (getattr(resp, "response_metadata", None) or {}).get("token_usage") or {}
            pt, ct = usage.get("prompt_tokens", 0) or 0, usage.get("completion_tokens", 0) or 0
        if pt + ct == 0:
            # 漏账必须可见：update_cost 对 0 静默 return，真模型实测一场会话里几十次调用
            # 只有零星几笔入账时无从分辨"哪条路没回执"（2026-09-15）。有 warning 才有可 grep 的账差。
            logger.warning(f"add_usage: response 无 usage，本笔不进账 (model={model}, tag={tag})")
        self.note_window_usage(pt)
        self.update_cost(pt, ct, model)
        # cc 是这一笔的币种（"" = 未计价模型）。逐笔留痕是给 trace 与对账用的：
        # 只有合计的话，混币种这件事在数据里就看不见了——正是 C12 的根因形状。
        self.records.append({"tag": tag, "model": model, "pt": pt, "ct": ct,
                             "cc": self.currency_of(model)})


class TokenCostManager(CostManager):
    """本地/免费模型：只记 token 不算钱。"""

    def update_cost(self, prompt_tokens, completion_tokens, model):
        self.total_prompt_tokens += prompt_tokens
        self.total_completion_tokens += completion_tokens
