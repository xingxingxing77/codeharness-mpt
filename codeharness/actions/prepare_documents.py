"""PrepareDocuments：立项建 workspace（源 actions/prepare_documents.py 语义）+ 需求落盘。"""
from codeharness.base.action import BaseAction
from codeharness.schema import Message, Document
from codeharness.const import RepoName, DocName
from codeharness.document_store.artifact_store import ArtifactStore


class PrepareDocuments(BaseAction):
    async def run(self, msg: Message) -> Message:
        store = ArtifactStore.active()
        await store.save(RepoName.DOCS, Document(filename=DocName.REQUIREMENT, content=msg.content))
        return msg      # 原样放行（cause_by=UserRequirement），供 SOP 表路由到 PM
