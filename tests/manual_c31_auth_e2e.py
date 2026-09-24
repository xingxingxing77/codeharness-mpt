"""C31 未验①的读数：**auth 开 + 真模型 + 真向量 + 真 HTTP**，端到端跑一场真 think。

C31 那条「kb 读写两侧的租户同源」此前只有替身 embedding 的读数（`s15 t14`，零外网），
本工装补的就是那一格缺的「auth 开的真会话」：以真用户注册 → 真 HTTP 灌一份文档（**真百炼**）
→ 以那个人跑一场真 think（**真 StepFun**）→ 召回必须非空且**带出处进模型输入**；
再换一个用户名同 query 必须召不回（隔离没被这条链修坏）。

五格判据（各钉一种坏法）：
  ① 灌库真发生：端点 200、`errors=[]`、`chunk_count>0`（不是替身，向量的 dim 见 ②）；
  ② 写侧租户 = 票上那个人，且向量是**云上那台**的维度（`len(vector)==settings.embedding.dim`
     ⇒ 证明这一场没被悄悄换成 HashEmbeddings，读数才有资格算「真模型下」）；
  ③ auth 开的真会话里，那份文档**召得回来**：首轮 prompt 里出现 `〔来自 <文件名>〕`
     ——这一格才是产品症状的反证（C31 修前是「自己传的文档自己召不回，界面上还看不见错」）；
  ④ 跨租户仍然隔离：同 query 以另一个人召回 **0 条**；
  ⑤ 花费照实打印（¥ / pt / ct / embedding 真 usage token），两道闸都在工装里、**发钱之前**判。

两道闸（撞了就是 exit 3 并印「已花 / 跑到哪一格」；**不许自己抬闸**，要改档回来摊给用户）：
  · `LLM_SPEND_GATE_CNY`（默认 0.30）：StepFun 累计人民币，读 `CostManager` 那个只读计量桶；
  · `EMB_TOKEN_GATE`（默认 20000）：百炼 embedding，按端点回的**真 usage** 累计，不是估算。

跑法（Qdrant 要在 6333：`docker start codeharness-qdrant`）：
  cd /e/Codeharness && PYTHONPATH=/e/Codeharness:/e/Codeharness/logs PYTHONIOENCODING=utf-8 \\
    PYTHONDONTWRITEBYTECODE=1 REDIS__DB=15 LANGFUSE__ENABLED=0 NO_PROXY=127.0.0.1,localhost,::1 \\
    F:/anaconda/python.exe -B tests/manual_c31_auth_e2e.py

**这一份不算门禁**：挂在真服务与真凭据上，服务/端点缺席直接 exit 1 说「没跑成」，不拿绿勾冒充。
只碰自己的集合前缀 `c31e2e`（跑完 drop）与 `workspace/c31_e2e`（跑完删），不碰生产集合、不碰 db0、不碰 `.env`。
"""
import asyncio
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx                                          # noqa: E402
from qdrant_client import models as m                 # noqa: E402

from codeharness.const import RequirementTag          # noqa: E402
from codeharness.runtime import CURRENT_PROJECT, CURRENT_USER  # noqa: E402
from codeharness.schema import Message                # noqa: E402

LLM_GATE = float(os.environ.get("LLM_SPEND_GATE_CNY", "0.30"))
EMB_GATE = int(os.environ.get("EMB_TOKEN_GATE", "20000"))
PREFIX, USER, OTHER, SRC = "c31e2e", "carol", "mallory", "值班与升级.md"
PROJ = "c31_e2e"
DOC = ("# 值班与告警升级\n\n夜间 P1 告警由当班同学先响应，十五分钟内没有确认就升级到二线。\n\n"
       "升级时要在群里带一条记录：已尝试的处置、影响面、当前接手人。\n\n"
       "周末值班表每季度重排一次，排班变更在周会上公示，不以私信为准。")
Q = "夜里十一点收到 P1 告警，我十五分钟内没确认会发生什么？升级的时候要带上什么？"

_emb_spent = 0
_emb_calls = 0


def _die(msg: str) -> None:
    sys.exit(f"exit 1：{msg}")


