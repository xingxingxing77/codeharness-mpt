"""S17 门禁（B2 + 治理 §3 第 1 条）：`runner.answer_human` 不得抢槽，且断点态不得被收尾抹掉。

钉四件事：
  t1 **前置核对**（总文档 §B2 要求先核的那条）：图 interrupt 后 `_run` 走 `finally` 把
     `tasks[sid]` 清出 → 人工回答时刻 `is_running()` 已是 False，所以守卫用 `is_running` 就够，
     不需要「按状态分流」。收尾状态自 A 项起**必须**仍是 awaiting_human（旧写法在这儿
     无条件写 finished，读数见 t4 的对照组）。
  t2 runner 级：会话真在跑（有活任务）时 `answer_human` 必须返回 False 且**不换槽**；
     连点两次只起一个 resume 任务；无活任务（awaiting_human）时照常起。
  t3 HTTP 级：running 时 POST /api/sessions/{sid}/human-input → 409，且 tasks 槽未被覆盖。
  t4 落态半边（A 项新增）：停在断点 → store 仍是 awaiting_human、graphs 留着供 resume、
     `stop()` 就地落 stopped（不是钉死在 stopping）、`start` 回 409 且文案说清去路；
     正常收口（无 interrupt）→ 照旧 finished。对照组用修复前那三行原样跑，读数必须是
     finished——证明这条判据抓得住复发。
  t5/t6 真图真 gate（A4 之后新增，零花费）：审批卡驻留四格 + 批准真写盘/拒绝零副作用。
  t7 C15：审批面只 police `self.tools` 里的真工具——`end`/`RoleZero.*` 这类零副作用特殊命令
     不许挂起（真跑台账里那张 `tool:'end'` 的卡），同时**正向对照** `write_file` 照旧挂起。
  t8 C18①：启动自愈只抹 `running`；停在待批处的会话跨**进程**重启仍是可信驻留态且真能恢复
     （t4–t7 全在一个进程里，`heal_running()` 这条启动路径一次都没被问过）。

跑法：
  cd /e/Codeharness && PYTHONPATH=/e/Codeharness:/e/Codeharness/logs PYTHONIOENCODING=utf-8 \\
    PYTHONDONTWRITEBYTECODE=1 F:/anaconda/python.exe tests/s17_human_input_guard.py
"""
import asyncio
import tempfile
import threading
from pathlib import Path

from server.runner import SessionRunner
from server.events import SessionEventBus
from server.sessions import SessionStore, SessionStatus


def _make_runner():
    tmp = Path(tempfile.mkdtemp())
    store = SessionStore(path=tmp / "sessions.json")
    runner = SessionRunner(store, SessionEventBus())
    s = store.create("B2 冒烟", project_name=f"b2_{tmp.name}")
    return store, runner, s


class _Team:
    """假图：按需产出事件，`hold` 不释就不收口（用来模拟「会话真在跑」）。

    ⚠ 落态判据**不再吃 `astream_events` 的事件**——实测 langgraph 1.2.11 只发 `on_chain_*`，
    没有 `on_interrupt`（A4 真模型那批查出来的缺陷）。runner 现在问活图 `aget_state().tasks[*].interrupts`，
    所以这个替身得把那一面也装上，形状照真图（`SimpleNamespace(tasks=[…interrupts=[…]])`）。
    真图端到端的判据在 t5/t6，不吃这里的替身。"""

    def __init__(self, events=(), hold=None, interrupts=()):
        self.events, self.hold, self.interrupts = list(events), hold, list(interrupts)

    async def astream_events(self, _input, _config, version=None):
        for ev in self.events:
            yield ev
        if self.hold is not None:
            await self.hold.wait()

    async def aget_state(self, _config):
        from types import SimpleNamespace as NS
        if not self.interrupts:
            return NS(next=(), tasks=[])
        return NS(next=("gate",), tasks=[NS(interrupts=[NS(value=v) for v in self.interrupts])])


INTERRUPT = {"event": "on_interrupt", "data": {"chunk": None},
             "value": {"question": "要哪个方案？"}}
# 替身图的「停在待人工处」由 aget_state 表达（真事件不存在，见 _Team 注释）
PARKED = [{"question": "要哪个方案？"}]


def _install(runner, team, project):
    async def fake_prepare(session, proj, cost_manager):
        return team, {"configurable": {"thread_id": proj}}, {"messages": []}

    runner._prepare = fake_prepare
    runner.projects[project] = project


