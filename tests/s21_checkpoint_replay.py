"""S21 门禁（C11 时间旅行回放）：两条只读端点的语义与防御。

钉三件事：
  t1 单位层 `_ckpt_page`：真两节点小图 + 真 AsyncSqliteSaver 跑 4 场 ainvoke（实测每次落 3 份，
     共 12 份）。断言 ①页大小与 `has_more`/`next_before`；②**新→旧、step 单调递减**；
     ③页内每份**不带 values**（列表页不许把黑板搬出来）；④字段集钉死（漂移即红，前端吃这份）；
     ⑤**翻页是开区间**：第二页不许含 `next_before` 那一条本身，也不许与第一页重叠。
     ⑤是本轮最容易写错的一格：把 checkpoint_id 塞进 config 是「含它自己」的闭区间，
     只有 `before=` 关键字才是开区间——B2 那轮踩过同族形状。
  t2 端点层：`GET /{sid}/checkpoints` 与 `/{sid}/checkpoints/{cid}` 的码与形状，含
     **413 那格**（把上限压到极小跑正向、恢复后跑对照，证明是上限在挡而不是永远不触发）、
     图重建不出来时列表回空但带 `reason`（不装死）、详情回 409、`limit` 值域 422。
  t3 采集是免费的这件事不许被忘掉：断言「没写进 checkpoint 的东西读不出来」——
     端点只读 saver，跑完会话后再也不 ainvoke 一次，历史条数不许变。
  t4 B7：回放面那两条 GET 走**不注册**的只读重建——三张表零注册、store.update 零调用
     （原先看一眼回放就把完整团队图永久钉进 runner，还顺手写库）。

跑法：
  cd /e/Codeharness && PYTHONPATH=/e/Codeharness:/e/Codeharness/logs PYTHONIOENCODING=utf-8 \\
    PYTHONDONTWRITEBYTECODE=1 F:/anaconda/python.exe -B tests/s21_checkpoint_replay.py
"""
import asyncio
import tempfile
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from codeharness.environment.checkpoint import async_sqlite_saver, close_all
from server.events import SessionEventBus
from server.runner import SessionRunner
from server.sessions import SessionStore

PAGE_FIELDS = {"checkpoint_id", "step", "source", "ts", "next", "tasks"}   # 09-25 删掉装死的 writes（langgraph 的 metadata 无此键）


class S(TypedDict):
    n: int
    log: list


async def _build(runs: int = 4):
    """真图 + 真文件 saver：与 server 用的同一条工厂，别拿内存 saver 糊（那测不到落盘形状）。"""
    async def step(state: S) -> S:
        return {"n": state["n"] + 1, "log": state["log"] + [f"s{state['n'] + 1}"]}

    g = StateGraph(S)
    g.add_node("step", step)
    g.add_edge(START, "step")
    g.add_edge("step", END)
    saver = await async_sqlite_saver(Path(tempfile.mkdtemp()) / "s21.db")
    assert saver is not None, "拿不到 AsyncSqliteSaver（aiosqlite / langgraph.checkpoint.sqlite 缺失）"
    app = g.compile(checkpointer=saver)
    cfg = {"configurable": {"thread_id": "s21"}, "recursion_limit": 25}
    for _ in range(runs):
        await app.ainvoke({"n": 0, "log": []}, cfg)
    return app, cfg


def _ok(n, msg):
    print(f"  ok  {n} {msg}")


