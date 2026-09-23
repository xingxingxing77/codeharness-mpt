#!/usr/bin/env python -m asyncio
"""S15 门禁（C3 重写）：知识库那条链**端到端**——上传 → 切块入库 → 召回命中 → 进模型上下文。

跑法（PYTHONPATH 必须带 logs 那截，少了会撞本机 WMI 永久卡死）：
  cd /e/Codeharness && PYTHONPATH=/e/Codeharness:/e/Codeharness/logs PYTHONIOENCODING=utf-8 \\
    PYTHONDONTWRITEBYTECODE=1 F:/anaconda/python.exe tests/s15_kb_upload.py

**为什么整份重写**：旧版三例全是存在性/签名断言（`hasattr`、参数名、payload 字面量），
t2 拿手工 `kb_context="FAQ: ..."` 构造一个 `TalkAction` 再喂 FakeLLM——模型确实收到了那段字，
但**没有任何生产代码会去填那个参数**（`UploadKB` 零调用者、kb 切片零读者）。这正是 §0 硬约定 4
点名的「断言打在构造参数上」：门禁绿 ≠ 知识库能用（`docs/对照2:97` G 条同一句）。
现在的八格按链路排：
  t1 真 Qdrant（gate 专属集合）+ 确定性替身 embedding：一份 .md 切成多块灌进 `doc_type="kb"`，
     召回**命中自己刚灌进去的切片**，且重传幂等（点数不翻倍）。
  t2 HTTP 端点门口三判（离线，store/embeddings 换替身）：multipart 上传 → 原件真落
     `会话工作区/kb/`、`../` 这类脏文件名被剥成 basename、非白名单后缀被拒并给原因、
     并且**传进 action 的 files 就是盘上那份路径**（不是内存字节）。
  t3 真图真 think：知识库读者挂在角色身上时，模型收到的 prompt 里真有 `[知识库片段]` 与那份 FAQ；
     **对照组**同一个角色摘掉读者再跑一次，prompt 里不许出现该字样——否则这条断言没有区分力。
  t4 摄取口径：不支持的格式与空文件明确拒（不默默灌半截），且一个点都不写。
  t5 不复燃守卫（源码文本级，先例 `s3b t15`）：B7 那个「第二个半截实现」`QdrantStore.aembed_documents`
     不许长回来；`UploadKB` 必须有生产调用者。
  t6 C16 的可见结局：向量库 / 向量模型端点各**真连一个死端口**取一次读数 → 503 且文案说出
     「原件已在 `kb/`、没切片」；两台都在线是阳性对照（200），store 抛真 bug 必须仍是 500
     （把异常一律降级成「服务不可用」就是这里要防的谎）。这一格不依赖任何在线服务。
  t7 C27 的输入长度闸：端点会静默截断超长输入（本机 bge-m3 在真文档上量到「全文向量 = 前 3200 字
     的向量」逐维相同），
     所以入库前一律过 `document_store/embed_split.split_for_embedding`。四格=长度不变量（2 万字单段）、
     换行优先、**真上传发给端点的文本**被裁、坏配置当场拒。
  t8 C27 判据 ① 的结构不变量：三条入库路（知识库/记忆/经验池）发给端点的每条文本都必须带出口记号
     ——记号做在出口函数上，绕开出口的写法直接红，不是 grep 源码文本。

t1/t3 需要 Qdrant 在线（`docker start codeharness-qdrant`，或 `docker compose up qdrant`）；
不在线时这两格打印跳过并返回——**跳过会被印在末行里**，不许拿它冒充通过。
真 bge-m3 的语义改写召回不在这里（那是 s5 t25 的形状），本门禁只钉「链路通不通」。
"""
import asyncio
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from codeharness.const import RequirementTag                                    # noqa: E402
from codeharness.document_store.qdrant_store import QdrantStore                  # noqa: E402
from codeharness.provider.fake import FakeLLM, HashEmbeddings                    # noqa: E402
from codeharness.runtime import CURRENT_PROJECT, CURRENT_USER                    # noqa: E402
from codeharness.schema import Message                                           # noqa: E402

GATE_COLL = "s15gate_kb"          # 自测专用集合，绝不碰生产 collection
PROJ = "s15_kb_proj"
# 四段、约 400 字：`read_data` 的切块器是 256 字符一块，太短就切不出第二块（t1 要的就是「切片」这个词）
FAQ = ("# 常见问题\n\n"
       "重置密码：在登录页点「设置 - 安全 - 重置」，填注册邮箱后系统发一次性验证码，验证通过即设新密码；"
       "重置完成后所有已登录设备会在五分钟内被踢下线，需要重新登录。\n\n"
       "退款政策：下单后 7 天内可全额退款，超过 7 天按剩余次数折算；退款原路返回，"
       "银行卡渠道另有 3 个工作日处理期，第三方支付当天到账。\n\n"
       "开票说明：企业客户在合同签署后 5 个工作日内开具增值税专用发票，抬头与税号以合同登记主体为准；"
       "个人订单只能开普通发票，电子票与纸质票效力相同。\n\n"
       "账号封禁：连续 3 次触发内容策略进入 24 小时冷静期，第 5 次永久封禁但保留申诉入口，"
       "申诉邮件会在 15 个工作日内答复。")


def live_qdrant() -> bool:
    import httpx

    from codeharness.configs.settings import settings
    try:
        return httpx.get(f"{settings.qdrant.url}/healthz", timeout=3).status_code == 200
    except Exception:
        return False


def _write_faq(dir_: Path, name: str = "faq.md", content: str = FAQ) -> Path:
    p = dir_ / name
    p.write_text(content, encoding="utf-8")
    return p


async def _action(store=None, embeddings=None, files=(), **kw):
    from codeharness.actions.upload_kb import UploadKB
    kb = UploadKB(llm=None, store=store, embeddings=embeddings)
    return await kb._call({"files": [str(f) for f in files], **kw})