def t1_interrupt_clears_task_slot():
    """前置核对：interrupt 后 `_run` 是否已从 `tasks` 清出（决定守卫写法）。"""
    store, runner, s = _make_runner()
    _install(runner, _Team(interrupts=PARKED), s.project_name)

    async def body():
        task = asyncio.create_task(runner._run(s))
        runner.tasks[s.id] = task
        await task
        return runner.is_running(s.id), s.id in runner.tasks, store.get(s.id).status.value

    running_after, in_tasks, status = asyncio.run(body())
    assert running_after is False, "t1 核对失配：interrupt 后仍算在跑 → 守卫要改成按状态分流"
    assert in_tasks is False, "t1 核对失配：interrupt 后 tasks 里已留着活任务"
    # 状态那一格**不在这里判**：t1 喂的是合成事件 `{"event": "on_interrupt"}`，而实测
    # langgraph 1.2.11 的 astream_events(v2) 只发 on_chain_*——生产根本不走这条事件
    # （A4 真模型那批查出来的，主仓本次提交）。落态判据改打在**真图真 interrupt** 上：见 t5/t6。
    print(f"  ok  t1（核对读数：interrupt 收尾后 is_running={running_after}、tasks 已清出={in_tasks}；"
          f"合成事件下的 status={status} 不作判据，理由见上）")


class _Busy:
    def done(self):
        return False


def t2_no_slot_steal():
    """running 时 answer_human 必须 False 且不换槽；连点两次只起一个任务。"""
    store, runner, s = _make_runner()
    hold = asyncio.Event()
    _install(runner, _Team(hold=hold), s.project_name)
    resume_calls = []
    release = asyncio.Event()

    async def fake_resume(sid, content):
        resume_calls.append(content)
        await release.wait()

    runner._resume = fake_resume

    async def body():
        task = asyncio.create_task(runner._run(s))
        runner.tasks[s.id] = task
        await asyncio.sleep(0)                      # 让 _run 真正跑起来
        assert runner.is_running(s.id), "t2 前提：会话应在跑"
        orig = runner.tasks[s.id]
        ok_running = runner.answer_human(s.id, "抢槽")
        still_orig = runner.tasks[s.id] is orig
        hold.set()
        await task                                  # 原任务收尾（此刻 tasks 被 finally pop）
        ok_first = runner.answer_human(s.id, "第一次回答")
        first_ref = runner.tasks.get(s.id)
        ok_second = runner.answer_human(s.id, "第二次连点")
        release.set()
        await asyncio.gather(*[t for t in runner.tasks.values()], return_exceptions=True)
        return ok_running, still_orig, ok_first, ok_second, len(resume_calls), first_ref

    ok_running, still_orig, ok_first, ok_second, calls, first_ref = asyncio.run(body())
    assert ok_running is False, \
        "t2 失效：running 时 answer_human 仍返回 True——_resume 与 _run 抢同一个 tasks[sid] 槽，" \
        "先结束的一方 pop 掉另一方引用（is_running 误报 False、stop() 取消错对象）"
    assert still_orig, "t2 失效：tasks[sid] 的引用被覆盖，原 _run 任务失去句柄（跨 worker 停止失配）"
    assert ok_first is True, "t2 反证失败：无活任务时也该能 resume（awaiting_human 正常路径）"
    assert ok_second is False, "t2 连点两次起了两个任务"
    assert calls == 1, f"t2 resume 应只跑一次，实际 {calls} 次"
    print("  ok  t2")


