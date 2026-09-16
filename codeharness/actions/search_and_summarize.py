"""SearchAndSummarize。= 源 search_and_summarize.py(147) 的 `改`：prompt 常量构建脚本逐字节摘取；
搜索引擎腿换 S4 工具注册表的 `search_internet`（源在 Action 里持 SearchEngine 直发 HTTP——判 `改` 点），
对话历史（context[:-1]）照源进 PROMPT。sales 变体系统提示（源 :43-63）判 `推迟`：
本仓 Sales 角色的差异化只是 system 文案，等 2f 交互批一并处理。
"""
from codeharness.base.action import BaseAction
from codeharness.logs import logger
from codeharness.schema import Message
from codeharness.tools import search_internet

SEARCH_AND_SUMMARIZE_SYSTEM = """### Requirements
1. Please summarize the latest dialogue based on the reference information (secondary) and dialogue history (primary). Do not include text that is irrelevant to the conversation.
- The context is for reference only. If it is irrelevant to the user's search request history, please reduce its reference and usage.
2. If there are citable links in the context, annotate them in the main text in the format [main text](citation link). If there are none in the context, do not write links.
3. The reply should be graceful, clear, non-repetitive, smoothly written, and of moderate length, in {LANG}.

### Dialogue History (For example)
A: MLOps competitors

### Current Question (For example)
A: MLOps competitors

### Current Reply (For example)
1. Alteryx Designer: <desc> etc. if any
2. Matlab: ditto
3. IBM SPSS Statistics
4. RapidMiner Studio
5. DataRobot AI Platform
6. Databricks Lakehouse Platform
7. Amazon SageMaker
8. Dataiku
"""

SEARCH_AND_SUMMARIZE_SYSTEM_EN_US = SEARCH_AND_SUMMARIZE_SYSTEM.format(LANG="en-us")

SEARCH_AND_SUMMARIZE_PROMPT = """
### Reference Information
{CONTEXT}

### Dialogue History
{QUERY_HISTORY}
{QUERY}

### Current Question
{QUERY}

### Current Reply: Based on the information, please write the reply to the Question


"""


class SearchAndSummarize(BaseAction):
    async def run(self, msg: Message) -> Message:
        query = msg.content
        raw = await search_internet.ainvoke({"query": query[:200]})
        if raw.startswith("[搜索"):
            logger.warning("搜索引擎降级，仍按已有上下文作答")
        history = (msg.instruct_content or {}).get("history", "")
        answer = await self._aask(SEARCH_AND_SUMMARIZE_PROMPT.format(
            ROLE=self.prefix, CONTEXT=raw, QUERY_HISTORY=history, QUERY=query))
        return Message(content=answer, role="assistant", cause_by=self.name, sent_from=msg.sent_from or "Searcher")
