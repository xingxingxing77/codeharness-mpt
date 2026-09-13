"""Research：源 actions/research.py 的 CollectLinks + WebSearchAndSummarize 语义 →
search_internet 工具 + 总结两段（链接列表保留，供前端引用）。"""
from codeharness.base.action import BaseAction
from codeharness.schema import Message
from codeharness.tools import search_internet

RESEARCH_SYSTEM_PROMPT = """You are a professional researcher. Provide comprehensive, well-structured research
with cited sources. Answer in the same language as the requirement."""


class Research(BaseAction):
    async def run(self, msg: Message) -> Message:
        raw = await search_internet.ainvoke({"query": msg.content[:200]})
        summary = await self._aask(
            f"研究主题：{msg.content}\n\n搜索结果（含降级提示时按已有知识作答并注明）：\n{raw}\n\n"
            "输出：1) 关键发现（分点）2) 结论 3) 来源链接列表")
        return Message(content=summary, role="assistant", cause_by=self.name, sent_from="Researcher")
