"""LLM 网关：全项目唯一与对话模型通话的入口。判定 `重`（R1）。

源 `provider/` 3,614 行（`base_llm.py` 412 + `general_api_base.py` 581 + 16 家手写客户端）
**一律不复制**——那是本栈要用 LangChain 的理由本身。这里只做两件事：
1. 底座换成 `langchain_openai.ChatOpenAI`（S10 再按 `api_type` 分派其余厂商）
2. **对外签名照源**，因为 S6 是从源逐字复制 Action/Role，它们只认这套名字

对齐的源公开面（`base_llm.py`）：`aask`:179 / `aask_batch`:215 / `acompletion_text`:256 /
`with_model`:324 / `format_msg` / `count_tokens` / `get_next_prompt` / `_consistency_check`。

三件事在这里收口（源分散在 cost_manager / base_llm / report 三处）：
token 计数、成本累计、流式回调 `log_llm_stream`。
"""
from __future__ import annotations

import asyncio
from typing import AsyncIterator, Iterable, Optional, Type, Union

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from pydantic import BaseModel, SecretStr

from codeharness.configs.llm_config import LLMConfig, LLMType
from codeharness.logs import log_llm_stream, logger

# 源 `USE_CONFIG_TIMEOUT = 0` 表示"用配置里的 timeout"，非 0 则本次调用覆盖它
from codeharness.const import USE_CONFIG_TIMEOUT


class NoMoneyException(Exception):
    """预算耗尽。与 provider/cost.py 同一异常类（team_graph.budget_guard 抛出）。"""


def _message_to_dict(m: Union[str, dict, BaseMessage]) -> dict:
    """把单条消息归一成 openai 的 {role, content} dict（源 format_msg 的叶子）。"""
    if isinstance(m, str):
        return {"role": "user", "content": m}
    if isinstance(m, dict):
        return m
    role = getattr(m, "type", None) or getattr(m, "role", "user")
    mapping = {"human": "user", "ai": "assistant", "system": "system"}
    return {"role": mapping.get(role, role), "content": getattr(m, "content", str(m))}


