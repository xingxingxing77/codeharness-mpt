"""C24 判据的真读数工装：**检索能不能被模型主动调**——不是预取多一条，是它想说「我再查一次」时有这个口。

两场各取一个读数（判别场问两件事、对照场问预取就够答的那件），**判据三件**（09-24 用户拍定改的，
理由见 `main()` 里那段注释）：
  ① 每一次工具调用都必须**真召回到切片且带出处**（不许空转、不许丢出处）；
  ② 至少有一场是模型**自发**调的（口通了还得真有人用，只挂在名册里不算通电）；
  ③ 预取够不够用、这一场查了几次——只作**行为读数**打印，不做 pass/fail。
原判据里「对照场零调用」那半条被实测否掉（预取已带回答案时 thinking 模型仍会自己核实 1~3 次）；
「零调用」这个形状断言仍然成立、留在确定性的 `s15 t15③`（FakeLLM 脚本，量的是形状不是模型脾气）。

花钱的规矩（`plan/PLAN.md` §3 那张卡片批的就是这条）：
  · **硬闸 ¥2.0**（StepFun 侧累计，`LLM_SPEND_GATE_CNY` 可改小做预量），闸装在工装里、发钱之前判，
    撞闸就印「已花 / 跑到哪一场哪一轮」并非零退出。**不许自己抬闸**，要改档回来摊给用户。
  · 先一发最小调用探金额（A4 那轮的姿势：一句 28 字问答实测 ¥0.27，thinking 模型按内部迭代计费），
    读数不合理就别往下跑。
  · 向量与精排走 `.env` 那两台（目录铁律 28）：**不许为省额度换回本机 bge-m3**，那丢的是读数资格。

跑法（Qdrant 要在 6333：`docker start codeharness-qdrant`）：
  cd /e/Codeharness && PYTHONPATH=/e/Codeharness:/e/Codeharness/logs PYTHONIOENCODING=utf-8 \\
    PYTHONDONTWRITEBYTECODE=1 REDIS__DB=15 LANGFUSE__ENABLED=0 NO_PROXY=127.0.0.1,localhost,::1 \\
    F:/anaconda/python.exe -B tests/manual_c24_requery.py --probe-only
  ... 去掉 --probe-only 跑两场；--only discriminate|control 单跑一场

**C24 未验②（「工具多给几条会不会改变核实次数」）用同一份工装量**：`--tool-k 6 --only discriminate`
跑一档，再不带该参数跑同档基线，两场的「工具调用次数 / 每次带回几份多少字 / ¥」对起来读。
这一档的 pass/fail 只有两条（都是机械的）：旋钮落在**工具那条腿**、预取那条**不许被牵连**；
「这场一次都没调」在这一档是**读数**不是红（判红＝拿模型脾气当验收，正是 09-24 被否掉的那半条）。
拧的是工装不是产品——产品那档今天没有配置项，为一次测量加一档配置是反过来的。

**这一份不算门禁**：挂在真服务与真凭据上，服务/端点缺席直接 exit 非 0 说「没跑成」。
只碰自己的集合 `c24requery`（跑完 drop）与 `workspace/c24_*`，不碰生产集合、不碰 db0、不碰 `.env`。
"""
import argparse
import asyncio
import json
import os
import sys
import time
from contextvars import ContextVar
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx                                          # noqa: E402

from codeharness.const import RequirementTag          # noqa: E402
from codeharness.runtime import CURRENT_PROJECT, CURRENT_USER  # noqa: E402
from codeharness.schema import Message                # noqa: E402

GATE_CNY = float(os.environ.get("LLM_SPEND_GATE_CNY", "2.0"))
COLL = "c24requery"
USER, PROJ_D, PROJ_C = "u_c24", "c24_disc", "c24_ctrl"

