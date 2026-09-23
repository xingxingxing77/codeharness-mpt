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
    # ⚠ 有一条**没被本格证明**，别当已证：runner 读到非零的前提是"角色手上的 manager 就是
    # runner `self.costs[sid]` 那一份"（`_make_llm` 注入，B8 的截断计数同吃这条）。t10③ 只测了
    # "同一个 manager 的快照带出两键"，**没测那条注入链**——真端到端要起一场真会话读 GET 出口，
    # 已登记为未验边界（PLAN §1 本棒那行）。
    unknown_command_calls: int = 0
    # C19：正文空的调用（模型说完话但一个字没吐）。与上面两笔同族——只是计数，不参与金额口径。
    # 为什么要第三笔：2026-09-23 真云端实测 14 发里最贵那一发（¥0.914、24.5 万 completion token、
    # 10.1 秒）正文是**空串**，厂商自己认为说完了 ⇒ 没有 `finish_reason=length`（不进截断），
    # 也没走到命令派发（不进未知命令）。那种"钱花了、产出为零"的形态在账面上原本隐形。
    # ⚠ 只调工具不说话的那一笔**不算**浪费（`tool_calls` 非空即合法），所以判据两边都要有对照。
    empty_output_calls: int = 0

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