def t3_endpoint_returns_409():
    """HTTP 半边：running 时 POST human-input → 409，且 tasks 槽未被覆盖。"""
    import server.sessions as ss
    from codeharness.configs.settings import settings

    keep_sess, keep_redis = ss.SESSIONS_FILE, settings.platform.use_redis
    ss.SESSIONS_FILE = Path(tempfile.mkdtemp()) / "sessions.json"
    settings.platform.use_redis = False
    try:
        from fastapi.testclient import TestClient
        from server.app import create_app
        with TestClient(create_app()) as c:
            sid = c.post("/api/sessions", json={"idea": "B2", "project_name": "b2_http"}).json()["id"]
            runner = c.app.state.runner
            busy = _Busy()
            runner.tasks[sid] = busy                  # 本 worker 视角：这条会话正在跑
            rsp = c.post(f"/api/sessions/{sid}/human-input", json={"content": "hi"})
            kept = runner.tasks.get(sid) is busy      # 拒收时也不许换槽
            del runner.tasks[sid]                     # 只动自己塞进去的桩
            assert rsp.status_code == 409, f"t3 期望 409，实际 {rsp.status_code} body={rsp.text[:120]}"
            assert kept, "t3 失效：409 之前 tasks 槽已被 _resume 任务覆盖"
            detail = rsp.json()["detail"]

            # 正方向：没有活任务（interrupt 收尾后的真实形态）时真 answer_human 必须照常接并起 resume
            resumed = []
            started = threading.Event()
            saved = runner._resume

            async def fake_resume(_sid, content):     # 替身：跑真 resume 要建真图 + 写 checkpointer
                resumed.append(content)
                started.set()

            runner._resume = fake_resume
            try:
                ok = c.post(f"/api/sessions/{sid}/human-input", json={"content": "方案 A"})
                started.wait(3)
                assert ok.status_code == 200, f"t3 正方向失配：{ok.status_code} body={ok.text[:120]}"
                assert resumed == ["方案 A"], f"t3 正方向没起 resume：{resumed}"
            finally:
                runner._resume = saved
                runner.tasks.pop(sid, None)
            print(f"  ok  t3（拒收 409 detail={detail!r}；空闲时 200 且 resume 被起起来）")
    finally:
        settings.platform.use_redis = keep_redis
        ss.SESSIONS_FILE = keep_sess


def t4_breakpoint_settles_and_stops():
    """A 项落态半边：断点驻留态可信 + 有明确去路 + 正常收口不受影响 + 对照组。"""
    from server.sessions import _now

    # ① 停在断点：状态驻留、graph 留给 resume、stop() 就地落 stopped
    store, runner, s = _make_runner()
    _install(runner, _Team(interrupts=PARKED), s.project_name)

    async def body():
        task = asyncio.create_task(runner._run(s))
        runner.tasks[s.id] = task
        await task
        parked = store.get(s.id).status.value
        kept_graph = s.id in runner.graphs
        stopped = await runner.stop(s.id)
        return parked, kept_graph, stopped, store.get(s.id).status.value

    parked, kept_graph, stopped, after_stop = asyncio.run(body())
    assert parked == "awaiting_human", f"t4①失效：断点收尾后状态={parked!r}"
    assert kept_graph, "t4①失效：停在断点就把 graph 散了，resume 无路"
    assert stopped is True and after_stop == "stopped", \
        f"t4①失效：对停在断点的会话点停止 → {after_stop!r}（转发 ch:ctl 没人接，会钉死在 stopping）"

    # ② 正常收口（没有 interrupt）：照旧 finished 并散会
    store2, runner2, s2 = _make_runner()
    _install(runner2, _Team(events=[]), s2.project_name)

    async def body2():
        task = asyncio.create_task(runner2._run(s2))
        runner2.tasks[s2.id] = task
        await task
        return store2.get(s2.id).status.value, s2.id in runner2.graphs

    normal, graph_after = asyncio.run(body2())
    assert normal == "finished" and not graph_after, \
        f"t4②失效：正常收口应为 finished 且散会，实际 {normal!r} / graph 留着={graph_after}"

    # ③ 对照组＝修复前原样（收尾无条件写 finished）：同一载体必须变红，否则这条判据抓不住复发
    store3, runner3, s3 = _make_runner()
    _install(runner3, _Team(interrupts=PARKED), s3.project_name)

    async def old_settle(sid):
        """修复前原样（收尾无条件写 finished）。现在是 `await _settle(...)` 的调用约定，
        对照组必须同为协程，否则「await None」的 TypeError 会把读数变成 failed，对照就失真了。"""
        sess = runner3.store.update(sid, status=SessionStatus.finished, finished_at=_now())
        runner3._publish_status(sess, "run completed")
        runner3._forget(sid, terminal=True)

    runner3._settle = old_settle

    async def body3():
        task = asyncio.create_task(runner3._run(s3))
        runner3.tasks[s3.id] = task
        await task
        return store3.get(s3.id).status.value

    clobbered = asyncio.run(body3())
    assert clobbered == "finished", \
        f"t4③对照组不成立：旧写法读数是 {clobbered!r}，那 ① 的断言就没有区分力"

    # ④ 端点半边：停在待人工处不许再 start（旧口径下状态被抹成 finished，这条放行过）
    import server.sessions as ss
    from codeharness.configs.settings import settings

    keep_sess, keep_redis = ss.SESSIONS_FILE, settings.platform.use_redis
    ss.SESSIONS_FILE = Path(tempfile.mkdtemp()) / "sessions.json"
    settings.platform.use_redis = False
    try:
        from fastapi.testclient import TestClient
        from server.app import create_app
        with TestClient(create_app()) as c:
            sid = c.post("/api/sessions", json={"idea": "A", "project_name": "a_park"}).json()["id"]
            c.app.state.runner.store.update(sid, status=SessionStatus.awaiting_human)
            rsp = c.post(f"/api/sessions/{sid}/start")
            assert rsp.status_code == 409, f"t4④失效：停在断点仍能 start，实际 {rsp.status_code}"
            detail = rsp.json()["detail"]
            assert "待人工" in detail, f"t4④文案没给去路：{detail!r}"
    finally:
        settings.platform.use_redis = keep_redis
        ss.SESSIONS_FILE = keep_sess
    print(f"  ok  t4（驻留 {parked!r}→stop→{after_stop!r}、正常收口 {normal!r}、"
          f"对照组旧写法 {clobbered!r}、start 409 detail={detail!r}）")