# 判别场是**真两跳**：第一份里只留一个指针（「超出部分按《费用核销细则》的口径处理」），
# 真正的答案在另一份里，且**用词与用户那句话完全不重叠**（用户说「机场建设费和燃油附加」，
# 细则写「代收费」「不予核销」）⇒ 拿用户原话做的预取召不回细则那一份，模型必须自己换个说法再查。
# 上一版把两件事都写成用户能说的词，结果 k=3 预取一次就把两份都带回来了，模型没理由再查（0 调用）。
DOCS = {
    "会议室.md": "# 会议室使用\n\n会议室按部门提前一天预约，冲突时以发起人所在楼层优先。\n\n"
                 "超时十五分钟未到场则自动释放该时段，投影设备故障找行政值班处理。\n\n"
                 "外部访客进入会议室需登记姓名与来访单位，离开时带走白板笔与纸质材料。",
    "考勤.md": "# 考勤与请假\n\n弹性上班制，核心时段为上午十点到下午四点。\n\n"
               "请假需在系统提单，主管审批后自动同步至考勤记录。\n\n"
               "每月迟到累计三次折算半天事假，迟到十分钟以内不计。",
    "差旅规定.md": "# 差旅规定\n\n住宿按城市分档，一线城市每晚上限五百五十元，其他城市四百元。\n\n"
                   "铁路出行默认二等座，全额报销；商务座需部门负责人事前书面同意。\n\n"
                   "市内交通凭票据按月汇总，单次超过两百元的打车需附行程说明。\n\n"
                   "航空客票按实报销，单程票价上限一千八百元，超出部分按《费用核销细则》的口径处理。\n\n"
                   "发票须在出行结束后三十天内提交，抬头以合同登记主体为准。",
    "费用核销细则.md": "# 费用核销细则\n\n代收费一律不予核销，由经办人自行承担。\n\n"
                       "超出限额的差额部分同样由个人负担，单位不作二次分担。\n\n"
                       "已核销项目事后发现重复申报的，从下一次核销中等额扣回。",
}
# 判别场：问的是「能报多少 + 哪些自己出」，但后半答案的措辞只存在于细则那份里
Q_DISC = ("我出差买了一张两千三百块的机票，另外还付了机场建设费和燃油附加费，"
          "这些钱里公司能给我报多少、哪几笔得我自己掏？")
# 对照场：用词几乎照抄《会议室使用》第一条 ⇒ 预取够用 ⇒ 要求零调用
Q_CTRL = "会议室需要提前多久预约？冲突的时候谁优先？"


def _die(msg: str) -> None:
    sys.exit(f"exit 1：{msg}")


class _Gated:
    """把网关包一层：**发钱之前**先看闸。闸读的是 `CostManager` 的累计人民币桶（只读计量，
    本仓不设预算强制——限额不进 agent 内核，这里是**工装**替自己把关，不是产品行为）。"""

    def __init__(self, gw, gate: float, label: str):
        self._gw, self._gate, self._label = gw, gate, label
        self.turns = 0
        self.prompts: list[str] = []        # 每发的入参原样留着：判「预取到底带没带回那条」要看第一轮

    def _check(self):
        c = self._gw.cost_manager.get_costs()
        self.turns += 1
        print(f"  [{self._label}] 第 {self.turns} 发之前：已花 ¥{c.cost_cny:.4f} / 闸 ¥{self._gate}",
              flush=True)
        if c.cost_cny >= self._gate:
            print(f"  撞闸：停在 {self._label} 第 {self.turns} 发之前"
                  f"（pt={self._gw.cost_manager.total_prompt_tokens} "
                  f"ct={self._gw.cost_manager.total_completion_tokens}）——抬闸要人拍，别改工装")
            sys.exit(3)

    def structured(self, schema):
        inner = self._gw.structured(schema)
        outer = self

        class _S:
            async def ainvoke(self, prompt, **kw):
                outer._check()
                outer.prompts.append(str(prompt))
                return await inner.ainvoke(prompt, **kw)
        return _S()

    def __getattr__(self, k):
        return getattr(self._gw, k)


_TOOL_K: "ContextVar[int]" = ContextVar("c24_tool_k", default=0)


def _install_tool_k(k: int):
    """把**工具那条腿**的 k 拧一档（预取那条不动）——C24 未验②问的是「多给几条会不会少核实一次」。

    拧的是工装而不是产品：产品那档今天没有配置项，为一次测量加一档配置是反过来的（先量，量出
    结论再决定要不要有这一档）。实现只两件：给 `LongTermMemory.recall` 包一层记实收的 k，
    并在 `_TOOL_K` 有值时改它——那把旗只在工具的 coroutine 外面 set（见 `_run_session` 里的 `spy`），
    所以同一个进程、同一次 `asyncio.run` 里的预取那条腿不受影响（每腿实收的 k 由 ks 现证，不靠推断）。
    返回 `(restore, ks)`；`k` 为 0 或与产品同档时直接返回 `(None, [])`，不去包那一层。
    """
    import codeharness.memory.longterm as lt
    ks: list[tuple] = []
    if not k or k == 3:
        return None, ks
    orig = lt.LongTermMemory.recall

    async def patched(self, query, k=5):
        in_tool = _TOOL_K.get()
        ks.append(("tool" if in_tool else "prefetch", self.doc_type, in_tool or k))
        return await orig(self, query, k=in_tool or k)

    lt.LongTermMemory.recall = patched
    return (lambda: setattr(lt.LongTermMemory, "recall", orig)), ks


