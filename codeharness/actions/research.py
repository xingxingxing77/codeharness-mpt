"""Research。= 源 research.py(343) 三件套（CollectLinks/WebBrowseAndSummarize/ConductResearch）的 `改`：
prompt 常量由构建脚本自源**逐字节摘取**；管线三腿齐——①主题→关键词→搜索→拆解子问题（源 CollectLinks），
②子问题→搜索→LLM 排序→逐结果摘要（源 WebBrowseAndSummarize），③汇总成报告（源 ConductResearch）。
两条硬约束：联网**只走 S4 工具注册表的 `search_internet`**（源在 Action 里持 SearchEngine 直发 HTTP，
判 `改` 的核心就是换这个接缝）；playwright 浏览器腿判「推迟」（施工2 §S4 实测三条），②的"取正文"降级为
搜索摘要文本——降级只影响取材深度，不砍管线。源的模型侧 token 裁剪（reduce_message_length）照留，
单段整包进（源 gen_msg 的多段收缩到真上下文吃紧时再上 generate_prompt_chunk）。

产物：报告全文进 Message.content；instruct_content 带来源链接（前端引用、APA 溯源用）。
"""
import asyncio
import re
from datetime import datetime

from pydantic import TypeAdapter

from codeharness.base.action import BaseAction
from codeharness.configs.settings import settings
from codeharness.logs import logger
from codeharness.schema import Message
from codeharness.tools import search_internet
from codeharness.utils.common import OutputParser
from codeharness.utils.text import reduce_message_length

# 源 :19：报告腿 system 的语言后缀（ConductResearch.__get_system_prompt 的原料，language 默认照源 en-us）
LANG_PROMPT = "Please respond in {language}."

RESEARCH_BASE_SYSTEM = """You are an AI critical thinker research assistant. Your sole purpose is to write well \
written, critically acclaimed, objective and structured reports on the given text."""

RESEARCH_TOPIC_SYSTEM = "You are an AI researcher assistant, and your research topic is:\n#TOPIC#\n{topic}"

SEARCH_TOPIC_PROMPT = """Please provide up to 2 necessary keywords related to your research topic for Google search. \
Your response must be in JSON format, for example: ["keyword1", "keyword2"]."""

SUMMARIZE_SEARCH_PROMPT = """### Requirements
1. The keywords related to your research topic and the search results are shown in the "Search Result Information" section.
2. Provide up to {decomposition_nums} queries related to your research topic base on the search results.
3. Please respond in the following JSON format: ["query1", "query2", "query3", ...].

### Search Result Information
{search_results}
"""

COLLECT_AND_RANKURLS_PROMPT = """### Topic
{topic}
### Query
{query}

### The online search results
{results}

### Requirements
Please remove irrelevant search results that are not related to the query or topic.
If the query is time-sensitive or specifies a certain time frame, please also remove search results that are outdated or outside the specified time frame. Notice that the current time is {time_stamp}.
Then, sort the remaining search results based on the link credibility. If two results have equal credibility, prioritize them based on the relevance.
Provide the ranked results' indices in JSON format, like [0, 1, 3, 4, ...], without including other words.
"""

WEB_BROWSE_AND_SUMMARIZE_PROMPT = """### Requirements
1. Utilize the text in the "Reference Information" section to respond to the question "{query}".
2. If the question cannot be directly answered using the text, but the text is related to the research topic, please provide \
a comprehensive summary of the text.
3. If the text is entirely unrelated to the research topic, please reply with a simple text "Not relevant."
4. Include all relevant factual information, numbers, statistics, etc., if available.

### Reference Information
{content}
"""


CONDUCT_RESEARCH_PROMPT = """### Reference Information
{content}

### Requirements
Please provide a detailed research report in response to the following topic: "{topic}", using the information provided \
above. The report must meet the following requirements:

- Focus on directly addressing the chosen topic.
- Ensure a well-structured and in-depth presentation, incorporating relevant facts and figures where available.
- Present data and findings in an intuitive manner, utilizing feature comparative tables, if applicable.
- The report should have a minimum word count of 2,000 and be formatted with Markdown syntax following APA style guidelines.
- Include all source URLs in APA format at the end of the report.
"""