def _patch_gate_assembly(leader_script=None):
    """把 dynamic 线三个角色的模型换成 FakeLLM 剧本（零花费、零外网），返回还原函数。

    ⚠ 换的是 `codeharness.team.dynamic_assembly` 这个**模块属性**：`runner._prepare` 在调用时才
    `from codeharness.team import dynamic_assembly`，绑到调用前取好的名字上就换不掉。
    `leader_script` 换队长的剧本（C15 要用「一上来就 end」那一格），默认就是 write_file → end。
    队长第一跑的 `write_file` 参数与 t5/t6/t8 断言的文件名内容逐字绑定，改它要一起改。
    """
    import json
    import codeharness.team as team
    from codeharness.const import TEAMLEADER_NAME
    from codeharness.provider.fake import FakeLLM

    s_end = json.dumps({"thought": "结束", "commands": [{"command_name": "end", "args": {}}]})
    s_call = json.dumps({"thought": "写文件", "commands": [
        {"command_name": "write_file", "args": {"path": "probe.txt", "content": "hello-from-gate"}}]},
        ensure_ascii=False)
    script = leader_script if leader_script is not None else [s_call, s_end]
    orig = team.dynamic_assembly

    def patched(llm):
        agents, sop = orig(llm)
        agents[TEAMLEADER_NAME].llm = FakeLLM(script)
        agents[TEAMLEADER_NAME].ltm = None
        for n in ("Alice", "Bob"):
            agents[n].llm = FakeLLM([s_end])
            agents[n].ltm = None
        return agents, sop

    team.dynamic_assembly = patched
    return lambda: setattr(team, "dynamic_assembly", orig)


def _gate_runner(project: str, leader_script=None):
    """真图 + 真 gate：队长只调一次需要审批的 `write_file`。

    为什么必须有这两组：t1–t4 全打在替身图上，而 A4 真模型那批实测出
    **`astream_events(v2)` 里没有 `on_interrupt` 这条事件**——替身喂得出的事件，生产发不出来，
    于是「停在待批处被写成 finished、审批卡进不了活流」这个缺陷带着 s17 全绿活了很久（§0 硬约定 4）。
    """
    import shutil
    restore = _patch_gate_assembly(leader_script)
    tmp = Path(tempfile.mkdtemp())
    store = SessionStore(path=tmp / "sessions.json")
    runner = SessionRunner(store, SessionEventBus())

    async def none():
        return None
    runner._saver = none
    s = store.create("写一个文件", project_name=project, paradigm="dynamic", permission="readonly")

    def cleanup():
        restore()
        shutil.rmtree(Path("workspace") / project, ignore_errors=True)
        shutil.rmtree(tmp, ignore_errors=True)
    return store, runner, s, cleanup


def _settle_parked(store, runner, s):
    async def body():
        runner.start(s)
        for _ in range(200):
            await asyncio.sleep(0.05)
            if store.get(s.id).status.value != "running":
                break
        return store.get(s.id).status.value, s.id in runner.graphs
    return asyncio.run(body())


