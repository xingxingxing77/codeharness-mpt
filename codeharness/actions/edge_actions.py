"""ExtractReadMe（源 actions/extract_readme.py:124 的 `改`）：README 四要素提取并**写进 SPO 图**。
源面是四段 aask（summary/install/config/usage，system 消息逐字搬）+ DiGraphRepository 四谓词 insert
+ save；本仓改的只有落点——git_repo.workdir 换会话根（`docs/graph_repo/readme.json`，与 RebuildClassView
同一图谱目录），README 正文从消息/指定路径取（源从仓库根目录扫 README.*）。
其余五只（WriteDocstring/WriteDesignReview/WriteReview/AnalyzeRequirements/GenerateQuestions）
2026-09-16 对账后删除：源侧消费者只有自身 fire CLI 与 tests（判定表 §一 edge_actions 行改判），
留下就是把源的死分支复制成我的死分支（§一 第二条纪律，先例 summarizing.py）。
本件去向（接线台账 #13）：N2 扩展点示例件——外部仓库导入是工具面不是会话 SOP，装配方在 ext_api。"""
from pathlib import Path

from codeharness.base.action import BaseAction
from codeharness.const import GRAPH_REPO_FILE_REPO
from codeharness.logs import logger
from codeharness.schema import Message


class ExtractReadMe(BaseAction):
    """源 LearnReadMe：summary/installation/configuration/usages 四段逐字 system，入 SPO 图。"""
    install_to_path: str = "/TO/PATH"          # 源 :32 默认值逐字

    async def run(self, msg: Message) -> Message:
        fic = msg.instruct_content or {}
        readme = fic.get("readme_content") or msg.content
        if not readme and fic.get("readme_path"):
            p = Path(fic["readme_path"])
            readme = p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""
        if not readme.strip():
            return Message(content="缺少 README 正文，未提取", role="assistant", cause_by=self.name)

        from codeharness.runtime import CURRENT_PROJECT, session_root
        from codeharness.utils.di_graph_repository import DiGraphRepository
        from codeharness.utils.graph_repository import GraphKeyword
        project = fic.get("repo") or CURRENT_PROJECT.get() or "session"
        graph_path = session_root() / GRAPH_REPO_FILE_REPO / "readme.json"
        graph_path.parent.mkdir(parents=True, exist_ok=True)
        graph_db = (await DiGraphRepository.load_from(str(graph_path)) if graph_path.exists()
                    else DiGraphRepository(name=graph_path.stem, root=graph_path.parent))

        # 四段 system 消息与源 :60-108 逐字（install 段里的 {install_to_path} 也是源的 f-string 口径）
        summary = await self._aask(readme, system_msgs=[
            "You are a tool can summarize git repository README.md file.",
            "Return the summary about what is the repository."])
        install = await self._aask(readme, system_msgs=[
            "You are a tool can install git repository according to README.md file.",
            "Return a bash code block of markdown including:\n"
            f"1. git clone the repository to the directory `{self.install_to_path}`;\n"
            f"2. cd `{self.install_to_path}`;\n3. install the repository."])
        configuration = await self._aask(readme, system_msgs=[
            "You are a tool can configure git repository according to README.md file.",
            "Return a bash code block of markdown object to configure the repository if necessary, otherwise return"
            " a empty bash code block of markdown object"])
        usage = await self._aask(readme, system_msgs=[
            "You are a tool can summarize all usages of git repository according to README.md file.",
            "Return a list of code block of markdown objects to demonstrates the usage of the repository."])

        for predicate, value in ((GraphKeyword.HAS_SUMMARY, summary), (GraphKeyword.HAS_INSTALL, install),
                                 (GraphKeyword.HAS_CONFIG, configuration), (GraphKeyword.HAS_USAGE, usage)):
            await graph_db.insert(subject=project, predicate=predicate, object_=value)
        await graph_db.save(graph_path.parent)   # 源语义：save 收目录，文件=目录/name.json
        logger.info(f"ExtractReadMe: {project} 四谓词入图 → {graph_path}")
        return Message(content=f"README 四要素已入图谱: {graph_path.name}", role="assistant",
                       cause_by=self.name,
                       instruct_content={"repo": project, "summary": summary, "installation": install,
                                         "configuration": configuration, "usages": usage},
                       instruct_schema="ReadmeSummary")
