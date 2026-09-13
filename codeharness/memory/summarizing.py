"""摘要压缩：源 memory/brain_memory.py（Redis + LLM 滚动摘要）的 30 行等价物。
分工规律照抄源项目：逐字引用的进 Qdrant（longterm），背景理解的进摘要。"""


class SummarizingMemory:
    def __init__(self, llm, keep_recent: int = 20):
        self.llm, self.keep_recent = llm, keep_recent

    async def condense(self, msgs: list) -> list:
        old, recent = msgs[:-self.keep_recent], msgs[-self.keep_recent:]
        if not old:
            return msgs
        summary = await self.llm.aask(
            "总结以下对话，保留关键决策、产物路径与未尽事项：\n"
            + "\n".join(str(m.to_dict()) for m in old), tag="memory_summary")
        from codeharness.schema import Message
        return [Message(content=f"[历史摘要] {summary}", role="system"), *recent]