async def _probe(gw) -> None:
    """一发最小调用：量今天这个 thinking 模型多少钱，再决定跑不跑两场。"""
    from langchain_core.messages import HumanMessage
    t0 = time.time()
    rsp = await gw.ainvoke([HumanMessage(content="报销上限是多少？只回一句：不知道")], tag="c24-probe")
    c = gw.cost_manager.get_costs()
    print(f"  探针一发：¥{c.cost_cny:.4f} / pt={c.total_prompt_tokens} ct={c.total_completion_tokens}"
          f" / {time.time() - t0:.1f}s / 正文 {str(rsp.content)[:40]!r}")
    if c.cost_cny > GATE_CNY / 3:
        _die(f"单发就吃掉闸的三分之一以上（¥{c.cost_cny:.3f} > ¥{GATE_CNY / 3:.2f}）"
             f"——两场跑不完，先回来摊给用户改档，别硬跑")


async def _ingest(embeddings, project: str) -> None:
    """把三份夹具灌进**这一个 project**。
    ⚠ 必须在灌之前把 `CURRENT_PROJECT`/`CURRENT_USER` 设成会话将要用的那一份：`UploadKB` 取的
    就是这两个 ContextVar，而召回按 (doc_type, user, project) 筛。第一版在 `boot()` 里灌、
    到 `_run_session` 才设 → 点落在 `default`/空 project 下，两场召回自然全空——**症状和
    「工具是坏的」长得一模一样，而它其实是 C31 那条「两处各算一次同一个身份」的第三次现身**。"""
    from codeharness.actions.upload_kb import UploadKB
    from codeharness.document_store.qdrant_store import QdrantStore
    tmp = Path("workspace") / "c24_probe_docs"
    tmp.mkdir(parents=True, exist_ok=True)
    files = []
    for name, text in DOCS.items():
        p = tmp / name
        p.write_text(text, encoding="utf-8")
        files.append(str(p))
    tok_p, tok_u = CURRENT_PROJECT.set(project), CURRENT_USER.set(USER)
    try:
        out = await UploadKB(llm=None, store=QdrantStore(), embeddings=embeddings)._call({"files": files})
    finally:
        CURRENT_PROJECT.reset(tok_p)
        CURRENT_USER.reset(tok_u)
    print(f"  灌库[{project}]：写入点 {out['uploaded_count']} 个 / 切片 {out.get('chunk_count')} 块，"
          f"errors={out['errors']}")
    # `uploaded_count` 是**写入点数**（按内容去重后），不是文件数——第一版拿它 == 3 判，当场红成
    # 「三份夹具没全灌进去」，其实三份都进去了（3 份切出 4 个点）。这里只判「没报错且有块」，
    # 三份文档各在不在库里由两场会话的召回结果现证。
    if out["errors"] or out["chunk_count"] < len(DOCS):
        _die(f"夹具没灌干净：{out}")


def _sources(text: str) -> list[str]:
    """从「〔来自 文件名 [第 N 页]〕」里取文件名——预取带回哪几份、工具带回哪几份，都靠它现证。"""
    import re
    return sorted(set(re.findall(r"〔来自 ([^〕]+?)\s*(?:第 \S+ 页)?〕", text)))