class LLMGateway:
    def __init__(self, cfg: Optional[LLMConfig] = None, cost_manager=None):
        from codeharness.configs.settings import settings
        from codeharness.provider.cost import CostManager
        self.cfg = cfg or settings.llm
        self.cost_manager = cost_manager or CostManager()
        self._model = self._build(self.cfg)

    # ---- 构造 ----------------------------------------------------------------
    @staticmethod
    def _build(cfg: LLMConfig) -> ChatOpenAI:
        """按 cfg 建模型。非 openai 兼容端点目前明确报错，等 S10 逐厂商接（不静默退回 openai）。"""
        if cfg.api_type not in (LLMType.OPENAI, LLMType.OPEN_LLM, LLMType.DEEPSEEK, LLMType.MOONSHOT,
                                LLMType.SILICONFLOW, LLMType.YI, LLMType.MISTRAL, LLMType.OPENROUTER,
                                LLMType.OPEN_ROUTER, LLMType.OPENROUTER_REASONING, LLMType.GEMINI):
            raise NotImplementedError(
                f"api_type={cfg.api_type.value} 的适配器在 S10 提供；"
                f"当前 gateway 只接 OpenAI 兼容端点。见 docs/施工1 S2 与判定表 R1。")
        kwargs = dict(
            model=cfg.model,
            api_key=SecretStr(cfg.api_key or "EMPTY"),
            base_url=cfg.base_url,
            temperature=cfg.temperature,
            max_tokens=cfg.max_token,                 # ⚠ 源字段是 max_token（单数）
            streaming=cfg.stream,
            timeout=cfg.timeout or None,
        )
        if cfg.top_p != 1.0:
            kwargs["top_p"] = cfg.top_p
        if cfg.n is not None:
            kwargs["n"] = cfg.n
        if cfg.stop:
            kwargs["stop"] = cfg.stop
        if cfg.presence_penalty:
            kwargs["presence_penalty"] = cfg.presence_penalty
        if cfg.frequency_penalty:
            kwargs["frequency_penalty"] = cfg.frequency_penalty
        if cfg.api_version:
            kwargs["model_kwargs"] = {"api_version": cfg.api_version}
        return ChatOpenAI(**kwargs)

    @staticmethod
    def embeddings():
        """bge-m3 embedding 工厂（1024 维，OpenAI 兼容端点）。向量模型不走 gateway。"""
        from codeharness.configs.settings import settings
        return OpenAIEmbeddings(model=settings.embedding.model, base_url=settings.embedding.base_url,
                                api_key=settings.embedding.api_key or "EMPTY")

    # ---- 源 BaseLLM 的公开面 --------------------------------------------------
    def format_msg(self, messages: Union[str, dict, BaseMessage, list]) -> list[BaseMessage]:
        """源 `BaseLLM.format_msg`：str / dict / BaseMessage 混合列表 → LangChain 消息列表。"""
        if isinstance(messages, str):
            return [HumanMessage(content=messages)]
        if isinstance(messages, BaseMessage):
            return [messages]
        if isinstance(messages, dict):
            messages = [messages]
        out = []
        for m in messages:
            if isinstance(m, BaseMessage):
                out.append(m)
                continue
            d = _message_to_dict(m)
            role, content = d["role"], d["content"]
            out.append(SystemMessage(content=content) if role == "system"
                       else AIMessage(content=content) if role in ("assistant", "ai")
                       else HumanMessage(content=content))
        return out

    def count_tokens(self, content: Union[str, list, dict]) -> int:
        """源 `BaseLLM.count_tokens`（那是方法，不在 utils 里）。
        底层组合 `utils/token_counter` 的两个真函数：字符串走 count_output_tokens，
        消息列表走 count_message_tokens。"""
        from codeharness.utils.token_counter import count_message_tokens, count_output_tokens
        if isinstance(content, str):
            return count_output_tokens(string=content, model=self.cfg.model)
        msgs = [content] if isinstance(content, dict) else [_message_to_dict(m) for m in content]
        return count_message_tokens(messages=msgs, model=self.cfg.model)

    async def ainvoke(self, msgs: Union[str, list, None] = None, tag: str = "",
                      stream: bool = False, timeout: int = USE_CONFIG_TIMEOUT, **kwargs) -> BaseMessage:
        """唯一的出口：所有计数/trace 都在这里，**别在别处再算一遍**。"""
        msgs = self.format_msg(msgs or [])
        model = self._model.bind(**kwargs) if kwargs else self._model
        deadline = timeout or self.cfg.timeout

        if stream:
            pieces, last_chunk = [], None
            agen = model.astream(msgs)
            last_chunk = None
            async for chunk in agen:
                last_chunk = chunk
                if chunk.content:
                    pieces.append(chunk.content)
                    log_llm_stream(chunk.content)
            resp = AIMessage(content="".join(pieces),
                             response_metadata=getattr(last_chunk, "response_metadata", {}) or {})
        else:
            resp = await _acall(model.ainvoke, msgs, timeout=deadline)

        if stream:
            log_llm_stream("\n")
        if self.cfg.calc_usage:                       # ⚠ 单点计数：cost 只在这里更新
            self.cost_manager.add_usage(resp, model=self.cfg.model, tag=tag)
        return resp

    async def aask(self, msg: Union[str, list], system_msgs: Optional[list[str]] = None,
                   stream: bool = False, tag: str = "", timeout: int = USE_CONFIG_TIMEOUT, **kwargs) -> str:
        """源 :179。⚠ 必须走 ainvoke，否则不记账（源 FakeLLM 曾在此漏记账）。"""
        msgs = [SystemMessage(content=s) for s in (system_msgs or [])]
        msgs += self.format_msg(msg) if not isinstance(msg, str) else [HumanMessage(content=msg)]
        return (await self.ainvoke(msgs, tag=tag, stream=stream, timeout=timeout, **kwargs)).content

    async def aask_batch(self, msgs: list, *, stream: bool = False, tag: str = "",
                         timeout: int = USE_CONFIG_TIMEOUT, **kwargs) -> str:
        """源 :215：多模/批量上下文一次问。"""
        return (await self.ainvoke(self.format_msg(msgs), tag=tag, stream=stream, timeout=timeout, **kwargs)).content

    async def aask_code(self, messages: Union[str, list], system_msgs: Optional[list[str]] = None,
                        format_msgs: Optional[list[str]] = None, language: str = "python",
                        timeout: int = USE_CONFIG_TIMEOUT, tag: str = "", **kwargs) -> str:
        """源 :246：取代码块。"""
        formatted_messages = [SystemMessage(content=s) for s in (system_msgs or [])]
        formatted_messages += [SystemMessage(content=f) for f in (format_msgs or [])]
        formatted_messages += self.format_msg(messages)
        rsp = await self.ainvoke(formatted_messages, timeout=timeout, tag=tag, **kwargs)
        from codeharness.utils.common import CodeParser
        return CodeParser.parse_code(block="", text=rsp.content, lang=language)

    async def aask_with_examples(self, prompt: str, examples: Iterable[tuple[str, str]] = (),
                                 system_msgs: Optional[list[str]] = None,
                                 tag: str = "", timeout: int = USE_CONFIG_TIMEOUT) -> str:
        """源 `_aask_v1` 的 few-shot 变体：examples 展开为 user/assistant 交替消息。"""
        msgs = [SystemMessage(content=s) for s in (system_msgs or [])]
        for q, a in examples:
            msgs += [HumanMessage(content=q), AIMessage(content=a)]
        msgs.append(HumanMessage(content=prompt))
        return (await self.ainvoke(msgs, tag=tag, timeout=timeout)).content

    async def acompletion_text(self, messages: list[dict], stream: bool = False,
                               timeout: int = USE_CONFIG_TIMEOUT) -> str:
        """源 :256：直接吃 openai 风格 dict 列表。"""
        return (await self.ainvoke(messages, stream=stream, timeout=timeout)).content

    def structured(self, schema: Type[BaseModel], include_raw: bool = False):
        """替代源 `action_node.py` 的 876 行结构化引擎。
        解析失败时回落 `provider.repair` 的升级式修复档（S3 的 R2 会在此加**字段级**定向重试）。"""
        runnable = self._model.with_structured_output(schema, include_raw=include_raw)

        class _Wrapped:
            """保持与 FakeLLM.structured() 同构：await .ainvoke(prompt) -> schema 实例。"""

            def __init__(self, outer: "LLMGateway"):
                self.outer, self.schema = outer, schema

            async def ainvoke(self, prompt, **kw):
                try:
                    return await runnable.ainvoke(prompt, **kw)
                except Exception as exc:
                    from codeharness.provider.repair import repair_to_model
                    raw = getattr(exc, "output", None)
                    text = getattr(raw, "content", None) or (raw if isinstance(raw, str) else "")
                    fixed = repair_to_model(text, self.schema) if text else None
                    if fixed is None:
                        raise
                    logger.warning(f"structured 解析失败，已走 repair_to_model 修复档：{type(exc).__name__}")
                    return fixed

        return _Wrapped(self)

    def with_model(self, model: str) -> "LLMGateway":
        """源 :324：换模型但共用成本账本（同一次会话的多档模型计费要合流）。"""
        clone = LLMGateway.__new__(LLMGateway)
        clone.cfg = self.cfg.model_copy(update={"model": model})
        clone._model = self._model.bind(model=model)
        clone.cost_manager = self.cost_manager
        return clone

    async def check_connection(self, attempts: int = 3) -> bool:
        """源 BaseLLM.check_connection：探活，不抛。"""
        for i in range(attempts):
            try:
                await self._model.ainvoke("hi")
                return True
            except Exception as exc:
                logger.warning(f"api health check failed ({i + 1}/{attempts}): {exc}")
                await asyncio.sleep(1)
        return False


async def _acall(fn, *args, timeout: int = 0, **kwargs):
    """指数退避重试（源在 `general_api_base` 里手写的 tenacity 语义，这里统一收口）。

    只对**超时与连接类**异常重试；HTTP 4xx（鉴权/参数错）立刻抛出，重试只是烧钱。
    流式路径不走这里——半截断流重放会让前端重复上屏，交由上层重新发起整轮。"""
    from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential
    result = {}
    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((asyncio.TimeoutError, ConnectionError, OSError)),
        reraise=True,
    ):
        with attempt:
            coro = fn(*args, **kwargs)
            result["v"] = await asyncio.wait_for(coro, timeout=timeout) if timeout else await coro
    return result["v"]