def t1_ingest_then_recall():
    """灌进去的切片必须能被召回——`doc_type="kb"` 非空，命中的就是自己写进去的那几块。"""
    if not live_qdrant():
        print("  skip t1（Qdrant 不在线）")
        return
    from qdrant_client import models as m

    from codeharness.memory.longterm import LongTermMemory

    tmp = Path(tempfile.mkdtemp())
    store = QdrantStore(collection=GATE_COLL)
    emb = HashEmbeddings()
    tok_p, tok_u = CURRENT_PROJECT.set(PROJ), CURRENT_USER.set("u_kb")

    async def total():
        r = await store.client.count(GATE_COLL, count_filter=m.Filter(must=[
            m.FieldCondition(key="doc_type", match=m.MatchValue(value="kb")),
            m.FieldCondition(key="user_id", match=m.MatchValue(value="u_kb")),
            m.FieldCondition(key="project", match=m.MatchValue(value=PROJ))]))
        return r.count

    try:
        asyncio.run(store.drop())                       # 从干净集合起算，重复跑门禁才有可比读数
        out = asyncio.run(_action(store, emb, [_write_faq(tmp)]))
        assert out["errors"] == [], f"t1①摄取报错：{out['errors']}"
        assert out["chunk_count"] >= 2, \
            f"t1①一份四段 FAQ 只切出 {out['chunk_count']} 块 = 整篇压成一个向量，检索粒度等于没有"
        assert out["uploaded_count"] == out["chunk_count"], out

        reader = LongTermMemory(project_id=PROJ, embeddings=emb, user_id="u_kb",
                               store=store, doc_type="kb")
        hits = asyncio.run(reader.recall("怎么重置密码", k=3))
        texts = [h.content for h in hits]
        assert texts, "t1②召回空——写进去的东西读不出来，这条链还是死的"
        assert any("重置密码" in t for t in texts), f"t1②没命中自己灌进去的切片：{texts}"
        got = asyncio.run(store.search("怎么重置密码", emb._v("怎么重置密码"), k=3,
                                       doc_type="kb", user_id="u_kb", project=PROJ))
        assert got and {h.payload["doc_type"] for h in got} == {"kb"}, \
            f"t1②切片键不对（会把记忆/经验池混进来）：{[h.payload['doc_type'] for h in got]}"

        n0 = asyncio.run(total())
        asyncio.run(_action(store, emb, [_write_faq(tmp)]))      # 原样重传
        assert asyncio.run(total()) == n0, \
            f"t1③重传同一份文档，kb 切片从 {n0} 点涨到 {asyncio.run(total())} 点（幂等破了）"
        hit = next(t for t in texts if "重置密码" in t)
        print(f"  ok  t1 切块 {out['chunk_count']} 块入库、召回命中「{' '.join(hit.split())[:16]}…」、"  # 折叠换行，读数不打乱表格
              f"重传后仍是 {n0} 点")
    finally:
        CURRENT_PROJECT.reset(tok_p)
        CURRENT_USER.reset(tok_u)
        asyncio.run(store.drop())
        shutil.rmtree(tmp, ignore_errors=True)


def t2_endpoint_door():
    """端点只判门口三件事，且传给摄取的必须是**盘上那份文件**。"""
    import codeharness.actions.upload_kb as mod
    from fastapi.testclient import TestClient

    written = []                          # 替身 store 收到的切片文本

    class _Store:                       # 离线替身：这一格测的是门口，不是 Qdrant
        async def write(self, points):
            written.extend(p.text for p in points)
            return len(points)

    class _GW:
        @staticmethod
        def embeddings():
            return HashEmbeddings()

    saved = (mod.QdrantStore, mod.LLMGateway)
    mod.QdrantStore, mod.LLMGateway = lambda *a, **k: _Store(), _GW
    keep = None
    tmp_root = Path(tempfile.mkdtemp())
    try:
        import server.sessions as ss
        from codeharness.configs.settings import settings
        keep = (ss.SESSIONS_FILE, settings.platform.use_redis)
        ss.SESSIONS_FILE = tmp_root / "sessions.json"
        settings.platform.use_redis = False           # 端点这格不依赖 Redis（写的是替身 store）
        from server.app import create_app
        with TestClient(create_app()) as c:
            sid = c.post("/api/sessions", json={"idea": "kb", "project_name": "s15_kb_ep"}).json()["id"]
            parts = [("files", ("faq.md", FAQ, "text/markdown")),
                     ("files", ("../../evil.md", "# 越界\n内容", "text/markdown")),
                     ("files", ("tool.exe", b"MZ\x00\x00", "application/octet-stream"))]
            rsp = c.post(f"/api/sessions/{sid}/workspace/upload_kb", files=parts)
            assert rsp.status_code == 200, f"t2①上传被拒：{rsp.status_code} {rsp.text[:160]}"
            body = rsp.json()
            assert sorted(body["written"]) == ["evil.md", "faq.md"], body["written"]
            assert any("tool.exe" in e and "只摄取文本类文档" in e for e in body["errors"]), \
                f"t2①非白名单后缀没被拒或没说清：{body['errors']}"
                # 文案与摄取件同源（C26 起 `door_refusal()` 一份），所以这里钉的是那句的关键词而不是「只收」
            ws = Path(c.get(f"/api/sessions/{sid}").json()["workspace"])
            on_disk = sorted(p.name for p in (ws / "kb").iterdir())
            assert on_disk == ["evil.md", "faq.md"], f"t2②落盘不对：{on_disk}"
            assert not (ws.parent / "evil.md").exists(), "t2②脏文件名跑出了 kb/ 目录"
            assert (ws / "kb" / "faq.md").read_text(encoding="utf-8") == FAQ, "t2②原件内容被改过"
            assert any("重置密码" in t for t in written), f"t2③盘上有文件却没进摄取：{written[:2]}"
        print(f"  ok  t2 门口三判（越界名剥成 basename、.exe 拒并给原因）、原件真落 kb/、"
              f"摄取吃到的就是盘上那份（{len(written)} 块）")
    finally:
        mod.QdrantStore, mod.LLMGateway = saved
        if keep is not None:
            ss.SESSIONS_FILE, settings.platform.use_redis = keep
        shutil.rmtree(Path("workspace") / "s15_kb_ep", ignore_errors=True)
        shutil.rmtree(tmp_root, ignore_errors=True)