def t5_real_gate_interrupt():
    project = "s17_gate_park"
    store, runner, s, cleanup = _gate_runner(project)
    try:
        status, kept = _settle_parked(store, runner, s)
        events = [(e.kind, e.name) for e in runner.bus.history(s.id)]      # Event 是 pydantic 模型，直接点字段
        cards = [n for k, n in events if k == "approval"]
        from codeharness.runtime import CURRENT_PROJECT  # noqa: F401  会话目录由它定位
        written = list((Path("workspace") / project).rglob("probe.txt")) if (Path("workspace") / project).exists() else []
        assert status == "awaiting_human", f"t5①失效：真审批中断收尾后状态={status!r}（生产没有 on_interrupt 事件，判据只能问活图）"
        assert kept, "t5②失效：停在待批处就把 graph pop 了，resume 无路"
        assert cards == ["requested"] or cards.count("requested") >= 1, \
            f"t5③失效：活流里没有审批卡（前端只在切会话时 GET，用户看不到新卡）：{cards}"
        assert not written, f"t5④失效：没批就把文件写了 {written}——副作用必须在批准之后"
        print(f"  ok  t5 真图真 gate：状态驻留 awaiting_human、graph 留着、卡进了活流、零副作用")
    finally:
        for t in list(runner.tasks.values()):
            t.cancel()
        cleanup()


def t6_real_approve_and_reject():
    """批准 → 动作真执行（文件真落盘）；拒绝 → 一次副作用都不发生。A4 格②③ 的永久化。"""
    from platforms.approval_store import ApprovalStore

    for outcome, expect_file in (("allowed-once", True), ("rejected", False)):
        project = f"s17_gate_{outcome.replace('-', '_')}"
        store, runner, s, cleanup = _gate_runner(project)
        try:
            _settle_parked(store, runner, s)
            st = ApprovalStore(s.id)
            pending = st.pending()
            assert pending, f"t6 前置失配：{outcome} 这一格台账里没有待批项"
            aid = pending[0]["id"]
            decided = st.decide(aid, outcome)
            assert decided == outcome, f"t6 台账写不进结论：{decided!r}"

            async def resume_and_wait():
                """`answer_human` 内部 `create_task` → 必须在跑着的 loop 里调（t6 第一版踩了这点）。
                轮询也不能只看「不再 running」：resume 刚起来时状态还是 awaiting_human，
                第一版立刻 break 就把「文件其实写了」判成失败。等的是**结果**，不是状态翻转。"""
                started = runner.answer_human(s.id, aid)
                dir_ = Path("workspace") / project
                end = asyncio.get_running_loop().time() + 30
                while asyncio.get_running_loop().time() < end:
                    if dir_.exists() and list(dir_.rglob("probe.txt")):
                        break
                    if store.get(s.id).status.value in ("finished", "failed", "stopped"):
                        break
                    await asyncio.sleep(0.1)
                return started

            assert asyncio.run(resume_and_wait()), f"t6 {outcome}：resume 没起来"
            files = list((Path("workspace") / project).rglob("probe.txt")) \
                if (Path("workspace") / project).exists() else []
            if expect_file:
                assert files, "t6 批准格失效：批了却没真执行，文件不在"
                assert files[0].read_text(encoding="utf-8").strip() == "hello-from-gate", files[0].read_text()[:60]
            else:
                assert not files, f"t6 拒绝格失效：被拒的动作还是执行了 {files}"
            print(f"  ok  t6 {outcome} → {'真写盘（内容逐字对）' if expect_file else '零副作用（同一参数不重问）'}")
        finally:
            for t in list(runner.tasks.values()):
                t.cancel()
            cleanup()