async def _run_session(args, project: str, question: str, label: str):
    """真图真 think：RoleZero + 真模型 + 真 Qdrant + 真向量，工具挂在名册里由模型自己决定调不调。"""
    from codeharness.configs.settings import settings
    from codeharness.environment.team_graph import build_team
    from codeharness.memory.longterm import LongTermMemory
    from codeharness.provider.cost import CostManager
    from codeharness.provider.gateway import LLMGateway
    from codeharness.roles.role_zero import RoleZero
    from codeharness.tools import search_knowledge_base

    embeddings = LLMGateway.embeddings()
    cm = CostManager()
    gw = _Gated(LLMGateway(cost_manager=cm), GATE_CNY, label)
    tool_calls = []
    orig = search_knowledge_base.coroutine

    async def spy(query: str) -> str:
        tok = _TOOL_K.set(args.tool_k)            # 只在这一次工具调用里拧 k，出来就摘
        try:
            out = await orig(query=query)
        except BaseException as e:                # 不记这一笔的话：那次调用从读数里**整个消失**
            # （09-25 k=6 那场就出现过「recall 记到一次工具侧调用、而 tool_calls 是空的」——
            #  当时无从判断是模型没调还是工装吞了异常。吞掉的调用会让「核实次数」被读少。）
            rec = {"query": query, "raised": f"{type(e).__name__}: {e}", "sources": [], "chars": 0}
            tool_calls.append(rec)
            print(f"    工具这一发抛了：{rec['raised'][:160]}", flush=True)
            raise
        finally:
            _TOOL_K.reset(tok)
        tool_calls.append({"query": query, "sources": _sources(out), "chars": len(out)})
        return out

    search_knowledge_base.coroutine = spy
    restore_k, ks = _install_tool_k(args.tool_k)   # C24 未验②：只拧工具那条腿的 k，预取不动
    tok_p, tok_u = CURRENT_PROJECT.set(project), CURRENT_USER.set(USER)
    try:
        role = RoleZero({"name": "R", "profile": "p", "goal": question},
                        [search_knowledge_base], gw, max_loops=args.max_loops)
        role.kb = LongTermMemory(embeddings=embeddings, doc_type="kb")
        g = build_team({"R": role}, sop={RequirementTag.USER_REQUIREMENT: ["R"]})
        await g.ainvoke(
            {"messages": [Message(content=question, role="user",
                                  cause_by=RequirementTag.USER_REQUIREMENT, sent_from="user")],
             "memories": {}, "debug_rounds": 0, "team_rounds": 0, "finished": False},
            {"configurable": {"thread_id": f"c24-{label}"}})
    finally:
        search_knowledge_base.coroutine = orig
        if restore_k:
            restore_k()
        CURRENT_PROJECT.reset(tok_p)
        CURRENT_USER.reset(tok_u)
    c = cm.get_costs()
    return {"label": label, "question": question, "tool_calls": tool_calls, "llm_turns": gw.turns,
            "recall_ks": ks, "tool_k": args.tool_k,
            # 第一发的 prompt 里预取带回的那几份——没有它，「首轮资料不够」这句话就没法现证
            "prefetch_sources": _sources(gw.prompts[0]) if gw.prompts else [],
            "cost_cny": round(c.cost_cny, 4), "prompt_tokens": c.total_prompt_tokens,
            "completion_tokens": c.total_completion_tokens,
            "truncated_calls": cm.truncated_calls, "unknown_command_calls": cm.unknown_command_calls}


# 今天判别场里模型自己组织的那句 query（09-25 00:1x 现测），与用户原话同表对比：
# 拿同一句「它会怎么问」去量档，比拿用户原话量更接近真实用法。
Q_MODEL = "代收费包含哪些项目 机场建设费燃油附加费是否属于代收费 机票报销上限"
KS = (3, 6)


