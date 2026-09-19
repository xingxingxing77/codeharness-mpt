"""LLM 网关：全项目唯一与对话模型通话的入口。判定 `重`（R1）。

源 `provider/` 3,614 行（`base_llm.py` 412 + `general_api_base.py` 581 + 16 家手写客户端）
**一律不复制**——那是本栈要用 LangChain 的理由本身。这里只做两件事：
1. 底座换成 `langchain_openai.ChatOpenAI`（S10 再按 `api_type` 分派其余厂商）
2. **对外签名照源**，因为 S6 是从源逐字复制 Action/Role，它们只认这套名字

对齐的源公开面（`base_llm.py`）：`aask` / `aask_batch` / `aask_code` / `acompletion_text` /
`with_model` / `format_msg` / `count_tokens`。⚠ 两处形态分叉登记在案（判定表 §六 台账）：
`aask_code` 源经 function-call 返回 dict{language,code}（消费方 ut_writer 已判 `弃`），本仓返回纯代码块 str；
源 `aask` 的 `format_msgs`/`images` 参数未搬（现源无消费方用到 images 路径的逐字件）。

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
        if cfg.stream:
            # 没有它，OpenAI 兼容端点的流式响应末块不回 token_usage → add_usage(0,0) → 整条线账为 0
            kwargs["stream_usage"] = True
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

    def _count_tokens_direct(self, messages: Union[str, list, None]) -> int:
        """直接计数消息的 token 数（tiktoken，不查模型表）。"""
        from tiktoken import get_encoding
        
        tokens = 0
        try:
            encoding = get_encoding("cl100k_base")  # gpt-4o 同款
            for msg in (messages if isinstance(messages, list) else [messages]):
                content = msg.content if hasattr(msg, "content") else str(msg)
                tokens += len(encoding.encode(content))
        except Exception:
            # 降级：按字符数/4 估算
            content = messages if isinstance(messages, str) else "\n".join(
                str(m.content if hasattr(m, "content") else m) for m in (messages or [])
            )
            tokens = len(content) // 4
        return tokens
    
    async def _compress_messages(self, msgs: list, max_tokens: int) -> list:
        """B6: 精确压缩——保留最近的消息直到总 token ≤ max_tokens（post_cut_by_token）。

        是否触发压缩由调用点 ainvoke 按 `context_length × compress_threshold` 决定；
        本函数被调用即执行裁剪（不再二次判 compress_type，否则与调用点两套门互搏）。
        策略按 compress_type 分派（源 base_llm.py 四策略）在批次 5 引入。
        ponytail: ceiling = 目前只有 post_cut_by_token 一种，by_msg/pre_cut 留待批次 5。

        system 恒留（照源 base_llm.py：先摘 system 再裁非 system，最后 system 插回队首）。
        """
        system_msgs = [m for m in msgs if getattr(m, "type", getattr(m, "role", "")) in ("system", "developer")]
        rest = [m for m in msgs if m not in system_msgs]
        budget = max_tokens - self._count_tokens_direct(system_msgs)

        compressed = []
        current_tokens = 0

        # 从后往前累加（保留最近的）
        for msg in reversed(rest):
            msg_tokens = self._count_tokens_direct([msg])
            if current_tokens + msg_tokens > budget and compressed:
                break
            current_tokens += msg_tokens
            compressed.insert(0, msg)  # 保持顺序

        if not compressed and rest:
            compressed = [rest[-1]]    # 至少保留最后一条非 system
        return system_msgs + compressed  # system 恒在队首
    
    @staticmethod
    def embeddings():
        """bge-m3 embedding 工厂（1024 维，OpenAI 兼容端点）。向量模型不走 gateway。

        ⚠ `check_embedding_ctx_length=False`：langchain 默认先用 tiktoken 把输入编码成
        token-id 数组再发——本机 ollama 的 /v1/embeddings 只收字符串，收数组直接
        `400 invalid input type`（2026-09-16 实测）。直发原文，本地端点自己管分词。"""
        from codeharness.configs.settings import settings
        return OpenAIEmbeddings(model=settings.embedding.model, base_url=settings.embedding.base_url,
                                api_key=settings.embedding.api_key or "EMPTY",
                                check_embedding_ctx_length=False)

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
        
        # B6: token 压缩——按 context_length × threshold 裁断（保留最近的消息）
        if self.cfg.context_length and self.cfg.compress_threshold < 1.0:
            max_tokens = int(self.cfg.context_length * self.cfg.compress_threshold)
            
            # 直接计算 tokens（不调用 count_message_tokens 避免模型查表）
            current_tokens = self._count_tokens_direct(msgs)
            
            if current_tokens > max_tokens:
                # 精确压缩：逐条累加直到超过阈值
                compressed = await self._compress_messages(msgs, max_tokens)
                msgs = compressed
        
        model = self._model.bind(**kwargs) if kwargs else self._model
        deadline = timeout or self.cfg.timeout

        if stream:
            pieces, usage, meta = [], None, {}
            async for chunk in model.astream(msgs):
                if getattr(chunk, "usage_metadata", None):
                    usage = chunk.usage_metadata        # usage 常在中间块，末块反而是 None
                meta = getattr(chunk, "response_metadata", None) or meta
                if chunk.content:
                    pieces.append(chunk.content)
                    log_llm_stream(chunk.content)
            resp = AIMessage(content="".join(pieces), response_metadata=meta)
            if usage is not None:
                resp.usage_metadata = usage
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
        解析失败时回落 `provider.repair` 的升级式修复档（S3 的 R2 会在此加**字段级**定向重试）。

        ⚠ 内部恒用 `include_raw=True`：这条路不经过 `ainvoke`，不在这里记账就整条动态范式
        零账（RoleZero 每轮思考都走它）。对外契约不变——默认仍返回 schema 实例。"""
        runnable = self._model.with_structured_output(schema, include_raw=True)

        class _Wrapped:
            """保持与 FakeLLM.structured() 同构：await .ainvoke(prompt) -> schema 实例。"""

            def __init__(self, outer: "LLMGateway"):
                self.outer, self.schema = outer, schema

            def _account(self, raw, tag: str):
                """raw 有两种形态：langchain 消息，或 LengthFinishReasonError 挂的 openai ChatCompletion。
                被截断的那次调用照样花了钱，不记就是漏账。"""
                if not self.outer.cfg.calc_usage or raw is None:
                    return
                if getattr(raw, "usage_metadata", None) or getattr(raw, "response_metadata", None):
                    self.outer.cost_manager.add_usage(raw, model=self.outer.cfg.model, tag=tag)
                    return
                usage = getattr(raw, "usage", None)          # ChatCompletion.usage
                if usage is not None:
                    self.outer.cost_manager.update_cost(getattr(usage, "prompt_tokens", 0) or 0,
                                                        getattr(usage, "completion_tokens", 0) or 0,
                                                        self.outer.cfg.model)

            def _repair(self, text: str):
                from codeharness.provider.repair import repair_to_model
                fixed = repair_to_model(text, self.schema) if text else None
                if fixed is not None:
                    logger.warning(f"structured 解析失败，已走 repair_to_model 修复档：{self.schema.__name__}")
                return fixed

            @staticmethod
            def _partial_text(exc) -> str:
                """解析失败时模型到底回了什么：两种挂法都要覆盖到。
                `exc.output` 是 langchain 解析器失败的常规形态；`exc.completion` 是
                `LengthFinishReasonError`（thinking 模型把 max_token 烧光）的形态。"""
                raw = getattr(exc, "output", None)
                text = getattr(raw, "content", None) or (raw if isinstance(raw, str) else "")
                if text:
                    return text
                try:
                    return (exc.completion.choices[0].message.content or "")
                except Exception:
                    return ""

            async def ainvoke(self, prompt, tag: str = "", timeout: int = USE_CONFIG_TIMEOUT, **kw):
                deadline = timeout or self.outer.cfg.timeout
                try:
                    out = await _acall(runnable.ainvoke, prompt, timeout=deadline, **kw)
                except Exception as exc:
                    self._account(getattr(exc, "output", None) or getattr(exc, "completion", None), tag)
                    fixed = self._repair(self._partial_text(exc))
                    if fixed is None:
                        if type(exc).__name__ == "LengthFinishReasonError":
                            raise ValueError(
                                f"模型输出被 max_token={self.outer.cfg.max_token} 截断，结构化解析无果："
                                f"提高 LLM__MAX_TOKEN 或缩短单次产出（thinking 模型的 reasoning 也计在这里）"
                            ) from exc
                        raise
                    return {"raw": getattr(exc, "output", None), "parsed": fixed} if include_raw else fixed
                raw = (out or {}).get("raw")
                self._account(raw, tag)
                parsed = (out or {}).get("parsed")
                if parsed is None:                              # include_raw 形态下解析失败不抛，只带 error
                    fixed = self._repair(getattr(raw, "content", "") or "")
                    if fixed is None:
                        raise ValueError(f"structured 解析失败且修复无果: {str(getattr(raw, 'content', ''))[:200]}")
                    parsed = fixed
                return out if include_raw else parsed

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