def t7_special_commands_never_park():
    """C15：审批面只管 `self.tools` 里的真工具，`end`/`RoleZero.*` 这类零副作用特殊命令不许挂起。

    A4 真模型那批的读数就是这条：台账里出现 `tool:'end'`、`args_preview="end: {}"` 的待批项。
    根因是 `_gate_commands` 对模型给的每条命令**无差别**送 `gate_decide`，而 `TOOL_TIER` 没登记
    它们 → fail-closed 判成 `full_access`。`_act` 里 `end`/`RoleZero.*`/`Plan.*`/`publish_*`
    全走特殊分支、根本不进 `self.tools`，一次副作用都不会发生。
    两格都打在真图真台账上（不许合成事件，理由见 `_gate_runner` 的注释）：
      格①**阳性对照**：`write_file` 照旧挂起，批完之后队长第二轮的 `end` **不再冒第二张卡**、
        会话正常收口（修复前：批完 write_file → `end` 又挂一张 → 状态停在 awaiting_human，
        这一格就是 t6 批准格踩完文件就 break、没看状态而漏掉的那半张脸）；
      格②队长一上来就 `reply_to_human` + `end` → 零张卡、直接 finished。
    """
    from platforms.approval_store import ApprovalStore

    def cards(st):
        return [it["tool"] for it in st.pending() + st.settled()]

    def drop(*sts):
        """台账落在共享 db0（`ApprovalStore` 只有 Redis 一台，t5/t6 同路），
        键名带随机 sid 不会撞别人的数据，但自起的东西自己收口。"""
        for st in sts:
            st.r.delete(st.key, st.dkey)

    # 格①：真工具仍挂起，批完到 end 不再挂
    project = "s17_c15_tool_then_end"
    store, runner, s, cleanup = _gate_runner(project)
    st = None
    try:
        parked, _ = _settle_parked(store, runner, s)
        st = ApprovalStore(s.id)
        assert parked == "awaiting_human" and cards(st) == ["write_file"], \
            f"格①前置失配（真工具不挂起就没资格谈豁免）：status={parked!r} cards={cards(st)}"
        aid = st.pending()[0]["id"]
        st.decide(aid, "allowed-once")

        async def resume_and_finish():
            runner.answer_human(s.id, aid)
            end = asyncio.get_running_loop().time() + 30
            while asyncio.get_running_loop().time() < end:
                if store.get(s.id).status.value in ("finished", "failed", "stopped"):
                    break
                await asyncio.sleep(0.1)
            return store.get(s.id).status.value, cards(st)

        status, after = asyncio.run(resume_and_finish())
        assert status == "finished", \
            f"格①失效：批完 write_file 后停在 {status!r}（cards={after}）——" \
            f"`end` 又被送去审批了，这就是 A4 台账里那张 `tool:'end'` 的卡"
        assert after == ["write_file"], f"格①失效：冒出了第二张卡 {after}"
        print(f"  ok  t7 格①真工具照挂、批完不再为 end 挂卡（cards={after}、status={status}）")
    finally:
        for t in list(runner.tasks.values()):
            t.cancel()
        if st:
            drop(st)
        cleanup()

    # 格②：特殊命令整族不挂起
    import json
    s_special = json.dumps({"thought": "回一句就收工", "commands": [
        {"command_name": "RoleZero.reply_to_human", "args": {"content": "已经写好了"}},
        {"command_name": "end", "args": {}}]}, ensure_ascii=False)
    project2 = "s17_c15_special_only"
    store2, runner2, s2, cleanup2 = _gate_runner(project2, leader_script=[s_special])
    st2 = None
    try:
        status2, _ = _settle_parked(store2, runner2, s2)
        st2 = ApprovalStore(s2.id)
        got2 = cards(st2)
        assert status2 == "finished" and got2 == [], \
            f"格②失效：只回话+收口竟然挂起 {got2}、状态 {status2!r}"
        print(f"  ok  t7 格②reply_to_human+end 零张卡直接收口（status={status2}）")
    finally:
        for t in list(runner2.tasks.values()):
            t.cancel()
        if st2:
            drop(st2)
        cleanup2()


# ---------------- t8（C18①）：启动自愈不许放弃停在待批处的那一场 ----------------
def _sandbox_db():
    """门禁的会话态钉在 db15（PLAN §0 环境铁律：db0 一个键都不读写），与 s7 的 `TEST_DB` 同一条。"""
    from codeharness.configs.settings import RedisConfig, settings
    return RedisConfig(host=settings.redis.host, port=settings.redis.port, db=15)


def _purge(sid: str):
    """自起的键自己收口。手工删键绕过了 API 的删除路径，`ch:index` 的成员得自己 ZREM（ADR-07 的补法）。"""
    import redis
    from platforms.session_store import INDEX
    r = redis.Redis.from_url(_sandbox_db().to_url(), decode_responses=True)
    ks = [k for k in r.scan_iter("*") if sid in k]
    if ks:
        r.delete(*ks)
    r.zrem(INDEX, sid)


