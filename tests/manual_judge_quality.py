"""S9.2 质量评测·真钱通道（不进门禁）：用 SimpleScorer（源模板逐字，接真实 LLM）评一场
已完成会话的 SOP 产物质量——9.2 的「perfect_judges → 接真实 LLM，评 SOP 产物质量」。

用法：
  cd /e/Codeharness && PYTHONPATH=/e/Codeharness PYTHONIOENCODING=utf-8 F:/anaconda/python.exe \
      tests/manual_judge_quality.py <session_tag> [idea]

idea 缺省取 PRD 的 original_requirements；逐件打分（prd/design/tasks/summary，在场才评），
追加写入 storage/benchmark/s9_judged_quality.json。评分一次真模型调用一件，费用走 CostManager 台账。
"""
import asyncio
import datetime
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


async def go(sid: str, idea: str, scorer) -> dict:
    import codeharness.runtime as rt
    from codeharness.const import DocName, RepoName
    from codeharness.document_store.artifact_store import ArtifactStore

    rt.CURRENT_PROJECT.set(sid)
    store = ArtifactStore.active()
    prd = await store.get(RepoName.PRD, DocName.PRD)
    arts = {}
    if prd is not None:
        if not idea:
            idea = json.loads(prd.content).get("original_requirements", "")
        arts["prd"] = prd.content
        for name, doc in (("design", DocName.DESIGN_JSON), ("tasks", DocName.TASKS),
                          ("summary", DocName.CODE_SUMMARY)):
            d = await store.get(RepoName.DOCS, doc)
            if d is not None:
                arts[name] = d.content
    else:
        # RoleZero/动态线没有 SOP 文档产物：评代码件（src+tests 仓；空则退会话根目录 *.py，
        # RoleZero 经 editor 写的文件不进 SRC 仓——各取前 2，按文件名序确定性截断）
        if not idea:
            raise SystemExit(f"会话 {sid} 无 prd.json（RoleZero 线），必须显式给 idea 参数")
        for tag, repo in (("src", RepoName.SRC), ("tests", RepoName.TESTS)):
            for name in sorted(store.all_files(repo))[:2]:
                d = await store.get(repo, name)
                if d is not None:
                    arts[f"{tag}:{name}"] = d.content
        if not arts:
            from pathlib import Path
            for p in sorted(Path(store.root).glob("*.py"))[:4]:
                arts[f"file:{p.name}"] = p.read_text(encoding="utf-8", errors="replace")

    rows = []
    for name, content in arts.items():
        score = await scorer.evaluate(idea, content)
        rows.append({"artifact": name, "val": score.val, "reason": score.reason})
        print(f"  {name}: {score.val}/10 —— {score.reason[:80]}")
    return {"session": sid, "created": datetime.datetime.now().isoformat(timespec="seconds"),
            "idea": idea[:120], "scores": rows}


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    sid = sys.argv[1]
    idea = sys.argv[2] if len(sys.argv) > 2 else ""

    from codeharness.exp_pool.scorers import SimpleScorer
    from codeharness.provider.gateway import LLMGateway
    gw = LLMGateway()               # 网关在这里建、注入 scorer——费用台账才落在同一实例
    run = asyncio.run(go(sid, idea, SimpleScorer(llm=gw)))

    cm = gw.cost_manager
    run["cost"] = {"total_cost": cm.total_cost, "prompt_tokens": cm.total_prompt_tokens,
                   "completion_tokens": cm.total_completion_tokens}

    out = ROOT / "storage" / "benchmark" / "s9_judged_quality.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    history = json.loads(out.read_text(encoding="utf-8")) if out.exists() else []
    history.append(run)
    out.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    vals = [r["val"] for r in run["scores"]]
    print(f"FINAL: {run['session']} 件均分 {sum(vals) / len(vals):.1f}/10 "
          f"({len(vals)} 件, {run['cost']['total_cost']:.3f})；已追加 s9_judged_quality.json"
          f"（第 {len(history)} 次）")


if __name__ == "__main__":
    main()
