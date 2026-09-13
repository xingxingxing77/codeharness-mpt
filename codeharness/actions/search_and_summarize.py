"""SearchAndSummarize：源 actions/search_and_summarize.py 语义——搜索 + 基于结果的摘要
（源项目可注入自定义 SearchEngine；新栈统一走 search_internet 工具）。"""
from codeharness.base.action import BaseAction
from codeharness.schema import Message
from codeharness.tools import search_internet

SEARCH_SUMMARIZE_PROMPT = """Context: {context}
Question: {question}
Answer the question using the context. If the context is insufficient, say so honestly."""


class SearchAndSummarize(BaseAction):
    async def run(self, msg: Message) -> Message:
        raw = await search_internet.ainvoke({"query": msg.content[:200]})
        answer = await self._aask(
            SEARCH_SUMMARIZE_PROMPT.format(context=raw, question=msg.content))
        return Message(content=answer, role="assistant", cause_by=self.name, sent_from="Searcher")