def _json_list(text: str, fallback: list[str]) -> list[str]:
    try:
        return TypeAdapter(list[str]).validate_python(OutputParser.extract_struct(text, list))
    except Exception as e:
        logger.warning(f"JSON 列表解析失败，用兜底: {e}")
        return fallback


class Research(BaseAction):
    """三腿研究管线（源三件合进一个 Action：本仓无 Action 间编排需求，源拆开是为它的 Role 循环）。"""

    async def run(self, msg: Message) -> Message:
        topic = msg.content.strip()
        system_text = RESEARCH_TOPIC_SYSTEM.format(topic=topic)

        # ---- 腿1（源 CollectLinks）：关键词 → 搜索 → 拆解子问题 ----
        keywords = _json_list(await self._aask(SEARCH_TOPIC_PROMPT, [system_text]), [topic])[:2]
        k_results = await asyncio.gather(*(search_internet.ainvoke({"query": k}) for k in keywords))
        blob = "\n".join(f"#### Keyword: {k}\n Search Result: {r}\n" for k, r in zip(keywords, k_results))
        prompt = reduce_message_length((p for p in [SUMMARIZE_SEARCH_PROMPT.format(
            decomposition_nums=2, search_results=blob)]), settings.llm.model, system_text,
            settings.llm.max_token)
        queries = _json_list(await self._aask(prompt, [system_text]), keywords)

        # ---- 腿2（源 WebBrowseAndSummarize；浏览腿推迟 → 取搜索摘要文本）----
        per_query = {}
        for q in queries[:2]:
            results = await search_internet.ainvoke({"query": q})
            ranked = await self._rank(topic, q, results)
            summaries = []
            for block in self._blocks(ranked):
                s = await self._aask(WEB_BROWSE_AND_SUMMARIZE_PROMPT.format(query=q, content=block),
                                     [system_text])
                if "Not relevant." not in s:            # 源 :292 的过滤语义照留
                    summaries.append(s)
            per_query[q] = summaries

        # ---- 腿3（源 ConductResearch）----
        content = "\n\n".join(f"### {q}\n{chr(10).join(s)}" for q, s in per_query.items() if s)
        language = (msg.instruct_content or {}).get("language", "en-us")   # 源 ConductResearch.run 默认值
        report_system = " ".join((system_text, LANG_PROMPT.format(language=language)))  # 源 :343 __get_system_prompt
        report = await self._aask(CONDUCT_RESEARCH_PROMPT.format(content=content, topic=topic), [report_system])
        links = sorted({w for s in per_query.values() for line in s for w in line.split()
                        if w.startswith("http")})
        return Message(content=report, role="assistant", cause_by=self.name, sent_from="Researcher",
                       instruct_content={"links": links[:20]}, instruct_schema="ResearchOutput")

    async def _rank(self, topic: str, query: str, results: str) -> str:
        """源 _search_and_rank_urls 的排序腿：LLM 回索引 JSON；解析不动就回原序（排序是增益不是闸门）。"""
        prompt = COLLECT_AND_RANKURLS_PROMPT.format(topic=topic, query=query, results=results,
                                                    time_stamp=datetime.now().strftime("%Y-%m-%d"))
        idx = _json_list(await self._aask(prompt), [])
        blocks = self._blocks(results)
        picked = [blocks[i] for i in idx if isinstance(i, int) and 0 <= i < len(blocks)]
        return "\n\n".join(picked) if picked else results

    @staticmethod
    def _blocks(results: str) -> list[str]:
        """把工具出口的「N. 标题/链接/摘要」块还原成列表（工具返回是 str，这里只切文本不再发 HTTP）。"""
        out, cur = [], []
        for line in results.splitlines():
            if re.match(r"^\d+\. ", line.strip()) and cur:
                out.append("\n".join(cur))
                cur = [line]
            else:
                cur.append(line)
        if cur:
            out.append("\n".join(cur))
        return out or ([results] if results.strip() else [])