def t3_role_thinks_with_kb():
    """读者挂上 → 真图真 think 的 prompt 里真有那段资料；摘掉 → 不许出现（对照组）。"""
    if not live_qdrant():
        print("  skip t3（Qdrant 不在线）")
        return
    from codeharness.environment.team_graph import build_team
    from codeharness.memory.longterm import LongTermMemory
    from codeharness.roles.role_zero import RoleZero

    tmp = Path(tempfile.mkdtemp())
    store, emb = QdrantStore(collection=GATE_COLL), HashEmbeddings()
    tok_p, tok_u = CURRENT_PROJECT.set(PROJ), CURRENT_USER.set("u_kb")
    thought = json.dumps({"thought": "按知识库答", "commands": [{"command_name": "end", "args": {}}]},
                         ensure_ascii=False)

    def run(with_kb: bool) -> str:
        llm = FakeLLM([thought])
        role = RoleZero({"name": "R", "profile": "p", "goal": "g"}, [], llm, max_loops=2)
        if with_kb:
            role.kb = LongTermMemory(project_id=PROJ, embeddings=emb, user_id="u_kb",
                                     store=store, doc_type="kb")
        g = build_team({"R": role}, sop={RequirementTag.USER_REQUIREMENT: ["R"]})
        asyncio.run(g.ainvoke(
            {"messages": [Message(content="怎么重置密码？", role="user",
                                  cause_by=RequirementTag.USER_REQUIREMENT, sent_from="user")],
             "memories": {}, "debug_rounds": 0, "team_rounds": 0, "finished": False},
            {"configurable": {"thread_id": f"s15-t3-{with_kb}"}}))
        return str(llm.calls[-1])

    try:
        asyncio.run(store.drop())
        out = asyncio.run(_action(store, emb, [_write_faq(tmp)]))
        assert out["uploaded_count"] >= 2, f"t3 前置失配（没灌进切片）：{out}"
        with_kb = run(True)
        assert "[知识库片段]" in with_kb and "重置密码" in with_kb, \
            "t3①失效：知识库读者挂了但 prompt 里什么都没有——上传的文档永远不会被模型看见"
        without = run(False)
        assert "[知识库片段]" not in without, "t3②对照组不成立：没挂读者也出现了知识库段"
        print("  ok  t3 真图真 think 吃到知识库片段（含「重置密码」那段），摘掉读者后当场消失")
    finally:
        CURRENT_PROJECT.reset(tok_p)
        CURRENT_USER.reset(tok_u)
        asyncio.run(store.drop())
        shutil.rmtree(tmp, ignore_errors=True)


def t4_rejects_what_it_cannot_ingest():
    """不支持的格式与空文件：明确拒、一个点都不写（别默默灌半截进知识库）。"""
    class _Boom(QdrantStore):
        wrote = 0

        async def write(self, points):
            _Boom.wrote += len(points)
            return len(points)

    tmp = Path(tempfile.mkdtemp())
    store = _Boom(collection=GATE_COLL)
    try:
        out = asyncio.run(_action(store, HashEmbeddings(),
                                  [_write_faq(tmp, "data.csv", "question,answer\na,b\n")]))
        assert out["uploaded_count"] == 0 and any("只摄取文本类文档" in e for e in out["errors"]), \
            f"t4①表格类文档没被拒：{out}"
        assert _Boom.wrote == 0, f"t4①被拒的文档还是写了点：{_Boom.wrote}"
        empty = asyncio.run(_action(store, HashEmbeddings(), [_write_faq(tmp, "blank.md", "   \n")]))
        assert empty["uploaded_count"] == 0 and empty["errors"], f"t4②空文件读数不对：{empty}"
        gone = asyncio.run(_action(store, HashEmbeddings(), [tmp / "nosuch.md"]))
        assert any("文件不存在" in e for e in gone["errors"]), f"t4③缺文件没说清：{gone['errors']}"
        none = asyncio.run(_action(store, HashEmbeddings(), []))
        assert none["uploaded_count"] == 0 and none["errors"] == ["files 为空"], none
        print(f"  ok  t4 表格/空文件/缺文件/零文件四种输入全部明确拒收，零写入")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def t5_no_regression_guard():
    """两个假通道不复燃（源码文本级，先例 `s3b t15`）：第二个半截实现、以及「定义了没人调」。"""
    assert not hasattr(QdrantStore, "aembed_documents"), \
        "B7 那个自己建 embedding、自己 write 的第二份实现长回来了——入库必须只有一条路（UploadKB）"
    from codeharness.actions.upload_kb import UploadKB
    assert {"store", "embeddings"} <= set(UploadKB.model_fields), \
        "UploadKB 的注入口被摘掉 = 门禁只能挂到真 embedding 服务上，这条链就又测不了了"
    root = Path(__file__).resolve().parent.parent
    hits = [str(p.relative_to(root)) for p in (root / "server").rglob("*.py")
            if "UploadKB" in p.read_text(encoding="utf-8")]
    assert hits, "UploadKB 又变成零生产调用者了（C3 就是为这条而做的）"
    src = (root / "codeharness" / "roles" / "role_zero.py").read_text(encoding="utf-8")
    assert "_kb_recall" in src and "知识库片段" in src, "知识库读者被摘了：切片会退回只写不读"
    print(f"  ok  t5 单一摄取出口 + 注入口在位 + 生产调用者 {hits} + 读者在位")


