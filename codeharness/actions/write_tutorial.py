"""WriteTutorial：源 actions/write_tutorial.py 的 WriteDirectory + WriteContent 两段语义。
第一段出 Markdown 目录（结构化），第二段按目录逐节展开成完整教程文档。"""
import json
from pydantic import BaseModel, Field
from codeharness.base.action import BaseAction
from codeharness.schema import Message, Document
from codeharness.const import RepoName
from codeharness.document_store.artifact_store import ArtifactStore

DIRECTORY_PROMPT = """You are a professional tutorial assistant. Given a topic, produce a tutorial directory
in json: {{"title": "...", "sections": [{{"index": 1, "title": "...", "description": "..."}}]}}
Constraints: 4-8 sections, progressive difficulty, Markdown-friendly titles."""

CONTENT_PROMPT = """You are a professional tutorial writer. Write section {index}: "{title}" ({description})
of the tutorial "{title_all}". Requirements: Markdown syntax, neat standardized layout, include runnable examples
where appropriate. Write in {language}. Output ONLY this section's Markdown content."""


class TutorialDirectory(BaseModel):
    """= 源 WriteDirectory 的结构化产物"""
    title: str = ""
    sections: list[dict] = Field(default_factory=list)   # [{index, title, description}]


class WriteDirectory(BaseAction):
    output_schema = TutorialDirectory

    async def run(self, msg: Message) -> Message:
        d: TutorialDirectory = await self._structured(
            f"{self.prefix}\nTopic: {msg.content}", schema=TutorialDirectory)
        return Message(content=d.model_dump_json(), role="assistant", cause_by=self.name,
                       sent_from="Stitch", instruct_content=d.model_dump(), instruct_schema="TutorialDirectory")


class WriteContent(BaseAction):
    """按目录逐节写正文（msg.instruct_content = {"directory": {...}, "language": "中文"}）"""
    async def run(self, msg: Message) -> Message:
        d = msg.instruct_content or {}
        language = d.get("language", "中文")
        title = d.get("title", "Tutorial")
        store = ArtifactStore.active()
        parts = [f"# {title}"]
        for sec in d.get("sections", []):
            body = await self._aask(CONTENT_PROMPT.format(
                index=sec.get("index"), title=sec.get("title"),
                description=sec.get("description", ""), title_all=title, language=language))
            parts.append(f"## {sec.get('index')}. {sec.get('title')}\n\n{body}")
        doc = await store.save(RepoName.RESOURCES, Document(filename="tutorial.md", content="\n\n".join(parts)))
        return Message(content=f"教程已完成: {doc.root_relative_path}", role="assistant",
                       cause_by=self.name, sent_from="Stitch")
