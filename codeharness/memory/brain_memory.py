"""BrainMemory：会话对话史 + 单个 Redis key 整体 JSON + 超窗滚动摘要。

判定 `复改`（源 memory/brain_memory.py 345 行 → 本件约 120 行）。留下的都是本仓真用到的：
两个 Message 列表、historical_summary、dirty 才落盘、redis key 语义、分窗摘要。

判 `弃` 四条（各经 grep + codegraph callers 复核为零调用者）：
  - `_metagpt_summarize` / `_metagpt_is_related` / `_metagpt_rewrite`：源的 MetaGPTLLM 无模型兜底分支，
    本仓这个职责由 FakeLLM 承担；
  - `get_title`：会话标题在 S8 另有来源；
  - `is_related` / `rewrite`：RAG 查询改写，本仓 rag/ 走 LangChain，有真实消费者再写。
"""
from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from codeharness.configs.settings import settings
from codeharness.const import DEFAULT_MAX_TOKENS, DEFAULT_TOKEN_SIZE
from codeharness.logs import logger
from codeharness.schema import Message
from codeharness.utils.redis import Redis


class BrainMemory(BaseModel):
    history: List[Message] = Field(default_factory=list)
    knowledge: List[Message] = Field(default_factory=list)
    historical_summary: str = ""
    last_history_id: str = ""
    is_dirty: bool = False
    last_talk: Optional[str] = None
    cacheable: bool = True
    llm: Optional[Any] = Field(default=None, exclude=True)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    # ---- 写入 ----
    def add_talk(self, msg: Message):
        msg.role = "user"
        self.add_history(msg)
        self.is_dirty = True

    def add_answer(self, msg: Message):
        msg.role = "assistant"
        self.add_history(msg)
        self.is_dirty = True

    def add_history(self, msg: Message):
        self.history.append(msg)
        self.last_history_id = str(msg.id)
        self.is_dirty = True

    def exists(self, text: str) -> bool:
        return any(m.content == text for m in reversed(self.history))

    def pop_last_talk(self) -> Optional[str]:
        v, self.last_talk = self.last_talk, None
        return v

    def get_knowledge(self) -> str:
        return "\n".join(m.content for m in self.knowledge)

    @property
    def is_history_available(self) -> bool:
        return bool(self.history or self.historical_summary)

    @property
    def history_text(self) -> str:
        """摘要 + 除最后一条外的历史（最后一条是当前问话，由调用方自己带上）"""
        if not self.history and not self.historical_summary:
            return ""
        texts = [self.historical_summary] if self.historical_summary else []
        texts += [m.content for m in self.history[:-1] if isinstance(m, Message)]
        return "\n".join(texts)

    # ---- 溢出判定 ----
    def is_overflow(self) -> bool:
        """working memory 条数超过窗口就该压缩。memory_overflow_size 的口径照源 memory_k=条数"""
        return len(self.history) > settings.memory_overflow_size

    # ---- Redis 往返：整体 JSON 存单个 key ----
    async def loads(self, redis_key: str) -> "BrainMemory":
        if not redis_key:
            return self
        v = await Redis().get(key=redis_key)
        if v:
            loaded = BrainMemory.model_validate_json(v)
            loaded.is_dirty = False
            return loaded
        return self

    async def dumps(self, redis_key: str, timeout_sec: int = 30 * 60) -> bool:
        if not self.is_dirty or not redis_key:
            return False
        if self.cacheable:
            await Redis().set(key=redis_key, data=self.model_dump_json(), timeout_sec=timeout_sec)
        self.is_dirty = False
        return True

    @staticmethod
    def to_redis_key(prefix: str, user_id: str, chat_id: str) -> str:
        return f"{prefix}:{user_id}:{chat_id}"

    async def set_history_summary(self, history_summary: str, redis_key: str):
        """摘要取代历史：这是压缩发生的地方，也是 is_dirty 唯一的正常出口"""
        if self.historical_summary == history_summary:
            if self.is_dirty:
                await self.dumps(redis_key=redis_key)
            return
        self.historical_summary = history_summary
        self.history = []
        await self.dumps(redis_key=redis_key)

    # ---- 摘要 ----
    async def summarize(self, llm, redis_key: str = "", max_words: int = 200,
                        keep_language: bool = False, limit: int = -1) -> str:
        """把 history(+ 旧摘要) 压成一段新摘要，并落到 redis_key。limit 是给「还不够长」的短路。"""
        texts = ([self.historical_summary] if self.historical_summary else []) + [m.content for m in self.history]
        text = "\n".join(texts)
        if limit > 0 and len(text) < limit:
            return text
        self.llm = llm
        summary = await self._summarize(text=text, max_words=max_words, keep_language=keep_language, limit=limit)
        if summary:
            await self.set_history_summary(history_summary=summary, redis_key=redis_key)
            return summary
        raise ValueError(f"text too long: {len(text)}")

    async def _summarize(self, text: str, max_words: int = 200, keep_language: bool = False,
                         limit: int = -1) -> str:
        """长文本先分窗各摘一次，再合并重摘，直到装进一个窗口。max_count 是防死循环的闸。"""
        max_count = 100
        text_length = len(text)
        if limit > 0 and text_length < limit:
            return text
        summary = ""
        while max_count > 0:
            if text_length < DEFAULT_MAX_TOKENS:
                return await self._get_summary(text=text, max_words=max_words, keep_language=keep_language)
            padding = 20 if DEFAULT_MAX_TOKENS > 20 else 0
            windows = self.split_texts(text, window_size=DEFAULT_MAX_TOKENS - padding)
            part_max_words = min(int(max_words / len(windows)) + 1, 100)
            summaries = [await self._get_summary(text=w, max_words=part_max_words, keep_language=keep_language)
                         for w in windows]
            if len(summaries) == 1:
                return summaries[0]
            text = "\n".join(summaries)
            text_length = len(text)
            max_count -= 1
        return summary

    async def _get_summary(self, text: str, max_words: int = 20, keep_language: bool = False) -> str:
        if len(text) < max_words:
            return text
        system_msgs = [
            "You are a tool for summarizing and abstracting text.",
            f"Return the summarized text to less than {max_words} words.",
        ]
        if keep_language:
            system_msgs.append("The generated summary should be in the same language as the original text.")
        response = await self.llm.aask(text, system_msgs=system_msgs, tag="memory_summary")
        logger.debug(f"summary rsp: {response}")
        return response

    @staticmethod
    def split_texts(text: str, window_size: int) -> List[str]:
        """滑动窗口切分：每窗少算 padding 就是窗口间的重叠。"""
        if window_size <= 0:
            window_size = DEFAULT_TOKEN_SIZE
        total_len = len(text)
        if total_len <= window_size:
            return [text]
        padding = 20 if window_size > 20 else 0
        windows, idx, step = [], 0, window_size - padding
        while idx < total_len:
            if window_size + idx > total_len:
                windows.append(text[idx:])
                break
            windows.append(text[idx:idx + window_size])
            idx += step
        return windows