def t6_vector_service_down_says_so():
    """C16：向量服务连不上时，端点必须说出「没切片」+「原件还在 `kb/`」，而不是一句 500。

    四格各司其职，前两格是**真 TCP 连死端口**（不是手工构造异常）——生产里两台服务各把自己的
    连接失败包了一层（qdrant→`ResponseHandlingException`、openai→`APIConnectionError`，
    **顶层类名都不提「连接」**，所以 `_unreachable` 必须顺 cause/context 链往下走）：
      ① 向量库不可达（embeddings 用替身，只留这一个变量是死的）
      ② 向量模型端点不可达（store 用替身，同上）
      ③ 阳性对照：两台都「在线」（替身都会好好干活）→ 200，且不许出现那句降级文案
      ④ 反向对照：store 抛**真 bug**（ValueError）→ 必须还是 500。把异常一律降级成
         「服务不可用」是本格要防的谎，不防它就会有人这么写。

    反向验证记账（三种偷懒写法，两抓得住一格抓不住）：一律降级 → ④ 红；只看顶层类型 → ① 红
    （回 500 而不是 503）；文案少一句「没切片」→ ① 红。**抓不住的那格要写明白**：把
    `isinstance(链上任意节点, ConnectError/ConnectionError)` 换成「顶层类名白名单
    {ResponseHandlingException, APIConnectionError}」，本格四格照样全绿——它今天行为等价，
    但库里换个包装类就静默失效。这条判据防不住那种写法，别以为它防住了。
    """
    import codeharness.actions.upload_kb as mod
    from fastapi.testclient import TestClient

    from codeharness.configs.settings import settings
    from codeharness.provider.gateway import LLMGateway as LLMGatewayReal   # ② 要真工厂：真连接真失败
    QdrantStoreReal = mod.QdrantStore                                       # ① 要真客户端：真连死端口

    DEAD = "http://127.0.0.1:1"                 # 端口 1 必然拒连：造「服务没起」而不是「服务很慢」

    class _Store:
        def __init__(self, boom=None):
            self.written, self.boom = [], boom

        async def write(self, points):
            if self.boom:
                raise self.boom
            self.written.extend(p.text for p in points)
            return len(points)

    class _GW:
        @staticmethod
        def embeddings():
            return HashEmbeddings()

    saved = (mod.QdrantStore, mod.LLMGateway, settings.qdrant.url, settings.embedding.base_url)
    keep = None
    tmp_root = Path(tempfile.mkdtemp())
    store_box = {}
    try:
        import server.sessions as ss
        keep = (ss.SESSIONS_FILE, settings.platform.use_redis)
        ss.SESSIONS_FILE = tmp_root / "sessions.json"
        settings.platform.use_redis = False             # 本格不依赖 Redis（也别去写共享 db）
        from server.app import create_app

        def parts(extra_exe=True):
            p = [("files", ("faq.md", FAQ, "text/markdown"))]
            if extra_exe:
                p.append(("files", ("tool.exe", b"MZ\x00\x00", "application/octet-stream")))
            return p

        with TestClient(create_app(), raise_server_exceptions=False) as c:   # False：④ 要拿 500 的响应体
            sid = c.post("/api/sessions", json={"idea": "c16", "project_name": "s15_kb_down"}).json()["id"]
            ws = Path(c.get(f"/api/sessions/{sid}").json()["workspace"])

            def expect_503(why):
                rsp = c.post(f"/api/sessions/{sid}/workspace/upload_kb", files=parts())
                assert rsp.status_code == 503, f"t6{why}：该回 503，实回 {rsp.status_code} {rsp.text[:160]}"
                detail = rsp.json()["detail"]
                assert "没有切片" in detail and "kb/" in detail, f"t6{why}：没说清「没切片」：{detail[:160]}"
                assert "faq.md" in detail, f"t6{why}：没点名哪份原件躺着：{detail[:160]}"
                assert "QDRANT__URL" in detail and "EMBEDDING__BASE_URL" in detail, \
                    f"t6{why}：没给出该查哪两台：{detail[:200]}"
                assert (ws / "kb" / "faq.md").exists(), f"t6{why}：文案说原件在 kb/，盘上却没有"
                assert "与向量库无关" in detail and "tool.exe" in detail, \
                    f"t6{why}：门口就拒的那条被降级文案盖掉了（部分成功不许吞）：{detail[:220]}"
                return detail

            # ① 向量库不可达：**store 用真客户端**指死端口（embeddings 换替身，只留一个死变量）
            mod.QdrantStore, mod.LLMGateway = QdrantStoreReal, _GW
            settings.qdrant.url = DEAD
            d1 = expect_503("①")
            assert "ResponseHandlingException" in d1 or "ConnectError" in d1 or "Connection" in d1, \
                f"t6①：没带上真实失败形状：{d1[:200]}"
            # ② 向量模型端点不可达：**embeddings 用真工厂**指死端口（store 换替身，同上）
            mod.QdrantStore, mod.LLMGateway = lambda *a, **k: _Store(), LLMGatewayReal
            settings.embedding.base_url = DEAD + "/v1"
            d2 = expect_503("②")
            assert "APIConnectionError" in d2, f"t6②：这台失败的真实类型没进文案：{d2[:200]}"
            # ③ 阳性对照：两台都在线（替身好好干活）→ 200，且不许有那句降级文案
            mod.QdrantStore, mod.LLMGateway = lambda *a, **k: _Store(), _GW
            settings.qdrant.url = "http://127.0.0.1:6333"
            settings.embedding.base_url = "http://127.0.0.1:11434/v1"
            rsp3 = c.post(f"/api/sessions/{sid}/workspace/upload_kb", files=parts())
            body3 = rsp3.json()
            assert rsp3.status_code == 200 and body3["uploaded_count"] > 0, \
                f"t6③ 对照失效：服务在线时也不落切片（{rsp3.status_code} {str(body3)[:160]}）"
            assert "没有切片" not in json.dumps(body3, ensure_ascii=False), "t6③：在线也报降级"
            assert any("tool.exe" in e for e in body3["errors"]), f"t6③：门口拒因丢了：{body3['errors']}"
            # ④ 反向对照：真 bug 不许被翻成「服务不可用」
            mod.QdrantStore = lambda *a, **k: _Store(boom=ValueError("'loss'"))
            rsp4 = c.post(f"/api/sessions/{sid}/workspace/upload_kb", files=parts(extra_exe=False))
            assert rsp4.status_code == 500, \
                f"t6④：把真异常降级成了 {rsp4.status_code}——「服务不可用」的谎就是这么写出来的"
            assert "没有切片" not in rsp4.text, f"t6④：500 里混进了降级文案：{rsp4.text[:160]}"
        print("  ok  t6 两种不可达各取真读数（qdrant/openai 各包一层，类名都不提「连接」）、"
              "文案三件事齐（没切片 / 原件在 kb/ / 该查哪两台）、门口拒因不被盖掉；"
              "阳性对照 200、反向对照真 bug 仍 500")
    finally:
        mod.QdrantStore, mod.LLMGateway, settings.qdrant.url, settings.embedding.base_url = saved
        if keep is not None:
            ss.SESSIONS_FILE, settings.platform.use_redis = keep
        shutil.rmtree(Path("workspace") / "s15_kb_down", ignore_errors=True)
        shutil.rmtree(tmp_root, ignore_errors=True)


