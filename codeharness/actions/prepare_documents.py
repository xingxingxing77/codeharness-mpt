"""PrepareDocuments。= 源 prepare_documents.py(90) 的 `改`：需求落 docs/requirement 并携带
PrepareDocumentsOutput 结构化上下文（源 :76-86）；project_path/prd_filenames 是本仓产物仓口径
（源 ProjectRepo + git 初始化判 `弃`——per-session 目录由 runner 的 session_root 建，
config.update_via_cli/parse_resources 属源 CLI 立项制，本仓会话在 API 层立项，不搬）。
放行语义保留：content 原样带给 WritePRD，cause_by 由 Agent._act 统一改写成动作名。"""
from codeharness.base.action import BaseAction
from codeharness.const import DocName, RepoName
from codeharness.document_store.artifact_store import ArtifactStore
from codeharness.schema import Document, Message


class PrepareDocuments(BaseAction):
    async def run(self, msg: Message) -> Message:
        store = ArtifactStore.active()
        doc = await store.save(RepoName.DOCS, Document(filename=DocName.REQUIREMENT, content=msg.content))
        prd_files = store.all_files(RepoName.PRD)
        return Message(content=msg.content, role=msg.role, cause_by=msg.cause_by, sent_from=msg.sent_from,
                       instruct_content={"project_path": str(store.root),
                                         "requirements_filename": doc.root_relative_path,
                                         "prd_filenames": [f"{RepoName.PRD}/{f}" for f in prd_files]},
                       instruct_schema="PrepareDocumentsOutput")