def _worker(phase: str, root: Path, project: str, sid: str = "") -> dict:
    """t8 第二格必须跨**进程**：单进程里「启动自愈」和「重启后重建图」两件事一件都发生不了。

    `park` = 真图真 gate 挂出审批卡后进程退出（=服务死掉）；`resume` = 全新进程 `create_app()`
    （启动期真跑自愈）走真 HTTP 面把它恢复掉。两个进程都把 `WORKSPACE_ROOT` 指到 tmp，断点因此
    落在 `<root>/storage/checkpoints.db`——与生产 `default_checkpoint_path` 同一个形状，跑完随 tmp 没。
    （产物目录 `workspace/<project>` 是工具层按 cwd 算的，跟 t5/t6 一样另清。）
    """
    import server.settings as srv_settings
    from codeharness.configs.settings import settings
    settings.platform.use_redis = True
    root.mkdir(parents=True, exist_ok=True)
    srv_settings.WORKSPACE_ROOT = root        # `runner._saver` 调用时才读这个属性，改这里就改了断点位置
    restore = _patch_gate_assembly()
    try:
        if phase == "park":
            from platforms.event_store import RedisEventBus
            from platforms.session_store import RedisSessionStore
            from server.runner import SessionRunner
            db = _sandbox_db()
            store, bus = RedisSessionStore(db), RedisEventBus(db)
            runner = SessionRunner(store, bus)

            async def go():
                bus.start()
                s = store.create("写一个文件", project_name=project, paradigm="dynamic",
                                 permission="readonly")
                runner.start(s)
                status = "timeout"
                for _ in range(600):                       # 60s 还不收口就是挂了，别把门禁一起卡住
                    await asyncio.sleep(0.1)
                    status = store.get(s.id).status.value
                    if status not in ("created", "running"):
                        break
                from codeharness.environment.checkpoint import close_all
                await close_all()                         # 断点得真写进 sqlite 文件，WAL 不能留在将死的进程里
                await bus.aclose()
                return {"sid": s.id, "parked": status}
            return asyncio.run(go())

        import time
        import server.app as sa
        sa.WORKSPACE_ROOT = root                          # create_app 的 mkdir/静态挂载在建 app 时读它
        from fastapi.testclient import TestClient
        from server.app import create_app
        target = Path("workspace") / project / "probe.txt"
        out: dict = {}
        with TestClient(create_app()) as c:
            out["after_boot"] = c.get(f"/api/sessions/{sid}").json()["status"]
            appr = c.get(f"/api/sessions/{sid}/approvals").json()
            out["pending"] = len(appr["pending"])
            if not appr["pending"]:
                return out                                # 没有卡就没资格谈恢复
            aid = appr["pending"][0]["id"]
            r = c.post(f"/api/sessions/{sid}/approvals/{aid}/respond", json={"outcome": "allowed-once"})
            out["respond"] = r.status_code
            out["ok"] = r.json().get("ok") if r.status_code == 200 else None
            final = ""
            for _ in range(600):
                time.sleep(0.1)
                if "file" not in out and target.exists():
                    out["file"] = target.read_text(encoding="utf-8").strip()
                final = c.get(f"/api/sessions/{sid}").json()["status"]
                if "file" in out and final in ("finished", "failed", "stopped"):
                    break                                 # 等的是**结果**，不是状态翻转（t6 同一课）
            out["final"] = final
        return out
    finally:
        restore()


def _t8a_self_heal_only_touches_running():
    """格① 两处启动自愈都只抹 running。

    它们是两份代码（Redis 的 `RedisSessionStore.heal_running()` / 进程内 `SessionStore.__init__`
    重载时的自愈），只钉一份另一份会漂。其中 `running → stopped` 那半是**阳性对照**：
    自愈不许整个失效，否则这条判据恒绿。
    """
    import shutil
    from platforms.session_store import RedisSessionStore
    tmp = Path(tempfile.mkdtemp()) / "sessions.json"
    st = RedisSessionStore(_sandbox_db())
    a = st.create("自愈该抹", project_name="s17_c18_run")
    b = st.create("自愈不该抹", project_name="s17_c18_park")
    try:
        st.update(a.id, status=SessionStatus.running)
        st.update(b.id, status=SessionStatus.awaiting_human)
        st.heal_running()
        assert st.get(a.id).status == SessionStatus.stopped, \
            f"格①阳性对照不成立：running 没被自愈（{st.get(a.id).status.value!r}）——这条判据抓不到任何东西"
        assert st.get(b.id).status == SessionStatus.awaiting_human, \
            f"格①失效：`heal_running()` 把待批会话抹成了 {st.get(b.id).status.value!r}（C18①）"

        s0 = SessionStore(path=tmp)
        x = s0.create("自愈该抹", project_name="s17_c18_run")
        y = s0.create("自愈不该抹", project_name="s17_c18_park")
        s0.update(x.id, status=SessionStatus.running)
        s0.update(y.id, status=SessionStatus.awaiting_human)
        s1 = SessionStore(path=tmp)                        # 重新加载同一份 JSON 就是「重启」
        assert s1.get(x.id).status == SessionStatus.stopped, "格①失效：进程内 store 不再抹 running，两条实现漂了"
        assert s1.get(y.id).status == SessionStatus.awaiting_human, \
            f"格①失效：进程内 `SessionStore.__init__` 把待批抹成了 {s1.get(y.id).status.value!r}（与 Redis 版同罪）"
        print("  ok  t8 格①两处自愈只抹 running、待批原样留着（Redis 与进程内各判一次）")
    finally:
        _purge(a.id)
        _purge(b.id)
        shutil.rmtree(tmp.parent, ignore_errors=True)