async def t1_page_semantics():
    app, cfg = await _build(runs=4)
    runner = SessionRunner(SessionStore(path=Path(tempfile.mkdtemp()) / "sessions.json"), SessionEventBus())

    page = await runner._ckpt_page(app, cfg, 4)
    items = page["checkpoints"]
    assert len(items) == 4, f"页大小不对：{len(items)}"
    assert page["has_more"] is True and page["next_before"], f"还有 8 条却说没下一页：{page['has_more']}/{page['next_before']!r}"
    steps = [it["step"] for it in items]
    assert steps == sorted(steps, reverse=True) and len(set(steps)) == len(steps), \
        f"不是新→旧且严格单调：{steps}"
    for it in items:
        assert set(it) == PAGE_FIELDS, f"页项字段集漂移（前端吃这份形状）：{sorted(set(it) ^ PAGE_FIELDS)}"
        assert "values" not in it and "state" not in it, "列表页混进了整份黑板"
    # 「谁在这一步干活」只有 tasks 一个真值源：旧写法读的是 `md.get("writes")`，而本机 langgraph 根本
    # 不给这个键 ⇒ 那一格永远是空数组，前端那句「空则退回 tasks」又把死字段盖住了。删字段之后必须验它**有内容**：
    assert any(it["tasks"] for it in items), "所有超步的 tasks 都是空的 ⇒ 这一栏没有信息，界面那列又是破的"
    assert all(isinstance(it["tasks"], list) for it in items), "tasks 该是节点名数组"

    p2 = await runner._ckpt_page(app, cfg, 4, before={"configurable": {"thread_id": "s21",
                                                                      "checkpoint_id": page["next_before"]}})
    ids1 = {it["checkpoint_id"] for it in items}
    ids2 = {it["checkpoint_id"] for it in p2["checkpoints"]}
    assert not (ids1 & ids2), f"两页重叠（before 成了闭区间）：{ids1 & ids2}"
    assert page["next_before"] not in ids2, "before 那一条自己出现在第二页 = 开区间没生效"
    assert len(p2["checkpoints"]) == 4 and p2["has_more"] is True

    tail = await runner._ckpt_page(app, cfg, 20)
    assert len(tail["checkpoints"]) == 12, f"4 场 ainvoke 应落 12 份，实读 {len(tail['checkpoints'])}"
    assert tail["has_more"] is False and tail["next_before"] == "", "到底了还留着游标"
    _ok("t1", "12 份快照新→旧、step 单调；页项无 values 且字段集不漂；"
              f"翻页开区间（第二页不含边界条、与第一页零重叠，ids={len(ids1 | ids2)} 唯一）")


async def t2_endpoints():
    import server.sessions as ss
    from codeharness.provider import gateway  # noqa: F401  确保 settings 已加载（与端点同进程）
    from fastapi.testclient import TestClient
    from server.app import create_app

    tmp = Path(tempfile.mkdtemp())
    keep = ss.SESSIONS_FILE
    ss.SESSIONS_FILE = tmp / "sessions.json"
    app, cfg = await _build(runs=4)
    try:
        import codeharness.configs.settings as cs
        cs.settings.platform.use_redis = False
        with TestClient(create_app()) as c:
            sid = c.post("/api/sessions", json={"idea": "回放", "project_name": "s21ep"}).json()["id"]
            runner = c.app.state.runner
            runner._ensure_graph = lambda _sid, **kw: asyncio.sleep(0, (app, cfg))   # 注入真图

            r = c.get(f"/api/sessions/{sid}/checkpoints?limit=5")
            assert r.status_code == 200, r.text[:120]
            body = r.json()
            assert len(body["checkpoints"]) == 5 and body["has_more"] is True and body["next_before"], body
            cid = body["checkpoints"][0]["checkpoint_id"]

            d = c.get(f"/api/sessions/{sid}/checkpoints/{cid}")
            assert d.status_code == 200, d.text[:160]
            det = d.json()
            assert det["checkpoint_id"] == cid and isinstance(det["state"], dict) \
                and "n" in det["state"], f"详情没带回当时那份 state：{str(det)[:160]}"

            # 413 正向：把上限压到 1 字节，同一请求必须 413；恢复后必须 200（对照）
            import server.api.sessions as api
            keep_bytes = api.MAX_STATE_BYTES
            api.MAX_STATE_BYTES = 1
            over = c.get(f"/api/sessions/{sid}/checkpoints/{cid}")
            api.MAX_STATE_BYTES = keep_bytes
            back = c.get(f"/api/sessions/{sid}/checkpoints/{cid}")
            assert over.status_code == 413, f"上限压到 1 字节竟不挡：{over.status_code} {over.text[:120]}"
            assert "超" in over.text and "回放上限" in over.text, f"413 detail 没说是上限：{over.text[:120]}"
            assert back.status_code == 200, f"恢复上限后同请求还 413（那是另一条洞）：{back.status_code}"

            # limit 值域
            assert c.get(f"/api/sessions/{sid}/checkpoints?limit=0").status_code == 422
            assert c.get(f"/api/sessions/{sid}/checkpoints?limit=501").status_code == 422

            # 图重建不出来：列表空但有 reason，详情 409——都不许装成"这个会话没有历史"
            runner._ensure_graph = lambda _sid, **kw: asyncio.sleep(0, None)
            empty = c.get(f"/api/sessions/{sid}/checkpoints")
            assert empty.status_code == 200 and empty.json()["checkpoints"] == [] \
                and empty.json().get("reason"), f"重建失败时列表没给 reason：{empty.text[:120]}"
            assert c.get(f"/api/sessions/{sid}/checkpoints/{cid}").status_code == 409
    finally:
        ss.SESSIONS_FILE = keep
    _ok("t2", f"两端点码与形状全对；413 正向+恢复对照（压到 1 字节必挡、恢复必 200）；"
              "limit 值域 422；重建失败列表带 reason、详情 409")