def t7_long_input_cannot_reach_the_endpoint_whole():
    r"""C27：embedding 端点会把超长输入**静默截断**——本机 bge-m3 在真中文文档上量到「全文向量
    与它的前 3200 字的向量逐维完全相同」（`tests/manual_embed_truncation.py` A 格；更早那发用重复串
    前缀量到的是 3600，两条读数与出处都在 `settings.py` 的 `EMBEDDING_OBSERVED_TRUNCATION_CHARS`
    注释里）。四格都不依赖任何在线服务：

      ① 长度不变量：2 万字单段无换行 → 逐块 ≤ H **且内容守恒**（改前这种输入必产出一块 2 万字）；
      ② 换行优先：整行不被硬切拆开（否则「按段检索」这件事就没了），只有单行本身超上限才切；
      ③ 生产路径真被裁：走 `UploadKB`，spy 记下**实际发给端点的文本**——少这一格，①② 可以在
         纯函数里全绿而调用点压根没接（批次36「只跑了 s8 没跑 s7」的同族错）；
      ④ 配置面：H 是端点属性、走 `EMBEDDING__MAX_CHARS`，坏值**当场拒**（非正、或超过观测截断点），
         而不是「服务起得来、保护没了」。
    """
    from pydantic import ValidationError

    from codeharness.configs.settings import EMBEDDING_OBSERVED_TRUNCATION_CHARS, EmbeddingConfig
    from codeharness.configs.settings import settings
    from codeharness.document_store.embed_split import split_for_embedding

    h = settings.embedding.max_chars
    assert 0 < h <= EMBEDDING_OBSERVED_TRUNCATION_CHARS, f"t7④：默认上限自身不合法：{h}"

    whole = "甲" * 20000
    chunks = split_for_embedding([whole])
    assert len(chunks) > 1, f"t7①：2 万字单段只出 {len(chunks)} 块 = 整条压成一个向量"
    assert all(0 < len(c) <= h for c in chunks), \
        f"t7①：出口产出了超过上限 {h} 的块：{[len(c) for c in chunks]}"
    assert "".join(chunks) == whole, "t7①：切完少字了（守恒破了，尾部被静默丢掉就是这么发生的）"

    lines = ["乙" * 90 for _ in range(30)]              # 2729 字符 > H，但每行都远低于 H
    chunks2 = split_for_embedding(["\n".join(lines)])
    assert len(chunks2) > 1, f"t7②：{sum(map(len, lines))} 字没裁开"
    assert all(any(l in c for c in chunks2) for l in lines), "t7②：有整行被硬切拆开了"

    class _Store:
        def __init__(self):
            self.written = []

        async def write(self, points):
            self.written.extend(p.text for p in points)
            return len(points)

    class _Spy(HashEmbeddings):
        def __init__(self):
            self.sent = []

        async def aembed_documents(self, texts):
            self.sent.append(list(texts))
            return await super().aembed_documents(texts)

    spy, store = _Spy(), _Store()
    tmp = Path(tempfile.mkdtemp())
    try:
        out = asyncio.run(_action(store, spy, [_write_faq(tmp, "big.md", "丙" * 5000)]))
        assert len(spy.sent) == 1, f"t7③：发端点不该只一次：{spy.sent}"
        sent = spy.sent[0]
        assert all(len(t) <= h for t in sent), \
            f"t7③：{max(map(len, sent))} 字的切片原样发给了端点（出口没接上）"
        assert len(sent) > 1 and out["chunk_count"] == len(sent) == len(store.written), \
            f"t7③ 读数不自洽：发了 {len(sent)} 块，chunk_count={out['chunk_count']}，写了 {len(store.written)}"
        assert "".join(sent) == "丙" * 5000, "t7③：发给端点的文本拼不回去，切块过程吞了字"
        short = _Spy()
        asyncio.run(_action(_Store(), short, [_write_faq(tmp, "faq.md", FAQ)]))
        assert any(len(batch) > 1 for batch in short.sent) and \
            all(len(t) <= h for batch in short.sent for t in batch), \
            f"t7③ 阳性对照失效：正常四段 FAQ 的读数不对（{[len(b) for b in short.sent]}）"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    for bad in (0, -5, EMBEDDING_OBSERVED_TRUNCATION_CHARS + 1):
        try:
            EmbeddingConfig(max_chars=bad)
            raise AssertionError(f"t7④：坏值 {bad} 竟然被接受了（非正/超过观测截断点要当场拒）")
        except ValidationError:
            pass
    assert EmbeddingConfig(max_chars=1).max_chars == 1
    print(f"  ok  t7 出口守住长度不变量（2 万字→{len(chunks)} 块、逐块 ≤{h} 且守恒）、整行不被拆、"
          f"真上传发给端点的最长 {max(map(len, sent))} 字；坏配置三种（0 / -5 / "
          f"{EMBEDDING_OBSERVED_TRUNCATION_CHARS + 1}）全部当场拒")