def _retryable(exc: BaseException) -> bool:
    """只重试**超时与连接类**异常；HTTP 状态错（鉴权/参数错，openai 的 APIStatusError）立刻抛出。

    ⚠ 继承链坑：openai 的 APIStatusError 是 APIError 的子类——按类型族重试会把 401/400 也重了，
    重试只是烧钱。要重的是「非 status 的 APIError」基形态：流被掐断 / 服务端 abort 掉
    response_format 的 JSON 生成（qwen MaaS 实测：`APIError: Model output became abnormal
    while generating a JSON response for response_format`——同一请求重发即成，典型瞬态）。"""
    from openai import APIError, APIStatusError
    if isinstance(exc, (asyncio.TimeoutError, ConnectionError, OSError)):
        return True
    return isinstance(exc, APIError) and not isinstance(exc, APIStatusError)


async def _acall(fn, *args, timeout: int = 0, **kwargs):
    """指数退避重试（源在 `general_api_base` 里手写的 tenacity 语义，这里统一收口）。

    重试判据见 `_retryable`。流式路径不走这里——半截断流重放会让前端重复上屏，交由上层重新发起整轮。"""
    from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential
    result = {}
    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception(_retryable),
        reraise=True,
    ):
        with attempt:
            coro = fn(*args, **kwargs)
            result["v"] = await asyncio.wait_for(coro, timeout=timeout) if timeout else await coro
    return result["v"]