async def t3_read_only():
    """回放面只读：读一轮历史之后，条数不许变（真往里写过东西才会有下次读数突然多出来的怪事）。"""
    app, cfg = await _build(runs=2)
    runner = SessionRunner(SessionStore(path=Path(tempfile.mkdtemp()) / "sessions.json"), SessionEventBus())
    before = len((await runner._ckpt_page(app, cfg, 50))["checkpoints"])
    for _ in range(3):
        await runner._ckpt_page(app, cfg, 5)
        await app.aget_state(cfg)
    after = len((await runner._ckpt_page(app, cfg, 50))["checkpoints"])
    assert before == after, f"读历史把历史读长了：{before} → {after}（端点里藏了写）"
    _ok("t3", f"只读成立：连读 3 轮 + aget_state 之后仍是 {after} 份，端点路径上不产生新 checkpoint")


async def t4_read_only_rebuild_registers_nothing():
    """B7：回放面那两条 GET 只许**借**一个图——不许挂进 runner，也不许写库。

    `_ensure_graph` 原先只有一条路：慢路径把重建结果写进 `graphs/projects/costs`，而这三张表
    只有 `_forget` 清、`_forget` 只由 run/stop 调（断点态还刻意留着）⇒「看一眼旧会话的回放」
    就把一个完整团队图永久钉在进程里；顺带 `_prepare` 里那句 `store.update(roles=…)` 让
    只读路由写了库。判据（`_prepare` 打桩，量的就是 runner 自己那半）：
      ① `register=False`：三张表一个键都没多、**`store.update` 一次都没被调**（spy 打在 store 上，
         不靠桩自证——桩里手写一遍「我不写」是测替身，不是测产品）；
      ② 阳性对照：同一支 spy 手工调一次 `store.update` 必须被记到（否则①可能只是 spy 坏了），
         且 `register=True` 那条正路照旧注册（不是「什么都不做所以没副作用」）；
      ③ 只读那条路每次都给得出 (graph, config)，`persist_roles=False` 真传进了 `_prepare`。
    """
    store = SessionStore(path=Path(tempfile.mkdtemp()) / "sessions.json")
    runner = SessionRunner(store, SessionEventBus())
    s = store.create("只读回放", project_name="s21_ro")
    seen, updates = [], []
    real_update = store.update
    store.update = lambda *a, **kw: (updates.append((a, kw)), real_update(*a, **kw))[1]

    async def fake_prepare(session, project, cost_manager, persist_roles=True):
        seen.append(persist_roles)
        return object(), {"configurable": {"thread_id": project}}, None

    runner._prepare = fake_prepare
    packed = await runner._ensure_graph(s.id, register=False)
    assert packed, "只读重建拿不到 (graph, config)"
    assert not runner.graphs and not runner.costs and not runner.projects, \
        (f"只读路由把重建结果挂进了 runner（B7：这三张表只有 _forget 清）——"
         f"graphs={list(runner.graphs)} costs={list(runner.costs)} projects={list(runner.projects)}")
    assert updates == [], f"只读重建写库了（store.update 被调）：{updates}"
    assert seen == [False], f"persist_roles=False 没传到 _prepare：{seen}"

    # ② spy 的阳性对照：手工走一次 `store.update`（=被 spy 截住的那个入口）必须被记到，
    #    否则①的「零调用」可能只是 spy 挂错了地方——那才是真正的假绿。
    store.update(s.id, status="awaiting_human")
    assert updates, "spy 根本没记到任何 update ⇒ ①那一格是假绿"
    updates.clear()

    packed2 = await runner._ensure_graph(s.id)                  # ② 正路：照旧注册
    assert packed2 and runner.graphs.get(s.id) and runner.costs.get(s.id) and runner.projects.get(s.id), \
        f"register=True 却没注册（正路被改坏了）：graphs={list(runner.graphs)}"
    assert seen[-1] is True, f"正路没让 _prepare 回写 roles：{seen}"
    assert store.get(s.id).status.value == "awaiting_human", "stub 那步会话记录没写进去"
    _ok("t4", "只读重建（register=False）：三张表零注册 + store.update 零调用；"
              "正路照旧注册且 persist_roles=True（spy 有阳性对照）")