def t8_every_ingestion_path_goes_through_the_exit():
    """C27 判据 ①：**结构不变量**——四条入库路发给端点的文本必须逐条经过 `split_for_embedding`。

    钉法不是在源码里 grep 那句调用（那种判据防不住「换了个变量名、但把原文发出去」，也只防得住
    写法不像防得住行为）。这里**在出口上做记号**：把三个消费模块里的出口换成「切完给每块加一个
    哨兵字符」的包装，再用替身 embeddings 记下真发出去的文本。哪条路绕开出口，它发出去的就是没有
    哨兵的原文 ⇒ 那一格红；出口整个被摘掉（还原成 `aembed_documents(chunks)`）同样红。

    `.docx`/`.pdf` 那两支不必单列：它们的文本从 `_texts_of` 出来，与 .md 共用 `UploadKB` 那一次调用。
    """
    from codeharness.actions import upload_kb as kb_mod
    from codeharness.configs.settings import settings
    from codeharness.document_store import exp_store as exp_mod
    from codeharness.document_store.embed_split import split_for_embedding as real_split
    from codeharness.memory import longterm as ltm_mod
    from codeharness.memory.longterm import LongTermMemory
    from codeharness.document_store.exp_store import ExpStore

    MARK = "\u205f"                      # U+205F：真文本里不会有的字符，出口走过的记号（写成转义，别用肉眼看不见的字面量）
    mods = (kb_mod, ltm_mod, exp_mod)
    saved = [getattr(m, "split_for_embedding") for m in mods]

    def _marked(texts, max_chars: int = 0):
        return [t + MARK for t in real_split(texts, max_chars)]

    class _Store:
        async def write(self, points):
            return len(points)

    class _Spy(HashEmbeddings):
        def __init__(self):
            self.sent = []

        async def aembed_documents(self, texts):
            self.sent.append(list(texts))
            return await super().aembed_documents(texts)

        async def aembed_query(self, q):
            self.sent.append([q])
            return await super().aembed_query(q)

    tmp = Path(tempfile.mkdtemp())
    long_text = "戊" * (settings.embedding.max_chars * 2 + 7)
    try:
        for m in mods:
            m.split_for_embedding = _marked
        spy = _Spy()
        tok_p, tok_u = CURRENT_PROJECT.set("s15_t8"), CURRENT_USER.set("u_t8")
        try:
            asyncio.run(_action(_Store(), spy, [_write_faq(tmp, "long.md", long_text)]))
            kb_batches = spy.sent.copy(); spy.sent.clear()
            asyncio.run(LongTermMemory(project_id="s15_t8", embeddings=spy, user_id="u_t8",
                                       store=_Store(), doc_type="memory")
                        .overflow([Message(content=long_text, role="user")]))
            ltm_batches = spy.sent.copy(); spy.sent.clear()
            asyncio.run(ExpStore(embeddings=spy, user_id="u_t8", store=_Store())
                        .save("SomeAction", long_text, "resp"))
            exp_batches = spy.sent.copy()
        finally:
            CURRENT_PROJECT.reset(tok_p)
            CURRENT_USER.reset(tok_u)
        for name, batches in (("知识库 upload_kb", kb_batches), ("记忆 longterm.overflow", ltm_batches),
                              ("经验池 exp_store.save", exp_batches)):
            assert batches, f"t8：{name} 一次都没发端点——这格成了空转"
            for batch in batches:
                for t in batch:
                    assert t.endswith(MARK), \
                        f"t8：{name} 把没走出口的文本发给了端点（{len(t)} 字，端点在 ~3600 字以上静默截断）"
        print(f"  ok  t8 三条入库路（知识库/记忆/经验池）发给端点的 {sum(len(b) for b in kb_batches + ltm_batches + exp_batches)} "
              f"条文本全部带出口记号；pdf/docx 与 .md 共用知识库那一次调用")
    finally:
        for m, f in zip(mods, saved):
            m.split_for_embedding = f
        shutil.rmtree(tmp, ignore_errors=True)


DOCX_TEXT = "ALPHA chunk of the knowledge base probe. " * 70      # 3010 字，一段到底：顺手把 C27 的上限接缝量出来


def _write_docx(dir_: Path, name: str = "probe.docx", text: str = DOCX_TEXT) -> Path:
    """手工搭一份**最小合法 .docx**（zip + 三个部件）。

    不为此加 `python-docx`：夹具的本体就是「docx 是一个带 `[Content_Types].xml` 的 zip」，
    `docx2txt` 只读 `word/document.xml` 里的 `<w:t>`。文本按 `\\n` 分段的 `<w:p>`，
    所以它天然**整篇一个 Document**（不二次切）——那正是 C26 记的粒度缺陷，也是 C27 出口要接住的东西。
    """
    import zipfile
    body = "".join('<w:p><w:r><w:t xml:space="preserve">' + p + "</w:t></w:r></w:p>"
                   for p in text.split("\n"))
    parts = {
        "[Content_Types].xml":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        "_rels/.rels":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/officeDocument" Target="word/document.xml"/></Relationships>',
        "word/document.xml":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body>" + body + "</w:body></w:document>",
    }
    p = dir_ / name
    with zipfile.ZipFile(p, "w") as z:
        for k, v in parts.items():
            z.writestr(k, v)
    return p