def _t8b_cross_process_recovery():
    """格② 跨**进程**重启后，停在待批处的那一场还得真能恢复。

    A4 真模型那批只证到「卡还在 `ch:appr:{sid}`、`respond` 回 200、`_resume` 起跑」，而起跑不等于
    跑完；这里取完整读数：GET 仍是 awaiting_human → pending 非空 → respond → **动作真落盘** → 收口
    finished。零花费（FakeLLM 剧本，真图真 gate，同 t5/t6/t7）。
    """
    import json
    import os
    import shutil
    import subprocess
    import sys
    root = Path(tempfile.mkdtemp()) / "ws"
    project = f"s17_c18_{os.urandom(3).hex()}"
    me = str(Path(__file__).resolve())
    env = dict(os.environ, PLATFORM__USE_REDIS="1", REDIS__DB="15", PYTHONUNBUFFERED="1")

    def run(*args):
        p = subprocess.run([sys.executable, "-B", me, "--worker", *args], capture_output=True,
                           text=True, env=env, timeout=300, cwd=str(Path(me).parent.parent))
        line = next((l for l in p.stdout.splitlines() if l.startswith("RESULT ")), "")
        assert line, (f"格② 子进程没出读数：args={args} rc={p.returncode}\n"
                      f"stdout 末段={p.stdout[-600:]!r}\nstderr 末段={p.stderr[-600:]!r}")
        return json.loads(line[len("RESULT "):])

    sid = ""
    try:
        parked = run("park", str(root), project)
        sid = parked["sid"]
        assert parked["parked"] == "awaiting_human", f"格② 前置失配：子进程 A 没停在待批处：{parked}"
        after = run("resume", str(root), project, sid)
        assert after["after_boot"] == "awaiting_human", \
            f"格②失效：重启后 GET 该会话是 {after['after_boot']!r}——启动自愈抹掉了可信驻留态，" \
            f"而那张卡还在台账里（C18①，用户看得见卡却永远等不到这一场）"
        assert after["pending"] >= 1, f"格②失效：重启后台账里没有待批项（该在 ch:appr:{sid}）：{after}"
        assert after["respond"] == 200 and after["ok"] is True, f"格② respond 没被接住：{after}"
        assert after.get("file") == "hello-from-gate", \
            f"格②失效：批了没真执行——跨进程重建的图没续上 checkpointer 里的断点：{after}"
        assert after["final"] == "finished", f"格②失效：恢复后没收口，停在 {after['final']!r}"
        print(f"  ok  t8 格②跨进程重启：GET 仍 awaiting_human、pending={after['pending']}、"
              f"respond 后动作真落盘、收口 {after['final']}")
    finally:
        if sid:
            _purge(sid)
        shutil.rmtree(root.parent, ignore_errors=True)
        shutil.rmtree(Path("workspace") / project, ignore_errors=True)


def t8_restart_keeps_parked_session():
    """C18① 的两格（拆成两个函数只为反向验证能各自单独取证，见 `plan/` 里那轮变异读数）。"""
    _t8a_self_heal_only_touches_running()
    _t8b_cross_process_recovery()


def main():
    checks = [t1_interrupt_clears_task_slot, t2_no_slot_steal, t3_endpoint_returns_409,
              t4_breakpoint_settles_and_stops, t5_real_gate_interrupt, t6_real_approve_and_reject,
              t7_special_commands_never_park, t8_restart_keeps_parked_session]
    for f in checks:
        f()
    print(f"\nS17 门禁通过：{len(checks)} 组 —— interrupt 后 tasks 清出核对 1 组 + "
          f"不抢槽/连点幂等 1 组 + human-input 409 端点半边 1 组 + 断点落态/停止/start 1 组 + "
          f"真图真审批卡驻留四格 1 组 + 批准真执行/拒绝不执行 1 组 + "
          f"特殊命令不进审批面 1 组 + 跨进程重启仍驻留且真恢复 1 组")


if __name__ == "__main__":
    import json
    import sys
    if "--worker" in sys.argv:                        # t8 格② 的子进程模式，见 `_worker`
        a = sys.argv[sys.argv.index("--worker") + 1:]
        print("RESULT " + json.dumps(_worker(a[0], Path(a[1]), a[2], a[3] if len(a) > 3 else ""),
                                     ensure_ascii=False), flush=True)
    else:
        main()