async def t5_thread_key_is_the_session_id_not_the_project_name():
    """③（09-26 用户拍板「名字与唯一键解耦」）：会话在 checkpointer 里的键是 **sid**，不是项目目录名。

    原先 `_prepare` 把 `session.project_name or sid` 直接当 `thread_id`（`runner.py:219/241`），
    而 `project_name` 是**用户输入的名字**、同用户重名是合法的（`api/sessions.py` 的 409 只挡跨用户）
    ⇒ 同用户两场同名会话共用一条线程：B 的 messages/memories 追加进 A 的线程。

    判据走**真 `_prepare`**（不打桩它），量的是它交给装配器的 `project_id`——spy 打在
    `codeharness.team.prepare_project` 上（`_prepare` 是函数内 import，patch 模块属性有效）。
    四支，旧键/新键的「有没有断点」由假 saver 给：
      ① 同名两场（都没跑过）⇒ 各拿各的 sid、互不相同；
      ② **存量回退**：跑过的会话（`started_at` 非空）、sid 下空、老键（项目名）下有货 ⇒ 用老键
         （切换前落下的断点不作废，老会话照旧续得上）；
      ③ **新会话不被带回老线程**：没跑过的新会话，即便老键有货也用自己的 sid
         （少了这道，「同名新会话」会认领别人的线程 = 把这条修法反过来又踩一遍）；
      ④ 新键已有断点 ⇒ 优先新键，哪怕老键也有货。
    """
    import codeharness.team as team_mod
    from codeharness.provider.cost import CostManager
    from langgraph.checkpoint.memory import InMemorySaver

    class _Saver(InMemorySaver):
        """真 saver 的**子类**，只把「哪个 thread 有断点」这一问答成我们要的样子。

        ⚠ 不能自造一个只有 `aget_tuple` 的类：`_prepare` 会把它交给 `build_team`，而
        `langgraph.graph.state.compile` 里有 `ensure_valid_checkpointer`，非 `BaseCheckpointSaver`
        直接 TypeError（本轮第一版就这么红的）。这一格量的是**键的选择**，不是 saver 本身。"""

        def __init__(self):
            super().__init__()
            self.has: set = set()

        async def aget_tuple(self, config):
            return {"thread": config["configurable"]["thread_id"]} \
                if config["configurable"]["thread_id"] in self.has else None

    saver = _Saver()
    store = SessionStore(path=Path(tempfile.mkdtemp()) / "sessions.json")
    runner = SessionRunner(store, SessionEventBus())

    async def _fake_saver():
        return saver

    runner._saver = _fake_saver
    seen: list = []
    real_prepare = team_mod.prepare_project

    def spy(idea, project_id, **kw):
        seen.append(project_id)
        return real_prepare(idea, project_id, **kw)

    a = store.create("甲", project_name="same_name", user_id="u1")
    b = store.create("乙", project_name="same_name", user_id="u1")
    cm = CostManager()
    try:
        team_mod.prepare_project = spy
        await runner._prepare(a, "same_name", cm)          # ① 同名两场…
        await runner._prepare(b, "same_name", cm)
        assert seen == [a.id, b.id], \
            f"t5① 同名两会话没各用各的 sid（③ 未落地）：{seen}（a={a.id} b={b.id}）"

        store.update(a.id, started_at="2026-01-01 00:00:00")
        a_ran = store.get(a.id)                            # ⚠ update 会换一个新对象，别拿旧的（旧对象 started_at 仍空）
        saver.has = {"same_name"}                          # ② 存量：断点只在老键下
        seen.clear()
        await runner._prepare(a_ran, "same_name", cm)
        assert seen == ["same_name"], f"t5② 存量断点没回退到老键（老会话断点作废了）：{seen}"

        fresh = store.create("丙", project_name="same_name", user_id="u1")
        seen.clear()
        await runner._prepare(fresh, "same_name", cm)      # ③ 新会话 + 老键有货
        assert seen == [fresh.id], f"t5③ 同名新会话认领了老线程：{seen}（要 {fresh.id}）"

        saver.has = {"same_name", a.id}                    # ④ 新键也有货
        seen.clear()
        await runner._prepare(a_ran, "same_name", cm)
        assert seen == [a.id], f"t5④ 新键已有断点却没用它：{seen}"
    finally:
        team_mod.prepare_project = real_prepare
    _ok("t5", "会话键 = sid：同名两场各用各的线程（旧键仅在「跑过的会话 + 新键空 + 老键有货」时回退）；"
              "新会话不会被带回老线程")


def main():
    try:
        asyncio.run(t1_page_semantics())
        asyncio.run(t2_endpoints())
        asyncio.run(t3_read_only())
        asyncio.run(t4_read_only_rebuild_registers_nothing())
        asyncio.run(t5_thread_key_is_the_session_id_not_the_project_name())
    finally:
        # 不关连接的话解释器会被 aiosqlite 的后台线程吊住（C2 那轮的收官教训：断言全过但永不退出）
        asyncio.run(close_all())
    print("\ns21_checkpoint_replay: 5/5 全绿")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