def _write_pdf(dir_: Path, name: str = "probe.pdf", lines=("KB probe page one line",)) -> Path:
    """手工搭一份**最小合法单页 PDF**（Type1 Helvetica + 若干 `Tj`，带正确 xref）。

    用 ASCII：`pypdf` 从无字体的中文流里提不出东西，那会把「读不出块」和「读得出但没字」混成一格。
    PDF 那条按页出块（一页 = 一份 Document），所以这里的判据是「读出 ≥1 块」，不是「切成几块」。
    """
    stream = "BT /F1 11 Tf " + "".join("1 0 0 1 60 " + str(760 - 14 * i) + " Tm ("
                                       + t.replace("\\", "\\\\").replace("(", r"\(").replace(")", r"\)")
                                       + ") Tj " for i, t in enumerate(lines)) + "ET"
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R"
        b" /Resources << /Font << /F1 5 0 R >> >> >>",
        ("<< /Length " + str(len(stream)) + " >>\nstream\n" + stream + "\nendstream").encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objs, 1):
        offsets.append(len(out))
        out += (str(i) + " 0 obj\n").encode() + obj + b"\nendobj\n"
    xref_at = len(out)
    out += ("xref\n0 " + str(len(objs) + 1) + "\n0000000000 65535 f \n").encode()
    for off in offsets:
        out += (("%010d 00000 n \n" % off).encode())
    out += ("trailer\n<< /Size " + str(len(objs) + 1) + " /Root 1 0 R >>\nstartxref\n"
            + str(xref_at) + "\n%%EOF\n").encode()
    p = dir_ / name
    p.write_bytes(bytes(out))
    return p


def _fixture_for(dir_: Path, suffix: str) -> Path:
    return {".txt": lambda: _write_faq(dir_, "probe.txt", "知识库探针\n\n" + DOCX_TEXT),
            ".md": lambda: _write_faq(dir_, "probe.md", FAQ),
            ".docx": lambda: _write_docx(dir_),
            ".pdf": lambda: _write_pdf(dir_)}[suffix]()


