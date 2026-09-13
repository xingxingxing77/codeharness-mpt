"""产物仓：workspace/{project}/docs|src|tests 目录约定 + Document 存取（源 utils/file_repository.py 语义）。"""
from pathlib import Path
import asyncio
from codeharness.configs.settings import settings
from codeharness.const import RepoName
from codeharness.schema import Document


class ArtifactStore:
    SUBDIRS = (RepoName.DOCS, RepoName.PRD, RepoName.SRC, RepoName.TESTS, RepoName.TEST_OUTPUTS, RepoName.RESOURCES)

    def __init__(self, project_id: str | None = None):
        from codeharness.runtime import CURRENT_PROJECT
        self.root = Path(settings.workspace_root) / (project_id or CURRENT_PROJECT.get())
        for d in self.SUBDIRS:
            (self.root / d).mkdir(parents=True, exist_ok=True)

    @classmethod
    def active(cls) -> "ArtifactStore":
        """会话内使用：目录名由 runner 经 CURRENT_PROJECT 注入，与 session.workspace 同名——
        这是前端文件树（/{sid}/workspace/files）能看到产物的关键（第 10 步 §2）"""
        return cls()

    def _path(self, subdir: str, filename: str) -> Path:
        p = self.root / subdir / filename
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    async def save(self, subdir: str, doc: Document, filename: str | None = None) -> Document:
        fn = filename or doc.filename
        path = self._path(subdir, fn)
        await asyncio.to_thread(path.write_text, doc.content, encoding="utf-8")
        doc.root_path, doc.filename = subdir, fn
        # 报道接线（第 10 步 §1.2）：docs*→Docs 块（path 事件→前端文件卡片），src/tests→Editor 块（代码卡）
        from codeharness.report import BlockReporter, BlockType
        rep = BlockReporter(BlockType.DOCS if subdir.startswith("docs") else BlockType.EDITOR)
        if subdir.startswith("docs"):
            await rep.meta({"type": "prd" if fn.startswith("prd") else "doc"})
            await rep.path(self.root / subdir / fn)
        else:
            await rep.meta({"type": "code", "filename": fn})
            await rep.document({"filename": fn, "content": doc.content})
        await rep.close()
        return doc

    async def get(self, subdir: str, filename: str) -> Document | None:
        path = self._path(subdir, filename)
        if not path.exists():
            return None
        content = await asyncio.to_thread(path.read_text, encoding="utf-8")
        return Document(root_path=subdir, filename=filename, content=content)

    def all_files(self, subdir: str) -> list[str]:
        base = self.root / subdir
        return [str(p.relative_to(base)) for p in base.rglob("*") if p.is_file()] if base.exists() else []