async def _recall_table(project: str) -> int:
    """零模型地量「多给几条」这一半：同一句 query 按 k=3 与 k=6 各召一遍，比带回几份、多少字。

    为什么这一格能单独成立：`k` 改的是**一次已经决定要发的检索**的产出，它改不了「要不要发」——
    所以「多给几条会不会少核实一次」里能被决定性证伪的只有前半句（多给是否多带回）。后半句要拿
    多次会话的调用数分布才有形状，单场 0/1 次不构成结论（见 C24 行 09-24 的四场：3/1/0/0 次）。
    """
    from codeharness.document_store.qdrant_store import QdrantStore
    from codeharness.tools import search_knowledge_base

    st = QdrantStore()
    pts, _ = await st.client.scroll(st.collection, limit=100, with_payload=True)
    npts = len(pts)
    print(f"  集合 {st.collection} 里 kb 点共 {npts} 个（档 6 {'够' if npts >= 6 else '不够'}拿满："
          f"不够的话 k=6 与 k=3 的差别天然被库规模封住）")
    tok_p, tok_u = CURRENT_PROJECT.set(project), CURRENT_USER.set(USER)
    rows = []
    try:
        for q in (Q_DISC, Q_MODEL):
            row = {"query": q}
            for k in KS:
                tok = _TOOL_K.set(k)               # 走的是产品那件工具本身，只把档换掉
                try:
                    out = await search_knowledge_base.coroutine(query=q)
                finally:
                    _TOOL_K.reset(tok)
                row[f"k{k}"] = {"份": len(_sources(out)), "字": len(out),
                                "退化": out.startswith("[知识库检索")}
            rows.append(row)
            print(f"    「{q[:26]}…」→ " + " ｜ ".join(
                f"k={k}：{row[f'k{k}']['份']} 份 / {row[f'k{k}']['字']} 字" for k in KS))
    finally:
        CURRENT_PROJECT.reset(tok_p)
        CURRENT_USER.reset(tok_u)
    bad = []
    for row in rows:
        a, b = row[f"k{KS[0]}"], row[f"k{KS[1]}"]
        if a["退化"] or b["退化"]:
            bad.append(f"query「{row['query'][:20]}」有一条腿退化成不可用文案：{row}")
        if b["份"] < a["份"] or b["字"] < a["字"]:
            bad.append(f"档提到 {KS[1]} 反而带回更少（不单调＝筛子或旋钮有洞）：{row}")
    if bad:
        print("  判据未绿：" + "；".join(bad))
        return 2
    print(f"  判据（recall-only）：两档都未退化、k 单调不减；「多给是否多带回」见上表，"
          f"「是否少核实一次」这一档不判（要多次会话的分布，见本文件 docstring）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe-only", action="store_true")
    ap.add_argument("--only", choices=["discriminate", "control"], default="")
    ap.add_argument("--max-loops", type=int, default=3)
    ap.add_argument("--tool-k", type=int, default=0,
                    help="只拧**工具那条腿**的 k（预取仍 k=3）；0 或与产品同档则不干预。"
                         "C24 未验②用它量「多给几条会不会少核实一次」")
    ap.add_argument("--recall-only", action="store_true",
                    help="不跑会话（零模型、零 ¥），只把工具在同几条 query 上按 k=3/6 各召一遍，"
                         "量「多给几条到底多带回多少」——未验②里能被决定性证伪的那一半")
    args = ap.parse_args()

    from codeharness.configs.settings import settings
    try:
        assert httpx.get(f"{settings.qdrant.url}/healthz", timeout=3).status_code == 200
    except Exception as e:
        _die(f"Qdrant 不在线（{settings.qdrant.url}）：{type(e).__name__}: {e}")
    if not settings.llm.api_key:
        _die(".env 里没有 LLM 凭据，本工装不发空请求")

    from codeharness.provider.gateway import LLMGateway
    emb_probe = LLMGateway.embeddings()
    v = asyncio.run(emb_probe.aembed_query("端点探活"))
    if len(v) != settings.embedding.dim or not any(v):
        _die(f"向量端点假在线（{settings.embedding.model}，dim={len(v)}）")
    print(f"  端点：LLM={settings.llm.model} @ {settings.llm.base_url} ｜ "
          f"embedding={settings.embedding.model}（{len(v)} 维）｜ Qdrant={settings.qdrant.url}")
    print(f"  闸：StepFun 累计 ¥{GATE_CNY}（撞了就停手并印已花/跑到哪一轮，抬闸要人拍）")

    prod_prefix = settings.qdrant.collection_prefix
    settings.qdrant.collection_prefix = COLL        # 工具与预取那条读者都从这里现取集合名

    async def boot():
        from codeharness.document_store.qdrant_store import QdrantStore
        from codeharness.provider.gateway import LLMGateway as G
        st = QdrantStore()
        await st.drop()
        emb = G.embeddings()
        for pj in {"discriminate": [PROJ_D], "control": [PROJ_C]}.get(args.only, [PROJ_D, PROJ_C]):
            await _ingest(emb, pj)
        return st

    if not args.recall_only:                        # 探针那一发也是真模型的钱，recall-only 档不付
        asyncio.run(_probe(LLMGateway()))
    if args.probe_only:
        print("只探针，没跑会话（花费见上一行）")
        return 0

    store = asyncio.run(boot())
    if args.recall_only:                            # C24 未验②的决定性那一半：**一分钱模型都不花**
        rc = asyncio.run(_recall_table(PROJ_D if args.only != "control" else PROJ_C))
        asyncio.run(store.drop())
        settings.qdrant.collection_prefix = prod_prefix
        return rc
    results = []
    try:
        if args.only in ("", "discriminate"):
            results.append(asyncio.run(_run_session(args, PROJ_D, Q_DISC, "判别场")))
        if args.only in ("", "control"):
            results.append(asyncio.run(_run_session(args, PROJ_C, Q_CTRL, "对照场")))
    finally:
        asyncio.run(store.drop())
        settings.qdrant.collection_prefix = prod_prefix
        print(f"  清理：集合 {COLL} 已 drop（生产集合前缀 {prod_prefix} 未碰）")

    total = sum(r["cost_cny"] for r in results)
    print("\n读数：")
    for r in results:
        print(f"  {r['label']}｜问：{r['question'][:34]}\n"
              f"    预取带回 {r['prefetch_sources'] or '（空）'} ｜ 工具调用 {len(r['tool_calls'])} 次 "
              f"{r['tool_calls']}\n"
              f"    模型 {r['llm_turns']} 发 / ¥{r['cost_cny']} / pt={r['prompt_tokens']} "
              f"ct={r['completion_tokens']} / 无效调用 截断{r['truncated_calls']} "
              f"未知命令{r['unknown_command_calls']}"
              + (f"\n    每腿实收的 k：{r['recall_ks'] or '（未干预，与产品同档 k=3）'}" if r["tool_k"] else ""))
    print(f"  合计 ¥{total:.4f} / 闸 ¥{GATE_CNY}")

    disc = next((r for r in results if r["label"] == "判别场"), None)
    ctrl = next((r for r in results if r["label"] == "对照场"), None)
    runs = [r for r in results if r]
    bad = []
    for r in runs:
        print(f"  行为读数｜{r['label']}：预取带回 {r['prefetch_sources'] or '（空）'} → 工具 "
              f"{len(r['tool_calls'])} 次 "
              f"{[(c['query'][:22], c['sources'], c['chars']) for c in r['tool_calls']]}")
    # **判据（09-24 用户拍定改的）**：原文那半条「对照组=不需要再查的会话里该工具零调用」被实测否掉
    # ——预取已经把答案带回时，thinking 模型仍会自己核实 1~3 次。改成三条**都能被本工装发出来**的形状：
    #   ① 每一次调用都必须真召回到切片且带出处（不许空转、不许丢出处）；
    #   ② 至少有一场是模型**自发**调的（口通了还得有人用，只挂在名册里不算通电）；
    #   ③ 预取够不够用、这一场查了几次——只作行为读数打印，不做 pass/fail（那是模型行为不是链的对错）。
    # 「零调用」那半条在 FakeLLM 面上仍然成立且留在 `s15 t15③`——脚本是确定的，量的是形状不是脾气。
    for r in runs:
        for c in r["tool_calls"]:
            if not c["sources"] or c["chars"] < 40:
                bad.append(f"{r['label']} 有一次调用没带回带出处的切片：{c}")
    if runs and not any(r["tool_calls"] for r in runs) and not args.tool_k:
        bad.append("两场里模型一次都没主动调用 ⇒ 这个口今天没人用（机制通了但没通电）")
    if args.tool_k:
        # C24 未验②档：这一趟只把两件事判红——旋钮真落到**工具那条腿**、预取那条**不许被牵连**。
        # 「这场一次都没调」在这一档是**读数**（它可能就是「多给几条就少核实一次」的答案），不判红；
        # 代价是这种场次里旋钮无从现证，所以那一条要显式印出来，不悄悄放过。
        legs = [e for r in runs for e in r["recall_ks"]]
        for leg, dt, k in legs:
            want = args.tool_k if leg == "tool" else 3
            if k != want:
                bad.append(f"②那条腿的 k 不对：{leg}/{dt} 实收 {k}，应 {want}——旋钮串到别的腿上了")
        if not any(e[0] == "tool" for e in legs):
            print(f"  ②未现证：k={args.tool_k} 这一档里模型一次都没调工具 ⇒ 旋钮落没落地无从取证，"
                  f"这一趟的结论只能是「这场没核实」，不能写成「多给几条不影响」")
        else:
            print(f"  ②现证：工具那条腿实收 k={args.tool_k}、预取那条仍 k=3"
                  f"（各腿实收见上一行的「每腿实收的 k」）")
    if disc is not None and ctrl is not None and not bad:
        extra = sorted({s for c in disc["tool_calls"] for s in c["sources"]}
                       - set(disc["prefetch_sources"]))
        print(f"  两跳那条没做成 pass/fail（新刻度的 dense 会把同义改写直接桥进预取）："
              f"工具在判别场补出预取之外的文档 = {extra or '无'}")
    if bad:
        print("\n判据未绿：" + "；".join(bad))
        return 2
    print("\n判据：每次调用都真召回到带出处的切片、且至少一场是模型自发调的"
          "（预取够不够与调用次数只作行为读数）"
          if disc and ctrl else "\n单场读数：另一场没跑，本工装按单场结论不算全绿")
    return 0 if disc and ctrl else 2


if __name__ == "__main__":
    sys.exit(main())