def _install_embedding_gate() -> None:
    """把 openai 的 embeddings.create 包一层：**发钱之前**看真 usage 累计的闸。
    装在最底层而不是 langchain 那层，是因为灌库那条腿在**应用进程里**跑（端点自己取 embeddings），
    本工装碰不到它——只有这一层能把两场（HTTP 侧 + 召回侧）都收进同一个闸。"""
    global _emb_spent, _emb_calls
    from openai.resources.embeddings import AsyncEmbeddings

    orig = AsyncEmbeddings.create

    async def gated(self, *a, **kw):
        global _emb_spent, _emb_calls
        if _emb_spent >= EMB_GATE:
            sys.exit(f"exit 3：撞 embedding 总量闸 —— 已花 {_emb_spent:,}/{EMB_GATE:,} token，"
                     f"停在第 {_emb_calls + 1} 发之前。抬闸要人拍，别改工装。")
        rsp = await orig(self, *a, **kw)
        _emb_calls += 1
        _emb_spent += int(getattr(rsp.usage, "total_tokens", 0) or 0)
        return rsp

    AsyncEmbeddings.create = gated


async def _payload(store, project: str):
    """读回这个 project 下 kb 点的 (user_id 集合, 每条向量维度) —— ②格的两半都从这儿现证。"""
    flt = m.Filter(must=[m.FieldCondition(key="doc_type", match=m.MatchValue(value="kb")),
                         m.FieldCondition(key="project", match=m.MatchValue(value=project))])
    pts = []
    off = None
    while True:
        batch, off = await store.client.scroll(store.collection, scroll_filter=flt,
                                               limit=32, offset=off, with_payload=True,
                                               with_vectors=True)
        pts += batch
        if not off:
            break
    # 点是**具名向量**（`dense` + `sparse`），`p.vector` 回来是个两键的 dict——直接 `len()` 量到的是
    # 「有几路向量」（上一跑就报出 dims=[2] 这种荒唐读数），要的是 dense 那一路的维数。
    from codeharness.document_store.qdrant_store import DENSE
    dims = sorted({len((p.vector or {}).get(DENSE) or []) for p in pts})
    return sorted({(p.payload or {}).get("user_id") for p in pts}), dims, len(pts)


async def _recall_as(embeddings, user: str, project: str, query: str) -> str:
    """以某个租户召回一次并排成界面那副样子（只读，不发模型请求）。"""
    from codeharness.memory.longterm import LongTermMemory, format_kb_blocks
    tok_u, tok_p = CURRENT_USER.set(user), CURRENT_PROJECT.set(project)
    try:
        return format_kb_blocks(await LongTermMemory(embeddings=embeddings, doc_type="kb")
                                .recall(query, k=3)) or ""
    finally:
        CURRENT_USER.reset(tok_u)
        CURRENT_PROJECT.reset(tok_p)


async def _think_once(user: str, project: str, embeddings, cm):
    """真图真 think 一场：RoleZero + 真模型 + 真 Qdrant + 真向量，kb 订阅挂在角色上（C32 那条）。"""
    from codeharness.environment.team_graph import build_team
    from codeharness.memory.longterm import LongTermMemory
    from codeharness.provider.gateway import LLMGateway
    from codeharness.roles.role_zero import RoleZero
    from manual_c24_requery import _Gated, _sources

    gw = _Gated(LLMGateway(cost_manager=cm), LLM_GATE, "auth开真会话")
    tok_u, tok_p = CURRENT_USER.set(user), CURRENT_PROJECT.set(project)
    role = RoleZero({"name": "R", "profile": "p", "goal": Q}, [], gw, max_loops=2)
    role.kb = LongTermMemory(embeddings=embeddings, doc_type="kb")
    try:
        g = build_team({"R": role}, sop={RequirementTag.USER_REQUIREMENT: ["R"]})
        await g.ainvoke(
            {"messages": [Message(content=Q, role="user",
                                  cause_by=RequirementTag.USER_REQUIREMENT, sent_from="user")],
             "memories": {}, "debug_rounds": 0, "team_rounds": 0, "finished": False},
            {"configurable": {"thread_id": "c31-e2e"}})
    finally:
        CURRENT_USER.reset(tok_u)
        CURRENT_PROJECT.reset(tok_p)
    first = gw.prompts[0] if gw.prompts else ""
    return {"turns": gw.turns, "sources_in_first_prompt": _sources(first),
            "marker_in_first_prompt": "〔来自" in first, "chars_first_prompt": len(first)}