def t9_whitelist_never_lies():
    """C26：白名单宣称收 `.docx`/`.pdf`，而**声明**与**本机读得动**是两件事——以前混成一份，
    于是缺组件的机器上传一份 .docx：先落进 `kb/`、再在摄取那步抛
    `ModuleNotFoundError: No module named 'docx2txt'`，那句话被原样拼进给用户看的 `errors[]`，
    HTTP 还是 200（读数出处见 PLAN §4 C26 行）。

    三格，都不依赖在线服务（`_texts_of` 直调 + 门口那一格把 store/embeddings 换成替身）：
      ① **两态必居其一**（结构不变量）：白名单里每个后缀，要么这台机器真读出 ≥1 块，
         要么被明确拒收——**没有第三种**（静默回空、抛 Python 原文都算坏）。哪一态由本机的真能力决定，
         所以同一格在装了组件的镜像里走「读出」那支、在这台机器上走「拒收」那支，两支都必须过。
      ② **拒收那支的文案是人话**：点名缺哪个组件与补法，且**不含** `ModuleNotFoundError`/`ImportError`
         /`package not found` 任何一句 Python 原文（少这条，① 会被「抛了个别的错」糊过去）。
      ③ **门口真拒、原件不落盘**：真发一次 multipart（一份 .docx + 一份 .md）→ 200、`uploaded_count>0`
         （.md 那条照常摄取，这是① ② 的阳性对照），而缺件环境里 .docx **不在 `kb/` 里**、
         它的拒因带「没有入库」字样；装了组件的环境里它必须**进 `written[]`**（否则白名单反过来说谎了）。
    """
    from codeharness.actions.upload_kb import KbFormatError, SUPPORTED, _texts_of
    from codeharness.configs.settings import settings
    from codeharness.document import OPTIONAL_READERS, reader_available

    state = {s: reader_available(s) for s in sorted(SUPPORTED)}
    tmp = Path(tempfile.mkdtemp())

    class _Store:
        def __init__(self):
            self.written = []

        async def write(self, points):
            self.written.extend(p.text for p in points)
            return len(points)

    class _Spy(HashEmbeddings):
        def __init__(self):
            self.sent = []

        async def aembed_documents(self, texts):
            self.sent.append(list(texts))
            return await super().aembed_documents(texts)

    read_out, refused = [], []
    for suffix in sorted(SUPPORTED):
        f = _fixture_for(tmp, suffix)
        if state[suffix]:
            texts = _texts_of(f)
            assert texts and all(t.strip() for t in texts), f"t9①：{suffix} 说读得动却读不出块：{texts}"
            spy, store = _Spy(), _Store()
            out = asyncio.run(_action(store, spy, [f]))
            sent = spy.sent[0] if spy.sent else []
            assert out["uploaded_count"] > 0 and sent, f"t9①：{suffix} 走 action 没写进任何点：{out}"
            assert all(len(t) <= settings.embedding.max_chars for t in sent), \
                f"t9①：{suffix} 的块有 {max(map(len, sent))} 字，越过 C27 的上限接缝（{out}）"
            read_out.append(f"{suffix}→{len(texts)}块/{len(sent)}发")
        else:
            assert suffix in OPTIONAL_READERS, f"t9①：{suffix} 不是可选组件那类，凭什么拒收"
            try:
                _texts_of(f)
                raise AssertionError(f"t9①：{suffix} 缺组件却读出了东西——白名单和现实又对不上了")
            except KbFormatError as e:
                msg = str(e)
                assert OPTIONAL_READERS[suffix] in msg and "没有入库" in msg, \
                    f"t9②：拒因没点名该补哪个组件：{msg}"
            except Exception as e:
                raise AssertionError(f"t9②：{suffix} 抛的不是 KbFormatError，是 {type(e).__name__}: {e}")
            # **给用户看的那一行**才是判据对象（`_texts_of` 的 message 只是半成品）：
            # 不许有 Python 原文，也不许有异常类名前缀——`KbFormatError: …` 那种拼法
            # 就是 C26 那行 `type(e).__name__` 格式化器长出来的（反向验证 m3 专钉这一格）。
            line = asyncio.run(_action(_Store(), _Spy(), [f]))["errors"][0]
            for leak in ("ModuleNotFoundError", "ImportError", "No module named", "package not found"):
                assert leak not in line, f"t9②：Python 原文漏进给用户看的 errors[]：{line[:180]}"
            assert not re.search(r"[A-Za-z_][A-Za-z0-9_]*(Error|Exception)\s*:", line), \
                f"t9②：给用户看的那行还挂着异常类名前缀：{line[:180]}"
            assert "没有入库" in line and OPTIONAL_READERS[suffix] in line, \
                f"t9②：门口那两句人话没原样到达用户：{line[:180]}"
            refused.append(suffix)

    # ③ 端点门口那一格：store/embeddings 全换替身 ⇒ 真 HTTP 语义、零在线服务
    import codeharness.actions.upload_kb as mod
    from fastapi.testclient import TestClient

    class _GW:
        @staticmethod
        def embeddings():
            return _Spy()

    saved = (mod.QdrantStore, mod.LLMGateway)
    tmp_root = Path(tempfile.mkdtemp())
    try:
        import server.sessions as ss
        keep = (ss.SESSIONS_FILE, settings.platform.use_redis)
        ss.SESSIONS_FILE = tmp_root / "sessions.json"
        settings.platform.use_redis = False
        mod.QdrantStore, mod.LLMGateway = lambda *a, **k: _Store(), _GW
        from server.app import create_app
        with TestClient(create_app(), raise_server_exceptions=False) as c:
            sid = c.post("/api/sessions", json={"idea": "c26", "project_name": "s15_c26_door"}).json()["id"]
            ws = Path(c.get(f"/api/sessions/{sid}").json()["workspace"])
            rsp = c.post(f"/api/sessions/{sid}/workspace/upload_kb", files=[
                ("files", ("probe.docx", _write_docx(tmp).read_bytes(),
                           "application/vnd.openxmlformats-officedocument.wordprocessingml.document")),
                ("files", ("ok.md", FAQ, "text/markdown"))])
            assert rsp.status_code == 200, f"t9③：门口这一判不该把请求打成 {rsp.status_code}：{rsp.text[:200]}"
            body, docx_ok = rsp.json(), state.get(".docx", False)
            dline = [e for e in body["errors"] + body.get("written", []) if "probe.docx" in e]
            for e in body["errors"]:
                for leak in ("ModuleNotFoundError", "ImportError", "package not found", "No module named"):
                    assert leak not in e, f"t9③：端点把 Python 原文吐给了用户：{e[:160]}"
            assert body["uploaded_count"] > 0, f"t9③：阳性对照失效，那份 .md 也没进库：{body}"
            assert (ws / "kb" / "ok.md").exists(), "t9③：.md 的原件没落盘"
            landed = (ws / "kb" / "probe.docx").exists()
            if docx_ok:
                assert "probe.docx" in body["written"] and landed, \
                    f"t9③：这台机器读得动 .docx，它却没进 written[]：{body}"
                assert not dline or all("读不了" not in e for e in dline), f"t9③：读得动还被拒：{dline}"
            else:
                assert not landed, "t9③：.docx 本机读不了，原件却已经落进 kb/ 了（这正是 C26 那个坏形状）"
                assert any("没有入库" in e for e in body["errors"]), f"t9③：.docx 的拒因没说清：{body['errors']}"
        print(f"  ok  t9 白名单两态必居其一：读出 {read_out or '（本机一种都没有）'}；"
              f"缺件拒收 {refused or '（本机全读得动）'}；门口真请求 → "
              f".docx {'进 written[] 并落盘' if docx_ok else '不落盘、拒因说「没有入库」'}，"
              f".md 阳性对照 uploaded_count={body['uploaded_count']}")
    finally:
        mod.QdrantStore, mod.LLMGateway = saved
        if keep is not None:
            ss.SESSIONS_FILE, settings.platform.use_redis = keep
        shutil.rmtree(Path("workspace") / "s15_c26_door", ignore_errors=True)
        shutil.rmtree(tmp_root, ignore_errors=True)
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    from codeharness.configs.settings import settings
    if settings.langfuse.enabled:
        # 已知慢，不是卡死：`observability.shutdown()` 会在 lifespan 退出时**同步** flush 队列，
        # 而 `.env` 默认开着开关、本机 langfuse 容器停着 ⇒ 每退一次就多等一轮导出超时（t1/t3 真跑起来
        # span 一多，整份门禁能从 30 秒涨到看不完）。要快跑就 `LANGFUSE__ENABLED=0`，账在 PLAN C28。
        print("  ⚠ LANGFUSE__ENABLED=1 而端点多半没起：每组 TestClient 退出都会等一次 span flush；"
              "嫌慢就加 `LANGFUSE__ENABLED=0` 再跑（判据不依赖它）")
    checks = [t1_ingest_then_recall, t2_endpoint_door, t3_role_thinks_with_kb,
              t4_rejects_what_it_cannot_ingest, t5_no_regression_guard,
              t6_vector_service_down_says_so, t7_long_input_cannot_reach_the_endpoint_whole,
              t8_every_ingestion_path_goes_through_the_exit, t9_whitelist_never_lies]
    for f in checks:
        f()
    print(f"\nS15 门禁通过：{len(checks)} 组（知识库端到端：摄取→召回→进模型 + 向量服务不可达的可见结局 "
          f"+ C27 的输入长度两道闸 + C26 的白名单两态）——"
          f"其中 t1/t3 需要 Qdrant 在线，本次 {'已实跑' if live_qdrant() else '**跳过**'}；"
          f"t6/t7/t8/t9 都不依赖在线服务（死端口 + 替身），任何环境都必须跑到")


if __name__ == "__main__":
    main()