def main() -> int:
    from codeharness.configs.settings import settings
    try:
        assert httpx.get(f"{settings.qdrant.url}/healthz", timeout=3).status_code == 200
    except Exception as e:
        _die(f"Qdrant 不在线（{settings.qdrant.url}）：{type(e).__name__}: {e}")
    if not settings.llm.api_key:
        _die(".env 里没有 LLM 凭据，本工装不发空请求")
    from s15_kb_upload import _AuthEnv
    _install_embedding_gate()

    from codeharness.provider.gateway import LLMGateway
    probe = LLMGateway.embeddings()
    v = asyncio.run(probe.aembed_query("端点探活"))
    if len(v) != settings.embedding.dim or not any(v):
        _die(f"向量端点假在线（{settings.embedding.model}，dim={len(v)}）——不拿假端点跑真读数")
    keep_prefix = settings.qdrant.collection_prefix
    settings.qdrant.collection_prefix = PREFIX
    print(f"  端点：LLM={settings.llm.model} @ {settings.llm.base_url} ｜ "
          f"embedding={settings.embedding.model}（{len(v)} 维）｜ Qdrant={settings.qdrant.url}")
    print(f"  闸：StepFun 累计 ¥{LLM_GATE} ｜ embedding 累计 {EMB_GATE:,} token（按真 usage）")

    from codeharness.document_store.qdrant_store import QdrantStore
    store = QdrantStore()
    asyncio.run(store.drop())
    bad, read = [], {}
    try:
        with _AuthEnv(auth_on=True) as c:
            tok = c.post("/api/auth/register",
                         json={"username": USER, "password": "secret123"}).json()["token"]
            h = {"Authorization": f"Bearer {tok}"}
            s = c.post("/api/sessions", json={"idea": "c31 e2e", "project_name": PROJ},
                       headers=h).json()
            assert s["user_id"] == USER, f"前提失配：会话归属应为 {USER}，实为 {s}"
            r = c.post(f"/api/sessions/{s['id']}/workspace/upload_kb", headers=h,
                       files=[("files", (SRC, DOC.encode("utf-8"), "text/markdown"))])
            assert r.status_code == 200, f"灌库失败：{r.status_code} {r.text[:200]}"
            body = r.json()
            read["ingest"] = body
            if body["errors"] or not body["chunk_count"]:
                bad.append(f"①灌库没真发生：{body}")
            users, dims, npts = asyncio.run(_payload(store, PROJ))
            read["payload"] = {"users": users, "dims": dims, "points": npts}
            if users != [USER]:
                bad.append(f"②写侧租户不是票上那个人：{users}（应 ['{USER}']）——读写不同源还在")
            if dims != [settings.embedding.dim]:
                bad.append(f"②向量维度不是云上那台：{dims}（应 [{settings.embedding.dim}]）")

            embs = LLMGateway.embeddings()
            own = asyncio.run(_recall_as(embs, USER, PROJ, Q))
            other = asyncio.run(_recall_as(embs, OTHER, PROJ, Q))
            read["recall_markers"] = {"同租户": own.count("〔来自"), "换租户": other.count("〔来自")}
            if "〔来自" not in own:
                bad.append(f"④同租户召回为空：{own[:80]}")
            if "〔来自" in other:
                bad.append(f"④跨租户召到了别人的文档：{other[:80]}")

            from codeharness.provider.cost import CostManager
            cm = CostManager()
            out = asyncio.run(_think_once(USER, PROJ, LLMGateway.embeddings(), cm))
            read["think"] = out
            cost = cm.get_costs()
            read["cost"] = {"cost_cny": round(cost.cost_cny, 4),
                            "prompt_tokens": cost.total_prompt_tokens,
                            "completion_tokens": cost.total_completion_tokens,
                            "embedding_calls": _emb_calls, "embedding_tokens": _emb_spent}
            if not out["marker_in_first_prompt"]:
                bad.append("③auth 开的真会话里，首轮 prompt 没出现出处标记 ⇒ 自己传的文档召不回")
    finally:
        settings.qdrant.collection_prefix = keep_prefix
        asyncio.run(store.drop())
        shutil.rmtree(Path("workspace") / PROJ, ignore_errors=True)
        print(f"  清理：集合前缀 {PREFIX} 已 drop（生产前缀 {keep_prefix} 未碰）、"
              f"workspace/{PROJ} 已删、db0 未碰")

    print("\n读数：")
    for k in ("ingest", "payload", "recall_markers", "think", "cost"):
        print(f"  {k}：{read.get(k)}")
    if bad:
        print("\n判据未绿：" + "；".join(bad))
        return 2
    print("\n判据：auth 开 + 真模型 + 真向量这一场端到端通了——灌进去的切片写的是票上那个人、"
          "向量是云上那台的维度、那个人自己的首轮 prompt 里带回了出处、换个人召不回")
    return 0


if __name__ == "__main__":
    sys.exit(main())
