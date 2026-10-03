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
  t9 C18②：会话被打成 `failed` 那一刻留可 grep 的告警（`[session-failed]`）+ 事件流可见，
     正常收口不打（阳性对照）；批准后那一发 `_resume` 的路径单独判一次。
     C116/C147 的分类判据也长在这一格里（④拦停 ⑤崩溃对照 ⑥超窗 ⑦同码坏请求对照）——三族共用
     同一行 `[session-failed]`，所以只在这一格里加档，不另立组。

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
    真图端到端的判据在 t5/t6，不吃这里的替身。
    `boom` = 事件流走到一半时抛出的异常（t9 用它模拟「批准后那一发遇连接失败」）。"""

    def __init__(self, events=(), hold=None, interrupts=(), boom=None):
        self.events, self.hold, self.interrupts, self.boom = list(events), hold, list(interrupts), boom

    async def astream_events(self, _input, _config, version=None):
        for ev in self.events:
            yield ev
        if self.hold is not None:
            await self.hold.wait()
        if self.boom is not None:
            raise self.boom

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
    async def fake_prepare(session, proj, cost_manager, persist_roles=True):
        # persist_roles 是 B7 之后的新形参（只读回放不注册、不回写 roles）——替身要跟生产同签名
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


def _patch_gate_assembly(leader_script=None, on_agents=None):
    """把 dynamic 线三个角色的模型换成 FakeLLM 剧本（零花费、零外网），返回还原函数。

    ⚠ 换的是 `codeharness.team.dynamic_assembly` 这个**模块属性**：`runner._prepare` 在调用时才
    `from codeharness.team import dynamic_assembly`，绑到调用前取好的名字上就换不掉。
    `leader_script` 换队长的剧本（C15 要用「一上来就 end」那一格），默认就是 write_file → end。
    队长第一跑的 `write_file` 参数与 t5/t6/t8 断言的文件名内容逐字绑定，改它要一起改。
    `on_agents`（C59 t11 用）：装配好的三个角色交出去给调用方挂钩子（那格要把 `write_file` 包一层计数器），
    默认 None = 不挂，既有调用一律不受影响。
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
        if on_agents is not None:
            on_agents(agents)
        return agents, sop

    team.dynamic_assembly = patched
    return lambda: setattr(team, "dynamic_assembly", orig)


def _gate_runner(project: str, leader_script=None, permission: str = "readonly", on_agents=None):
    """真图 + 真 gate：队长只调一次需要审批的 `write_file`。

    为什么必须有这两组：t1–t4 全打在替身图上，而 A4 真模型那批实测出
    **`astream_events(v2)` 里没有 `on_interrupt` 这条事件**——替身喂得出的事件，生产发不出来，
    于是「停在待批处被写成 finished、审批卡进不了活流」这个缺陷带着 s17 全绿活了很久（§0 硬约定 4）。
    `permission` / `on_agents` 是 C59 t11 加的两个口（默认值与既有调用逐字一致）：
    那格要 `full_access`（免得审批闸口先插一脚）并把 `write_file` 包一层计数器。
    """
    import shutil
    restore = _patch_gate_assembly(leader_script, on_agents)
    tmp = Path(tempfile.mkdtemp())
    store = SessionStore(path=tmp / "sessions.json")
    runner = SessionRunner(store, SessionEventBus())

    async def none():
        return None
    runner._saver = none
    s = store.create("写一个文件", project_name=project, paradigm="dynamic", permission=permission)

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


def _worker(phase: str, root: Path, project: str, sid: str = "", script_kind: str = "approve") -> dict:
    """t8 第二格必须跨**进程**：单进程里「启动自愈」和「重启后重建图」两件事一件都发生不了。

    `park` = 真图真 gate 挂出审批卡后进程退出（=服务死掉）；`resume` = 全新进程 `create_app()`
    （启动期真跑自愈）走真 HTTP 面把它恢复掉。两个进程都把 `WORKSPACE_ROOT` 指到 tmp，断点因此
    落在 `<root>/storage/checkpoints.db`——与生产 `default_checkpoint_path` 同一个形状，跑完随 tmp 没。
    （产物目录 `workspace/<project>` 是工具层按 cwd 算的，跟 t5/t6 一样另清。）
    `script_kind="ask"`（t12②）：停车点不是审批卡而是 `ask_human`——剧本 `[ask_human, write_file, end]`
    加 `full_access`（没有审批卡，唯一的 interrupt 来自 ask），量的是「游标/pending_ask 进了 checkpointer、
    重启后续得上」而不是「审批卡还在台账里」。
    """
    import server.settings as srv_settings
    from codeharness.configs.settings import settings
    settings.platform.use_redis = True
    root.mkdir(parents=True, exist_ok=True)
    srv_settings.WORKSPACE_ROOT = root        # `runner._saver` 调用时才读这个属性，改这里就改了断点位置
    import json as _json
    if script_kind == "ask":
        leader = [_json.dumps({"thought": "先问人再动手", "commands": [
            {"command_name": "RoleZero.ask_human", "args": {"question": "重启后还能续上吗？"}},
            {"command_name": "write_file", "args": {"path": "after_ask.txt", "content": "RESUMED-OK"}},
            {"command_name": "end", "args": {}}]}, ensure_ascii=False)]
        permission = "full_access"
    else:
        leader, permission = None, "readonly"
    restore = _patch_gate_assembly(leader)
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
                                 permission=permission)
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
                return {"sid": s.id, "parked": status,
                        "file_before": (Path("workspace") / project / "after_ask.txt").exists()}
            return asyncio.run(go())

        import time
        import server.app as sa
        sa.WORKSPACE_ROOT = root                          # create_app 的 mkdir/静态挂载在建 app 时读它
        from fastapi.testclient import TestClient
        from server.app import create_app
        target = Path("workspace") / project / ("after_ask.txt" if script_kind == "ask" else "probe.txt")
        out: dict = {}
        with TestClient(create_app()) as c:
            out["after_boot"] = c.get(f"/api/sessions/{sid}").json()["status"]
            appr = c.get(f"/api/sessions/{sid}/approvals").json()
            out["pending"] = len(appr["pending"])
            if script_kind == "ask":
                # 停在 ask 上没有审批卡：恢复走 human-input（普通回答，`_ask` 的 interrupt 直接吃它）
                r = c.post(f"/api/sessions/{sid}/human-input", json={"content": "继续"})
                out["respond"] = r.status_code
            else:
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


def t9_failure_is_loud():
    """C18②：会话被打成 `failed` 那一刻必须留下**可 grep 的告警**，事件流里也看得见。

    缺陷原文是「批准之后那一发遇连接失败，整场直接 `failed`、零重试零告警」——取证后**「零重试」那句
    不成立**：非流式的模型调用走 `_acall`，连接类失败按 ADR-06 保留重发（实测桩收 3 次，判据在
    `tests/s2_gateway.py::t14`）；真正缺的是重发用尽之后的那声响。所以这一格只判 `_fail`：
      ① `_run` 那一发失败 → 状态 `failed` + 日志有 `[session-failed] sid=…` + 事件流有 `kind=error`；
      ② **阳性对照**：正常收口的会话不许出现那行告警（否则它恒亮，grep 等于没有）；
      ③ `_resume`（批准后那一发）单独判一次——它是被看见的那条路，与 `_run` 共用 `_fail` 但入口不同；
      ④ C116：供应侧拦停（真 `openai.APIStatusError` 451/`censorship_blocked`）⇒ 日志同一行里带
         `kind=blocked`、会话 `error` 既有人话也保留原始原因，而 `[session-failed]` 前缀一字不改；
      ⑤ C116 反向对照：普通崩溃 ⇒ `kind=crash`，且 `error` 文案与改前**逐字相同**（分类器不许见谁都甩锅外部）；
      ⑥ C147：超窗（真 `APIStatusError` 400/`context_length_exceeded` + `maximum context length`）⇒
         `kind=context_overflow`、`error` 既有人话也保留原始原因，`[session-failed]` 前缀照旧一字不改；
      ⑦ C147 反向对照：**同码 400** 但不含 context 字样（`invalid_request_error` 那种我们自己的调用写坏了）
         ⇒ 仍 `kind=crash` 且文案逐字相同——这一格防的是「见 400 就甩锅窗口」，是 ⑥ 的牙。
    异常用真的 `openai.APIConnectionError`：缺陷台账里那行的 `error=APIConnectionError` 就是这么来的。
    """
    import io
    from codeharness.logs import logger
    from openai import APIConnectionError
    import httpx

    boom = APIConnectionError(request=httpx.Request("POST", "http://127.0.0.1:1/v1/chat/completions"))

    def capture(fn):
        """只收 ERROR 级：告警用的是 `logger.error`，收全部级别会让任何一条旧 error 都算通过。
        ⚠ 顺序：先跑完再取 buffer——`return buf.getvalue(), fn()` 会按元组从左到右求值，
        在 fn 之前就把空 buffer 快照走了（第一版就这样「日志里零痕迹」假红了一次）。"""
        buf = io.StringIO()
        hid = logger.add(buf, format="{message}", level="ERROR")
        try:
            got = fn()
            return buf.getvalue(), got
        finally:
            logger.remove(hid)

    def one(boom_exc=None):
        """跑一版真 runner（替身图），返回 (状态, error 字段, 事件 kinds)。"""
        store, runner, s = _make_runner()
        _install(runner, _Team(boom=boom_exc), s.project_name)

        async def body():
            task = asyncio.create_task(runner._run(s))
            runner.tasks[s.id] = task
            await asyncio.gather(task, return_exceptions=True)
            return (store.get(s.id).status.value, store.get(s.id).error,
                    [(e.kind, e.name) for e in runner.bus.history(s.id)])
        return asyncio.run(body())

    log_failed, (status, err, kinds) = capture(lambda: one(boom_exc=boom))
    assert status == "failed", f"t9① 前提失配：状态是 {status!r}"
    assert "APIConnectionError" in (err or ""), f"t9① 失败原因没进会话记录：{err!r}"
    assert "[session-failed]" in log_failed, \
        f"t9① 失效：整场死了日志里零痕迹（运维 grep 不到）：{log_failed[-200:]!r}"
    assert any(k == "error" for k, _n in kinds), f"t9① 失效：事件流里没有 error：{kinds}"

    log_ok, (status_ok, _e2, _k2) = capture(one)      # 没有 boom、没有 interrupt → 正常收口 finished
    assert status_ok == "finished" and "[session-failed]" not in log_ok, \
        f"t9② 阳性对照不成立：正常收口也打了告警（status={status_ok!r}）——那行字恒亮就等于没有"

    # ③ 批准后那一发：先停在待批处，再 answer_human → `_resume` → 同一处 `_fail`。
    #    `boom` 必须在 resume 之前才装上——替身图与 `_run`/`_resume` 是同一个对象，
    #    第一跑就抛的话会话根本停不下来（前置就假了）。
    store3, runner3, s3 = _make_runner()
    team3 = _Team(interrupts=PARKED)
    _install(runner3, team3, s3.project_name)

    async def body3():
        task = asyncio.create_task(runner3._run(s3))
        runner3.tasks[s3.id] = task
        await task                                     # 停在待批处（graphs 留着供 resume）
        parked = store3.get(s3.id).status.value
        team3.boom = boom
        started = runner3.answer_human(s3.id, "答案")
        await asyncio.gather(runner3.tasks[s3.id], return_exceptions=True)
        return (parked, started, store3.get(s3.id).status.value, store3.get(s3.id).error,
                [(e.kind, e.name) for e in runner3.bus.history(s3.id)])

    log3, (parked, started, after, err3, kinds3) = capture(lambda: asyncio.run(body3()))
    assert parked == "awaiting_human", f"t9③ 前置失配：没停在待批处（{parked!r}）"
    assert started is True, "t9③ 前置失配：answer_human 没接（resume 没起）"
    assert after == "failed" and "APIConnectionError" in (err3 or ""), \
        f"t9③ 失效：批准后那一发失败后状态是 {after!r} err={err3!r}"
    assert "[session-failed]" in log3, \
        f"t9③ 失效：批准后那一发死了，日志里没有那行告警（C18② 原症状）：{log3[-200:]!r}"
    assert any(k == "error" for k, _n in kinds3), f"t9③ 失效：活流里没有 error 事件：{kinds3}"
    # ④⑤ C116：供应侧拦停（451 那一形）与编排崩溃**必须分得开**，而分的方式不许动 C18② 那行前缀。
    #    现证过的形状取自 10-01 22:46 那场真 StepFun 的日志原文（`APIStatusError: Error code: 451 -
    #    {'error': {'message': 'The content you provided or machine outputted is blocked.',
    #    'type': 'censorship_blocked'}}`）——用真异常类，不拿自造的 duck-type 糊。
    from openai import APIStatusError
    resp451 = httpx.Response(451, request=httpx.Request("POST", "http://127.0.0.1:1/v1/chat/completions"))
    blocked = APIStatusError(
        "Error code: 451 - {'error': {'message': 'The content you provided or machine outputted is blocked.',"
        " 'type': 'censorship_blocked'}}",
        response=resp451,
        body={"error": {"message": "The content you provided or machine outputted is blocked.",
                        "type": "censorship_blocked"}})

    log_b, (st_b, err_b, kinds_b) = capture(lambda: one(boom_exc=blocked))
    assert st_b == "failed", f"t9④ 前提失配：拦停场状态是 {st_b!r}（本件不改状态枚举）"
    assert "[session-failed]" in log_b, "t9④ 把 C18② 那行前缀改掉了（运维 grep 不到＝分类反把告警弄丢）"
    assert "kind=blocked" in log_b, f"t9④ 拦停没被分出来：{log_b[-220:]!r}"
    assert "供应侧拦停" in (err_b or ""), f"t9④ 用户那半没成人话（还是裸 repr？）：{err_b!r}"
    assert "censorship_blocked" in (err_b or ""), f"t9④ 分类把原始原因丢了（诊断价值没了）：{err_b!r}"
    assert any(k == "error" for k, _n in kinds_b), f"t9④ 拦停场活流里没有 error 事件：{kinds_b}"

    # ⑤ 反向对照：普通崩溃**不许**被糊成拦停——这一格防的就是「分类器见谁都说是外部问题」
    assert "kind=crash" in log_failed, f"t9⑤ 崩溃被误分类（日志行尾字段不是 crash）：{log_failed[-220:]!r}"
    assert "供应侧拦停" not in (err or ""), f"t9⑤ 崩溃的 error 被人话污染：{err!r}"
    assert err == f"APIConnectionError: {boom}", f"t9⑤ 崩溃文案被改了（应与改前逐字相同）：{err!r}"

    # ⑥⑦ C147：超窗单列一族，但**只有带窗口字样的 400** 才算，同码的其它 400 仍旧是我们的崩溃。
    #    ⚠ ⑥ 的形状取自 OpenAI 兼容口的公开口径，**没在真端点上现证过**——本仓在这台模型上从没真撞过窗
    #    （现取到的最大单发是 09-25 那笔 pt=25,216，`logs/20260925.txt:4363`）。真撞窗那一发原文一到就换掉它。
    resp400 = httpx.Response(400, request=httpx.Request("POST", "http://127.0.0.1:1/v1/chat/completions"))
    overflow = APIStatusError(
        "Error code: 400 - {'error': {'message': \"This model's maximum context length is 128000 tokens. "
        "However, your messages resulted in 145000 tokens\", 'type': 'context_length_exceeded'}}",
        response=resp400,
        body={"error": {"message": "This model's maximum context length is 128000 tokens. "
                                   "However, your messages resulted in 145000 tokens",
                        "type": "context_length_exceeded"}})
    badreq = APIStatusError(
        "Error code: 400 - {'error': {'message': 'messages[0].content: expected string, got list', "
        "'type': 'invalid_request_error'}}",
        response=resp400,
        body={"error": {"message": "messages[0].content: expected string, got list",
                        "type": "invalid_request_error"}})

    log_o, (st_o, err_o, kinds_o) = capture(lambda: one(boom_exc=overflow))
    assert st_o == "failed", f"t9⑥ 前提失配：超窗场状态是 {st_o!r}（本件不改状态枚举）"
    assert "[session-failed]" in log_o, "t9⑥ 把 C18② 那行前缀改掉了（运维 grep 不到＝分类反把告警弄丢）"
    assert "kind=context_overflow" in log_o, f"t9⑥ 超窗没被分出来：{log_o[-220:]!r}"
    assert "上下文超出" in (err_o or ""), f"t9⑥ 用户那半没成人话（还是裸 repr？）：{err_o!r}"
    assert "maximum context length" in (err_o or ""), f"t9⑥ 分类把原始原因丢了（诊断价值没了）：{err_o!r}"
    assert any(k == "error" for k, _n in kinds_o), f"t9⑥ 超窗场活流里没有 error 事件：{kinds_o}"

    # ⑦ 是 ⑥ 的牙：同码 400、不含窗口字样 ⇒ 必须仍 `kind=crash` 且文案逐字不动（少了这格，⑥ 就等于
    #    「凡是 400 都甩锅窗口」——而我们自己的调用写坏了恰恰也是 400）。
    log_x, (st_x, err_x, _kx) = capture(lambda: one(boom_exc=badreq))
    assert st_x == "failed", f"t9⑦ 前提失配：坏请求场状态是 {st_x!r}"
    assert "kind=crash" in log_x, f"t9⑦ 普通的 400 被误分成超窗（该记我们的崩溃）：{log_x[-220:]!r}"
    assert "上下文超出" not in (err_x or ""), f"t9⑦ 400 崩溃的 error 被人话污染：{err_x!r}"
    assert err_x == f"APIStatusError: {badreq}", f"t9⑦ 400 崩溃文案被改了（应与改前逐字相同）：{err_x!r}"

    print(f"  ok  t9（失败留 [session-failed] 告警 + error 事件、正常收口不打；"
          f"批准后那一发同判：{after}；C116 分类：拦停→kind=blocked+人话、崩溃→kind=crash 且文案一字未改；"
          f"C147 分类：超窗→kind=context_overflow+人话、同码坏请求→kind=crash 且文案一字未改）")


def t10_unknown_command_is_countable():
    """T4-③：模型吐「本轮工具面里没有的命令」必须留下一行可 grep 的话——线上唯一数得出的召回信号。

    为什么是日志不是指标：判据「命中率不降」要真值才算得出来，线上没有真值（PLAN §4 C6 行末定档 (a)，
    这套裁剪今天不上线）。但这一条量的不是命中率——是**代价**：吐一次未知命令就多一轮回喂、多烧一发
    （实测 ¥0.12–0.27）。纪律照 `[session-failed]`（t9）：留话、不抛、会话继续走。
    两格：① 真未知命令 → 日志有 `[unknown-command]` + 结果串照旧回喂 + 整场不许死；
          ② **阳性对照**：已知命令（`reply_to_human` + `end`）一条都不许打那行，否则它恒亮、grep 等于没有。
    为什么直接打 `_act` 不走真图：未知命令走的正是「不进 `self.tools`、不碰审批」那支，真图那条路
    t7 已经覆盖了；这里要判的是分支留下痕迹，绕开图反而少一层替身假绿。
    """
    import io

    from codeharness.logs import logger
    from codeharness.provider.fake import FakeLLM
    from codeharness.roles.role_zero import RoleZero

    role = RoleZero({"name": "Alice", "profile": "PM", "goal": "g"}, [], FakeLLM([""]))   # _act 不调模型，
    #     命令由 history 末条给进来；FakeLLM 只是构造子句柄（它 responses 空会在 _next 里 IndexError）

    def act(commands):
        buf = io.StringIO()
        hid = logger.add(buf, format="{message}", level="WARNING")   # 只收 WARNING 以上：收全部级别会让
        try:                                                        # 任何一条旧 warning 都算通过
            out = asyncio.run(role._act({"task": "把内容写进 note.txt", "history":
                                         [{"thought": "先写文件", "commands": commands}],
                                         "experience": "", "respond_language": "中文", "finished": False}))
            return buf.getvalue(), out
        finally:
            logger.remove(hid)

    log, out = act([{"command_name": "write_flie", "args": {"path": "note.txt"}}])
    assert "[unknown-command]" in log, \
        f"t10① 失效：模型要了个不存在的命令而日志零痕迹（运维数不出这一场烧了几发）：{log[-200:]!r}"
    # ①′ 同一笔要落到账本上：日志行给人 grep，但「这场会话白烧了几发」要能被 GET 读到、
    #      要能跨会话求和，就必须有个唯一出口（T4-③ 的聚合面）。
    cm = role.llm.cost_manager
    assert cm.unknown_command_calls == 1, \
        f"t10①′ 失效：账本没记这一笔，聚合面无从求和：{cm.unknown_command_calls}"
    res = out["history"][-1]["results"]
    assert res and res[0]["name"] == "write_flie" and "未知命令" in res[0]["result"], \
        f"t10① 回喂串变了，模型下一轮看不出自己写错了名字：{res}"

    log2, out2 = act([{"command_name": "RoleZero.reply_to_human", "args": {"content": "写好了"}},
                      {"command_name": "end", "args": {}}])
    assert "[unknown-command]" not in log2, \
        f"t10② 阳性对照不成立：已知命令也打了那行 → {log2[-200:]!r}"
    assert cm.unknown_command_calls == 1, \
        f"t10②′ 阳性对照不成立：已知命令也被计数了（计数会虚高）：{cm.unknown_command_calls}"
    # ③ 快照必须把两笔「无效调用」带出去。
    #    ⚠ 本格证的是「同一个 manager 的快照带出两键、且不长回合计字段」；**没证**的是
    #    "角色手上的 manager == runner 的 self.costs[sid]"（那条靠 `_make_llm` 注入，B8 的截断计数
    #    共用它）。要钉住同一性得跑一场真会话再读 GET 出口——已按未验边界记进 PLAN，
    #    不许把这条注释当成已验（team.py:82 记过"另建实例只记到 0"那条断链的教训）。
    from server.runner import cost_snapshot
    snap = cost_snapshot(cm)
    assert snap["unknown_command_calls"] == 1 and "truncated_calls" in snap, f"t10③ 快照没带出计数：{snap}"
    assert "total_cost" not in snap, f"t10③ 失效：快照又长出合计字段（C12 删掉的就是它）：{snap}"
    assert out2.get("finished") is True, f"t10② 前提失配：end 没收口，那这格什么都没测：{out2}"
    print("  ok  t10 未知命令：留可 grep 的告警 + 落账本计数 + 快照带出（已知命令三处都不动＝阳性对照）")


def t11_ask_human_does_not_replay_side_effects():
    """C59（09-26）：`ask_human` 与副作用命令在同一条命令列表里时，resume **不许**把副作用再跑一遍。

    为什么必须有这条**真图**判据：`interrupt()` 恢复时 LangGraph 从**节点开头**重跑整个节点函数，
    而 `_act` 原先在**函数末尾**才返回状态更新 ⇒ 中断前跑完的那截命令，结果一个字都没落进 state，
    重放时全部再跑一遍。复现读数（本轮）：一条 `[write_file, ask_human]` 的命令列表，
    `ask` 之前 `write_file` 1 次、resume 之后 **2** 次；终端命令 / 追加写 / 外部 API 同理翻倍。

    修法三件：把「问人」挪进零副作用的 `ask` 节点（`act` 靠 `pending_ask` 条件边转过去）、
    加 `act_cursor` 跳过已跑完的那截、`ask` 回边走 `gate`。四格（都走真图真 resume，零花费）：

      ① **主判据**：`[write_file, ask_human]` → resume 之后 `write_file` 恰好 **1** 次（修复前 2 次）；
      ② **阳性对照**：不含 ask 的列表照旧执行一次 —— 证明 ① 不是「命令根本没跑」那种假绿；
      ③ **游标重置**：第一轮只问人、第二轮才写 ⇒ 第二轮的写命令要真执行（`_think` 不重置游标的话，
         第二轮 `_act` 会从上一轮的游标起步、整列命令被跳过）；
      ④ **回边过闸门**（结构格）：编译出的图里 `ask → gate` 必须在、`ask → act` 不许有
         —— ask 回来后直连 `act` 就等于让剩下的命令绕过审批；
      ⑤ **同列表两次 ask**：两次都问到（停两次）、逐次按序、零副作用。
    """
    import json

    count = {"n": 0}

    def wrap(agents):
        """把队长的 `write_file` 包一层计数器。**写文件看不出来跑了几次**（覆盖写），副作用次数只能直接数。"""
        from codeharness.const import TEAMLEADER_NAME as _TL
        inner = agents[_TL].tools["write_file"]

        class _Counting:
            async def ainvoke(self, args, **kw):
                count["n"] += 1
                return await inner.ainvoke(args, **kw)

            def __getattr__(self, k):
                return getattr(inner, k)

        agents[_TL].tools["write_file"] = _Counting()

    def script(*cmds):
        return json.dumps({"thought": "脚本", "commands": list(cmds)}, ensure_ascii=False)

    WRITE = {"command_name": "write_file", "args": {"path": "trace.txt", "content": "ONCE"}}
    ASK = {"command_name": "RoleZero.ask_human", "args": {"question": "要继续吗？"}}
    END = {"command_name": "end", "args": {}}

    def run_case(project, leader_script, answers=("继续",)):
        """起会话 → 等它停车或收口 → 逐次回话直到收口 → 返回 `(停车次数, 回话前次数, 最终次数, 终态)`。

        ⚠ 两条都是踩过的坑：
          ① 别对每个用例都断言「必须停在待人工处」——② 那条剧本里没有 ask，本来就该直接收口；
          ② 回话后的轮询**不能只看状态**：`answer_human` 刚返回时状态还是 `awaiting_human`（resume 任务
             刚 `create_task`），立刻判「停着」就把结果判错了。要**先等它离开** awaiting_human、再等落地
             ——「等的是结果，不是状态翻转」（t6 的注释里就写着这条）。
        """
        count["n"] = 0
        store, runner, s, cleanup = _gate_runner(project, leader_script,
                                                 permission="full_access", on_agents=wrap)
        try:
            status, _kept = _settle_parked(store, runner, s)
            before = count["n"]
            started_parked = status == "awaiting_human"

            async def _wait(pred, limit=30.0):
                end = asyncio.get_running_loop().time() + limit
                while asyncio.get_running_loop().time() < end:
                    if pred(store.get(s.id).status.value):
                        return True
                    await asyncio.sleep(0.1)
                return False

            repark = 0

            async def go():
                nonlocal repark
                for ans in answers:
                    assert runner.answer_human(s.id, ans), f"{project}：resume 没起来"
                    await _wait(lambda st: st != "awaiting_human")        # 先等它离开断点
                    await _wait(lambda st: st in ("finished", "failed", "stopped", "awaiting_human"))
                    if store.get(s.id).status.value == "awaiting_human":  # 又停了一次
                        repark += 1
                return store.get(s.id).status.value

            final = asyncio.run(go()) if started_parked else store.get(s.id).status.value
            parks = (1 if started_parked else 0) + repark
            return parks, before, count["n"], final
        finally:
            for t in list(runner.tasks.values()):
                t.cancel()
            cleanup()

    p1, before1, after1, final1 = run_case("s17_c59_once", [script(WRITE, ASK), script(END)])
    assert p1 == 1, f"t11① 该停一次，实际 {p1}"
    assert before1 == 1, f"t11① 停车前 write_file 就该只有 1 次，实际 {before1}"
    assert after1 == 1, f"t11① resume 之后 write_file 跑了 {after1} 次（C59 复发：中断前那半条被重放）"
    assert final1 == "finished", f"t11① 终态 {final1!r}（该正常收口）"

    p2, _b2, after2, final2 = run_case("s17_c59_noask", [script(WRITE), script(END)])
    assert p2 == 0 and after2 == 1 and final2 == "finished", \
        f"t11② 阳性对照：不含 ask 的列表该**不停**、执行一次并收口，实际 停={p2} count={after2} final={final2!r}"

    p3, before3, after3, final3 = run_case("s17_c59_cursor", [script(ASK), script(WRITE), script(END)])
    assert p3 == 1, f"t11③ 该停一次，实际 {p3}"
    assert before3 == 0, f"t11③ 第一轮只问人不该有副作用，实际 {before3}"
    assert after3 == 1, f"t11③ 第二轮的新命令列表没被执行（游标没重置）：{after3}"
    assert final3 == "finished", f"t11③ 终态 {final3!r}"

    # ⑤ 同一列表里**两次 ask**：两次都要问到（停两次）、逐条按序，且一次副作用都不许有。
    p5, before5, after5, final5 = run_case("s17_c59_twoasks",
                                           [script(ASK, ASK, END)], answers=("第一次", "第二次"))
    assert p5 == 2, f"t11⑤ 同列表两次 ask 只停了 {p5} 次（第二次被吞了）"
    assert before5 == 0 and after5 == 0, f"t11⑤ ask 不该产生副作用：before={before5} after={after5}"
    assert final5 == "finished", f"t11⑤ 终态 {final5!r}"

    from langgraph.checkpoint.memory import InMemorySaver
    from codeharness.provider.fake import FakeLLM
    from codeharness.roles.role_zero import RoleZero
    g = RoleZero({"name": "probe", "profile": "p", "goal": "g"}, [], FakeLLM(["{}"])).build(
        checkpointer=InMemorySaver())
    # 静态边读编译图留着的那个 builder（`get_graph().edges` 只给 `__start__/__end__` 两条，
    # 条件边与后加的静态边都不在里面 —— 本轮第一版就是被这个绕过、红在格自己身上）。
    # 「`act` 在 `pending_ask` 时能走到 `ask`」那半由 ①②③ 的行为读数证（它们真停在了 ask 上）。
    edges = set(g.builder.edges)
    assert ("ask", "gate") in edges, f"t11④ `ask` 回来没走审批闸门（剩下的命令会绕过审批）：{sorted(edges)}"
    assert ("ask", "act") not in edges, f"t11④ `ask` 直连了 `act`：{sorted(edges)}"
    assert {"think", "gate", "act", "ask"} <= set(g.builder.nodes), "四个节点没齐"
    print(f"  ok  t11 C59：含 ask 的命令列表 resume 后副作用仍是 1 次（修复前 2）、不含 ask 的照旧 1 次、"
          f"第二轮新列表真执行、同列表两次 ask 停两次、静态边 {sorted(e for e in edges if e[0] == 'ask')}"
          f"（`ask` 回边走 gate）")


def t12_approval_and_ask_alternate():
    """C59 未验边界两格（09-26 验证批，主仓零生产改动）：审批与 ask 的**交互组合**。

    t11 覆盖纯 ask 的形状（t5/t6/t7 覆盖纯审批）；没量过的是两族组合：
      格①（进程内，真图真台账，readonly）两个子形状——**停车序由 gate 的前置性决定**（gate 会把
        一列命令里所有待批项**全部前置**问完，act 才开跑；实测读数，见下）：
        ①a 三轮剧本 `[WRITE_A] / [ASK] / [WRITE_B, END]` ⇒ **批A → 问 → 批B**（题面原样的交替）：
           批 A 之前零副作用、A 恰好 1 次；停在 ask 上时台账 settled=1/pending=0（纯问）；
           ask 回来后 B 照样挂卡（不绕过审批闸门）；B 恰好 1 次；终态 finished。
        ①b 单列表 `[WRITE_A, ASK, WRITE_B, END]` ⇒ **批A → 批B → 问**（同列表混合形状）：
           ask 回来后 gate 按 `commands[cursor:]` 复查，B 已在台账 ⇒ **不许冒第三张卡**（C59 第 5 处）；
           settled=2/pending=0、两次写各恰好一次、两个文件内容逐字对。
      格②（跨进程，t8 同款两进程姿势，full_access）：`[ask_human, write_file, end]`
        ⇒ 停在 ask 上进程退出（=服务死掉）；新进程 `create_app()` 走完启动自愈后：
        状态仍是 awaiting_human（自愈只抹 running）、**无审批卡**、POST human-input 后
        游标从断点续上——`act_cursor`/`pending_ask` 真的进了 checkpointer（t8 量的是停在审批卡，
        那条路的恢复只依赖台账，游标断点没被问过）。
    """
    import json
    from platforms.approval_store import ApprovalStore

    count = {"n": 0}

    def wrap(agents):
        """write_file 包计数器——覆盖写看不出跑了几次，副作用次数只能直接数（t11 同一课）。"""
        from codeharness.const import TEAMLEADER_NAME as _TL
        inner = agents[_TL].tools["write_file"]

        class _Counting:
            async def ainvoke(self, args, **kw):
                count["n"] += 1
                return await inner.ainvoke(args, **kw)

            def __getattr__(self, k):
                return getattr(inner, k)

        agents[_TL].tools["write_file"] = _Counting()

    def script(*cmds):
        return json.dumps({"thought": "脚本", "commands": list(cmds)}, ensure_ascii=False)

    WRITE_A = {"command_name": "write_file", "args": {"path": "alt_a.txt", "content": "ONE"}}
    ASK = {"command_name": "RoleZero.ask_human", "args": {"question": "中间确认一次，继续吗？"}}
    WRITE_B = {"command_name": "write_file", "args": {"path": "alt_b.txt", "content": "TWO"}}
    END = {"command_name": "end", "args": {}}

    def cards_of(st):
        return [p["tool"] for p in st.pending()], [p["tool"] for p in st.settled()]

    def _approve(st):
        """t6 口径：先在台账定案（allowed-once），再把卡 id 当回话内容交出去——
        gate 的 interrupt() 返回值只当「有人回过话」的信号，结论一律回台账读。"""
        aid = st.pending()[0]["id"]
        assert st.decide(aid, "allowed-once") == "allowed-once", "①a 台账写不进结论"
        return aid

    # ---- 格①a 三轮剧本：批A → 问 → 批B（题面原样的「先批、再问、再批」） ----
    count["n"] = 0
    seen = []

    def chk_a(i, status, n, st):
        pend, settled = cards_of(st)
        seen.append((i, status, n, len(pend), len(settled)))
        if i == 0:      # 第一停 = A 的审批卡：批之前零副作用
            assert status == "awaiting_human" and n == 0, f"①a 第{i}步失配：{seen[-1]}"
            assert pend == ["write_file"] and not settled, f"①a 第一张卡形状不对：{seen[-1]}"
        elif i == 1:    # 批 A → WRITE_A 执行 → 队长下一轮 [ASK] → 停在问（纯问：零卡）
            assert status == "awaiting_human" and n == 1, \
                f"①a 批 A 后 WRITE_A 该恰好 1 次并停在 ask 上：{seen[-1]}"
            assert pend == [] and len(settled) == 1, f"①a 停在 ask 上台账该 settled=1/pending=0：{seen[-1]}"
        elif i == 2:    # 答 ask → 游标续上 → 新一轮 [WRITE_B, END] → B 挂卡（不绕闸门）
            assert status == "awaiting_human" and n == 1, \
                f"①a ask 不该产生副作用、B 要先过审批：{seen[-1]}"
            assert pend == ["write_file"], f"①a ask 回来后 B 没挂卡（绕过审批闸门）：{seen[-1]}"
        else:           # 批 B → WRITE_B 执行 → 收口
            assert status == "finished" and n == 2, \
                f"①a 终态该 finished 且两次写各恰好一次：{seen[-1]}"
            assert pend == [] and len(settled) == 2, f"①a 台账该 settled=2/pending=0：{seen[-1]}"

    count["n"] = 0
    seen.clear()
    store_a, runner_a, sa_, cleanup_a = _gate_runner("s17_c59_alt3",
                                                     [script(WRITE_A), script(ASK), script(WRITE_B, END)],
                                                     permission="readonly", on_agents=wrap)
    st_a = None
    try:
        status, _k = _settle_parked(store_a, runner_a, sa_)
        st_a = ApprovalStore(sa_.id)
        chk_a(0, status, count["n"], st_a)

        async def step(sid_, ans):
            assert runner_a.answer_human(sid_, ans), "①a resume 没起来"

            async def _wait(pred, limit=30.0):
                end = asyncio.get_running_loop().time() + limit
                while asyncio.get_running_loop().time() < end:
                    if pred(store_a.get(sid_).status.value):
                        return True
                    await asyncio.sleep(0.1)
                return False

            await _wait(lambda v: v != "awaiting_human")
            await _wait(lambda v: v in ("finished", "failed", "stopped", "awaiting_human"))
            return store_a.get(sid_).status.value

        chk_a(1, asyncio.run(step(sa_.id, _approve(st_a))), count["n"], st_a)
        chk_a(2, asyncio.run(step(sa_.id, "继续")), count["n"], st_a)
        chk_a(3, asyncio.run(step(sa_.id, _approve(st_a))), count["n"], st_a)
        ws = Path("workspace") / "s17_c59_alt3"
        assert (ws / "alt_a.txt").read_text(encoding="utf-8").strip() == "ONE", "①a A 的内容不对"
        assert (ws / "alt_b.txt").read_text(encoding="utf-8").strip() == "TWO", "①a B 的内容不对"
        print(f"  ok  t12①a 批A→问→批B（三轮交替）：停车序 {[f'{x[0]}:{x[1]}' for x in seen]}、"
              f"A/B 各恰好 1 次、ask 停车时台账零卡、B 照样过闸门")
    finally:
        for t in list(runner_a.tasks.values()):
            t.cancel()
        if st_a is not None:
            st_a.r.delete(st_a.key, st_a.dkey)
        cleanup_a()

    # ---- 格①b 单列表 [A, ASK, B, end]：批A → 批B → 问（gate 把一列里的待批项全部前置），
    #      关键断言：ask 回来后 gate 按 commands[cursor:] 复查、B 已在台账 ⇒ 不许冒第三张卡（C59 第 5 处）。
    count["n"] = 0
    seen.clear()
    store_b, runner_b, sb, cleanup_b = _gate_runner("s17_c59_alt1",
                                                    [script(WRITE_A, ASK, WRITE_B, END)],
                                                    permission="readonly", on_agents=wrap)
    st_b = None
    try:
        status, _k = _settle_parked(store_b, runner_b, sb)
        st_b = ApprovalStore(sb.id)

        async def step_b(ans):
            assert runner_b.answer_human(sb.id, ans), "①b resume 没起来"

            async def _wait(pred, limit=30.0):
                end = asyncio.get_running_loop().time() + limit
                while asyncio.get_running_loop().time() < end:
                    if pred(store_b.get(sb.id).status.value):
                        return True
                    await asyncio.sleep(0.1)
                return False

            await _wait(lambda v: v != "awaiting_human")
            await _wait(lambda v: v in ("finished", "failed", "stopped", "awaiting_human"))
            return store_b.get(sb.id).status.value

        pend0, settled0 = cards_of(st_b)
        assert status == "awaiting_human" and count["n"] == 0, f"①b 第一停失配：{status!r} n={count['n']}"
        assert pend0 == ["write_file"] and not settled0, f"①b 第一张卡形状不对：{pend0}/{settled0}"

        s1 = asyncio.run(step_b(_approve(st_b)))                # 批 A → gate 复查 → B 的卡前置
        pend1, settled1 = cards_of(st_b)
        assert s1 == "awaiting_human" and count["n"] == 0, \
            f"①b 批 A 后 act 还不该开跑（gate 把 B 的审批前置了）：{s1!r} n={count['n']}"
        assert pend1 == ["write_file"] and len(settled1) == 1, f"①b 第二张卡形状不对：{pend1}/{settled1}"

        s2 = asyncio.run(step_b(_approve(st_b)))                # 批 B → act：A 执行、撞 ask → 停在问
        pend2, settled2 = cards_of(st_b)
        assert s2 == "awaiting_human" and count["n"] == 1, \
            f"①b 批 B 后 A 该恰好跑 1 次并停在 ask 上：{s2!r} n={count['n']}"
        assert pend2 == [] and len(settled2) == 2, f"①b 停在 ask 上台账该 settled=2/pending=0：{pend2}/{settled2}"

        s3 = asyncio.run(step_b("继续"))                         # 答 ask → B 执行 → 收口
        pend3, settled3 = cards_of(st_b)
        assert s3 == "finished" and count["n"] == 2, \
            f"①b 终态该 finished 且两次写各恰好一次：{s3!r} n={count['n']}"
        assert pend3 == [] and len(settled3) == 2, \
            f"①b 失效：ask 回来后冒了第三张卡（B 被重问，C59 第 5 处复发）：{pend3}/{settled3}"
        ws = Path("workspace") / "s17_c59_alt1"
        assert (ws / "alt_a.txt").read_text(encoding="utf-8").strip() == "ONE", "①b A 的内容不对"
        assert (ws / "alt_b.txt").read_text(encoding="utf-8").strip() == "TWO", "①b B 的内容不对"
        print("  ok  t12①b 批A→批B→问（单列表混合）：gate 前置两张卡、ask 停车时台账已定 2 张、"
              "resume 后不冒第三张卡、A/B 各恰好 1 次")
    finally:
        for t in list(runner_b.tasks.values()):
            t.cancel()
        if st_b is not None:
            st_b.r.delete(st_b.key, st_b.dkey)
        cleanup_b()

    # ---- 格② 重启停在 ask 上（跨进程，t8 同款姿势） ----
    import os
    import shutil
    import subprocess
    import sys
    root = Path(tempfile.mkdtemp()) / "ws"
    project2 = f"s17_c59ask_{os.urandom(3).hex()}"
    me = str(Path(__file__).resolve())
    env = dict(os.environ, PLATFORM__USE_REDIS="1", REDIS__DB="15", PYTHONUNBUFFERED="1")

    def run(*args):
        p = subprocess.run([sys.executable, "-B", me, "--worker", *args], capture_output=True,
                           text=True, env=env, timeout=300, cwd=str(Path(me).parent.parent))
        line = next((l for l in p.stdout.splitlines() if l.startswith("RESULT ")), "")
        assert line, (f"t12② 子进程没出读数：args={args} rc={p.returncode}\n"
                      f"stdout 末段={p.stdout[-600:]!r}\nstderr 末段={p.stderr[-600:]!r}")
        return json.loads(line[len("RESULT "):])

    sid = ""
    try:
        parked = run("park", str(root), project2, "", "ask")
        sid = parked["sid"]
        assert parked["parked"] == "awaiting_human", f"t12② 前置失配：没停在 ask 上：{parked}"
        assert parked.get("file_before") is False, "t12② 停在 ask 上就该零副作用，文件却已存在"
        after = run("resume", str(root), project2, sid, "ask")
        assert after["after_boot"] == "awaiting_human", \
            f"t12② 失效：重启后 GET 是 {after['after_boot']!r}——启动自愈抹掉了停在 ask 上的驻留态"
        assert after["pending"] == 0, f"t12② 停在 ask 上不该有审批卡：{after['pending']}"
        assert after["respond"] == 200, f"t12② human-input 没被接住：{after}"
        assert after.get("file") == "RESUMED-OK", \
            f"t12② 失效：重启后 resume 没从游标续上——文件没落盘或内容不对：{after}"
        assert after["final"] == "finished", f"t12② 收口 {after['final']!r}"
        print("  ok  t12② 重启停在 ask：自愈不抹、无审批卡、human-input 后游标从断点续上、真落盘、正常收口")
    finally:
        if sid:
            _purge(sid)
        shutil.rmtree(root.parent, ignore_errors=True)
        shutil.rmtree(Path("workspace") / project2, ignore_errors=True)


def t13_as_node_replay_does_not_reseed():
    """C72（09-28 全量审查批）：`as_node._run` 的**前置副作用**在 resume 时不许重放。

    为什么 t11 没盖住这条：C59 治的是**内层** `_act` 的重放（中断前那半条命令），而 `interrupt()`
    同样会从**节点开头**重跑**外层**节点函数——`RoleZero.as_node` 的 `_run` 里、`graph.ainvoke(...)`
    之前有三处副作用：任务文本进记忆、回报文本进记忆、`plan_fn(task)`（`roles/registry.py:262`
    把 ToT 树搜索接在这，**真模型=付费**）。每出现一次中断（审批卡或 ask_human）外层节点就重跑
    一次 ⇒ 规划器按中断次数重复烧钱、任务与 `[Planned path]` 在 prompt 里出现多份。

    三格（真图真 resume，零花费；`plan_fn` 用计数桩——生产里它是 `make_tot_planner(llm)`）：
      ① **主判据**：`[ask, ask, end]`（停两次 ⇒ 外层节点跑 3 次）后 `plan_fn` 仍只调 **1** 次、
         任务文本在队长记忆里只有 **1** 条、`[Planned path]` 只有 **1** 条（修复前各 3）；
      ② 停一次的形状 `[ask, end]`：同一个口径各 1；
      ③ 阳性对照 `[end]`（不停）：各恰好 1 ——证明 ① 不是「压根没播种」那种假绿。
    """
    import json
    from codeharness.const import TEAMLEADER_NAME as _TL

    count = {"plan": 0}
    seen = {}

    def wrap(agents):
        seen["agents"] = agents

        async def plan_fn(task):
            count["plan"] += 1
            return f"[树搜索产物 #{count['plan']}]"

        agents[_TL].plan_fn = plan_fn

    def script(*cmds):
        return json.dumps({"thought": "脚本", "commands": list(cmds)}, ensure_ascii=False)

    ASK = {"command_name": "RoleZero.ask_human", "args": {"question": "要继续吗？"}}
    END = {"command_name": "end", "args": {}}
    IDEA = "写一个文件"                     # `_gate_runner` 建会话用的 idea，就是队长拿到的那条任务文本

    def run_case(project, leader_script, answers=("继续",)):
        count["plan"] = 0
        seen.clear()
        store, runner, s, cleanup = _gate_runner(project, leader_script,
                                                 permission="full_access", on_agents=wrap)
        try:
            status, _kept = _settle_parked(store, runner, s)
            started_parked = status == "awaiting_human"

            async def _wait(pred, limit=30.0):
                end = asyncio.get_running_loop().time() + limit
                while asyncio.get_running_loop().time() < end:
                    if pred(store.get(s.id).status.value):
                        return True
                    await asyncio.sleep(0.1)
                return False

            repark = 0

            async def go():
                nonlocal repark
                for ans in answers:
                    assert runner.answer_human(s.id, ans), f"{project}：resume 没起来"
                    await _wait(lambda st: st != "awaiting_human")     # 先等它离开断点（t11 同款注释）
                    await _wait(lambda st: st in ("finished", "failed", "stopped", "awaiting_human"))
                    if store.get(s.id).status.value == "awaiting_human":
                        repark += 1
                return store.get(s.id).status.value

            final = asyncio.run(go()) if started_parked else store.get(s.id).status.value
            parks = (1 if started_parked else 0) + repark
            mem = [m.content for m in seen["agents"][_TL].memory.storage]
            return (parks, final, count["plan"], mem.count(IDEA),
                    sum(1 for c in mem if c.startswith("[Planned path]")))
        finally:
            for t in list(runner.tasks.values()):
                t.cancel()
            cleanup()

    p1, final1, plan1, task1, planned1 = run_case("s17_c72_twice", [script(ASK, ASK, END)],
                                                 answers=("第一次", "第二次"))
    assert p1 == 2, f"t13① 该停两次，实际 {p1}"
    assert plan1 == 1, f"t13① plan_fn 被调了 {plan1} 次（C72 复发：外层节点重放把付费规划重跑了）"
    assert task1 == 1, f"t13① 任务文本在记忆里有 {task1} 条（C72 复发：重放把任务装了多遍）"
    assert planned1 == 1, f"t13① [Planned path] 有 {planned1} 条（该恰好 1 条）"
    assert final1 == "finished", f"t13① 终态 {final1!r}"

    p2, final2, plan2, task2, planned2 = run_case("s17_c72_once", [script(ASK, END)])
    assert (p2, plan2, task2, planned2, final2) == (1, 1, 1, 1, "finished"), \
        f"t13② 停一次的形状读数 {p2},{plan2},{task2},{planned2},{final2!r}"

    p3, final3, plan3, task3, planned3 = run_case("s17_c72_noask", [script(END)])
    assert (p3, plan3, task3, planned3, final3) == (0, 1, 1, 1, "finished"), \
        f"t13③ 阳性对照：不停时 plan/任务/[Planned path] 该各 1，实际 {p3},{plan3},{task3},{planned3},{final3!r}"
    print(f"  ok  t13 C72：停两次后 plan_fn 仍 1 次、任务与 [Planned path] 各 1 条"
          f"（修复前各 3）；停一次/不停两种形状各 1（阳性对照）")


def t14_spawn_drops_quietly_when_the_row_vanished():
    """`_spawn` 咽喉：会话行在**起跑与落态之间**没了 ⇒ 不抛穿、不留死槽、可 grep、散会走到底。

    触发前提先判过（不给不存在的形状发明修法）：唯一的删行出口 `DELETE /{sid}` 只挡
    running/stopping/awaiting_human（`server/api/sessions.py:235`），所以「一场**没在跑**的会话被回了
    审批卡、随后删掉」这条路是通的；而 `_run` 那侧的窗口更宽——`start` 之后到落 running 之间隔着
    `_prepare`（C43 实测重建图 p50≈1633ms，这期间状态还停在 start 前那一档）。
    09-29 的 s8 门禁就在 t8 上把它现证过一次：28/28 全绿**之后**吊出
    `Task exception was never retrieved: KeyError`，栈停在 `_resume` 落 running 那一句（1/4 时序命中）。
    改前三处坏：① 异常没人 retrieve＝日志里零痕迹；② `_resume` 的 `tasks.pop` 写在 try 的 finally 里、
    而抛穿发生在 try **之前** ⇒ 死任务永久占住 `tasks[sid]`（:559 那两条注释讲的正是抢槽的代价）；
    ③ 散会没走 ⇒ 图/账/常驻 shell 全留在进程里。"""
    import io
    from types import SimpleNamespace as NS
    from loguru import logger

    class _Vanish:
        """替身图：`aget_state` 被到的那一刻把会话行删掉（真图这一步本来就排在 await 之后）。"""

        def __init__(self, store, sid):
            self.store, self.sid, self.gone = store, sid, False

        async def aget_state(self, _config):
            if not self.gone:
                self.store.delete(self.sid)
                self.gone = True
            return NS(next=("gate",), tasks=[NS(interrupts=[NS(id="i1", value={"q": 1})])])

        async def astream_events(self, *_a, **_k):
            raise AssertionError("会话行已没了还去跑图——收口点应落在落 running 之前")
            yield

    def _drive(resume_fn):
        buf = io.StringIO()
        hid = logger.add(buf, format="{message}", level="WARNING")     # 与 s3b/s4/s17 同款抓法
        try:
            err = resume_fn(buf)
        finally:
            logger.remove(hid)
        return err, buf.getvalue()

    # ① `_resume` 那条路：起跑后落 running 撞空会话
    store, runner, s = _make_runner()
    runner.graphs[s.id] = (_Vanish(store, s.id), {"configurable": {"thread_id": s.project_name}})
    store.update(s.id, status=SessionStatus.awaiting_human)

    async def resume_case():
        assert runner.answer_human(s.id, "批准") is True, "前提：这条回答该被接走（会话还在、没在跑）"
        t = runner.tasks[s.id]
        try:
            await t
            return None
        except KeyError as e:
            return e

    err, log = _drive(lambda _b: asyncio.run(resume_case()))
    assert err is None, f"① 落态撞空会话时 KeyError 抛穿了（没人 retrieve＝静默死）：{err!r}"
    assert "[runner-dropped]" in log, f"② 这条作废没有可 grep 的痕迹：{log[:200]!r}"
    assert s.id not in runner.tasks, "② 死任务还占着 tasks 槽（改前形状：pop 在 try 里、抛穿在 try 之前）"
    assert runner.graphs.get(s.id) is None and runner.chats.get(s.id) is None, \
        "③ 散会没走：图或插话队列还留在进程里（`_forget` 才是这条路的收尾）"
    # ⑥ C117：这场没「跑失败」，是行没了 ⇒ 不许再打运维按数的那一行。
    #    现证（改前，`E:/tmp/ch_c116_s17.out:447`）：`[session-failed] sid=… KeyError: '<sid>'` 在前、
    #    `[runner-dropped] sid=…` 紧跟，同一个 sid 两条互相矛盾的告警。
    assert "[session-failed]" not in log, \
        f"⑥ C117 假告警：撞空会话被记成『跑失败了』（运维按 [session-failed] 计数会凭空多一场）：{log[:280]!r}"

    # ④ 同族另一条路：`start` 与落 running 之间隔着 `_prepare`（真窗口），撞空时也要安静
    store2, runner2, s2 = _make_runner()

    async def prepare_then_delete(session, proj, cost_manager, persist_roles=True):
        store2.delete(session.id)           # ← C43 量过的那 1.6s 里，另一条 API 把行删了
        return _Team(events=[]), {"configurable": {"thread_id": proj}}, {"messages": []}

    runner2._prepare = prepare_then_delete

    async def run_case():
        runner2.start(s2)
        t = runner2.tasks[s2.id]
        try:
            await t
            return None
        except KeyError as e:
            return e

    err2, log2 = _drive(lambda _b: asyncio.run(run_case()))
    assert err2 is None, f"④ `_run` 同族那一路还在抛穿：{err2!r}"
    assert "[runner-dropped]" in log2, "④ `_run` 撞空会话时没走同一个咽喉（两处各写一遍必漂）"
    assert s2.id not in runner2.tasks, "④ `_run` 这条路的槽没摘干净"
    assert "[session-failed]" not in log2, \
        f"⑥b C117 同族那一路（`_run`）也在打假告警：{log2[:280]!r}"

    # ⑤ 阳性对照：会话行**还在**时，这条守卫不许咽掉正常路径（落 running→跑完→按断点驻留）
    store3, runner3, s3 = _make_runner()
    team3 = _Team(events=[], interrupts=PARKED)
    runner3.graphs[s3.id] = (team3, {"configurable": {"thread_id": s3.project_name}})
    store3.update(s3.id, status=SessionStatus.awaiting_human)

    async def alive_case():
        assert runner3.answer_human(s3.id, "选方案二") is True
        await runner3.tasks[s3.id]
        return store3.get(s3.id).status.value

    status_after, log3 = _drive(lambda _b: asyncio.run(alive_case()))
    assert "[runner-dropped]" not in log3, "⑤ 会话健在也走了 dropped 分支——守卫写宽了，真故障会被一起咽掉"
    assert status_after == SessionStatus.awaiting_human.value, \
        f"⑤ 正常恢复路被这条守卫改坏了（应照旧停在待人工）：status={status_after}"
    assert s3.id not in runner3.tasks, "⑤ 正常收口后槽没摘"
    print("  ok  t14 起跑咽喉：会话行在起跑与落态之间没了 ⇒ `_resume`/`_run` 两路都不抛穿、"
          "槽按任务对象摘、`[runner-dropped]` 可 grep、散会走到底；会话健在时正常恢复路一字未变；"
          "⑥ C117 撞空那两路**不打** `[session-failed]`（阳性对照＝t9① 真失败仍要打，两处共用一个咽喉）")


def t15_approval_receipt_records_actor():
    """C119：回执记审批人与时刻（谁批的、何时批的）。四格：
    ① `decide(aid, outcome, actor)` 落 JSON 回执，`settled()` 能读出 decided_by/decided_at；
    ② 首到生效保留——第二个审批人同 id 再 decide，结论与审批人都还是第一位的；
    ③ 旧格式（升级窗口里 30 天 TTL 内还活着的裸字符串回执）兼容读，不炸 `decision`/`settled`；
    ④ 进程内档与 Redis 档同形（同一份 `_parse_receipt`），同一组断言双跑。"""
    import uuid
    from platforms.approval_store import ApprovalStore, InProcessApprovalStore

    aid_a, aid_b = "a" * 16, "b" * 16
    st = ApprovalStore(f"s17_t15_{uuid.uuid4().hex[:8]}")
    try:
        st.request({"id": aid_a, "tool": "write_file", "args_preview": "t15", "ts": 1.0})
        first = st.decide(aid_a, "allowed-once", actor="alice")
        assert first == "allowed-once", f"t15① 首批写不进：{first!r}"
        again = st.decide(aid_a, "rejected", actor="bob")
        assert again == "allowed-once", f"t15② 首到生效被破坏：bob 的回执盖掉了 alice 的（{again!r}）"
        assert st.decision(aid_a) == "allowed-once", "t15② decision 读回的不是生效结论"
        row = next(r for r in st.settled() if r["id"] == aid_a)
        assert row["decided_by"] == "alice" and row["decided_at"] > 0, f"t15① 回执没记审批人：{row!r}"
        assert row["outcome"] == "allowed-once", f"t15① outcome 字段漂了：{row!r}"

        st.request({"id": aid_b, "tool": "write_file", "args_preview": "t15-legacy", "ts": 2.0})
        st.r.hset(st.dkey, aid_b, "rejected")            # ③ 旧格式直塞（升级前形状）
        assert st.decision(aid_b) == "rejected", "t15③ 旧格式裸字符串读不出结论"
        legacy = next(r for r in st.settled() if r["id"] == aid_b)
        assert legacy["outcome"] == "rejected" and legacy["decided_by"] == "" \
            and legacy["decided_at"] == 0.0, f"t15③ 旧格式兼容读走样：{legacy!r}"
    finally:
        st.r.delete(st.key, st.dkey)                     # 直造的键自己收，别把 db15 留成垃圾场

    mem = InProcessApprovalStore("s17_t15_mem")          # ④ 同形同断言
    mem.request({"id": aid_a, "tool": "terminal_command", "args_preview": "t15", "ts": 1.0})
    assert mem.decide(aid_a, "rejected", actor="carol") == "rejected"
    assert mem.decide(aid_a, "allowed-once", actor="dave") == "rejected", "t15④ 进程内档首到生效被破坏"
    assert next(r for r in mem.settled() if r["id"] == aid_a)["decided_by"] == "carol"
    mem.request({"id": aid_b, "tool": "write_file", "args_preview": "t15-legacy", "ts": 2.0})
    mem._decisions[aid_b] = "allowed-once"               # 旧格式直塞
    assert mem.decision(aid_b) == "allowed-once"
    legacy_mem = next(r for r in mem.settled() if r["id"] == aid_b)
    assert legacy_mem["decided_by"] == "" and legacy_mem["decided_at"] == 0.0, \
        f"t15④ 进程内档旧格式兼容读走样：{legacy_mem!r}"
    print("  ok  t15 回执记审批人（who/when）+ 首到生效保留 + 旧格式兼容读 + 两档台账同形（C119）")


def t16_invalid_args_is_countable():
    """C122：模型给的 args 没过工具自己的 args_schema ⇒ ValidationError 照旧回喂，且落账本计数。

    与 t10 同族同形状（直接打 `_act`，不走真图）。`write_file` 只给 path 不给 content——
    LangChain 在 ainvoke **前**按 args_schema 校验就炸，副作用一次不发生。
    三格：① 真违例 → `invalid_args_calls` +1、`[错误]` 串照旧回喂（self-heal 链不断）；
          ② 阳性对照：schema 合法的调用（read_file 对不存在的路径返回人话串——工具内逻辑错，
            不是校验错）不计数；
          ③ 快照把第四个无效调用计数带出去。
    这一格是「args spec 进 prompt / 原生 function-calling」立项与否的读数出口
    （重开条件写死在 PLAN §4 C122 行：活体持续为 0 ⇒ 两件不立项）。
    """
    from codeharness.provider.fake import FakeLLM
    from codeharness.roles.role_zero import RoleZero
    from codeharness.tools import read_file, write_file

    role = RoleZero({"name": "Alice", "profile": "PM", "goal": "g"},
                    [write_file, read_file], FakeLLM([""]))   # _act 不调模型，命令由 history 末条给进来

    def act(commands):
        return asyncio.run(role._act({"task": "写文件", "history":
                                      [{"thought": "t", "commands": commands}],
                                      "respond_language": "中文", "finished": False}))

    out = act([{"command_name": "write_file", "args": {"path": "t16_probe.txt"}}])
    cm = role.llm.cost_manager
    assert cm.invalid_args_calls == 1, \
        f"t16① 账本没记 schema 违例：{cm.invalid_args_calls}"
    res = out["history"][-1]["results"][0]
    assert res["name"] == "write_file" and "ValidationError" in res["result"], \
        f"t16① 回喂串变了（模型下一轮要认得出错在 args）：{res}"

    out2 = act([{"command_name": "read_file", "args": {"path": "t16_no_such_file.txt"}}])
    res2 = out2["history"][-1]["results"][0]
    assert "文件不存在" in res2["result"], f"t16② 前提失配：read_file 的行为漂了：{res2}"
    assert cm.invalid_args_calls == 1, \
        f"t16② 阳性对照失守：非 schema 的工具内错误也被计数（读数会虚高）：{cm.invalid_args_calls}"

    from server.runner import cost_snapshot
    snap = cost_snapshot(cm)
    assert snap["invalid_args_calls"] == 1, f"t16③ 快照没带出第四个无效调用计数：{snap}"
    print("  ok  t16 无效 args：schema 违例落账本 + 回喂串逐字不变 + 工具内错误不虚计 + 快照带出（C122）")


def t17_throat_tells_crash_from_dropped():
    """C125/C131（10-02 复审批）：

    C125——`_resume` 的 `_ensure_graph`（含 `_prepare` 装配：未知 SOP 模板、角色缺 name）在它自己
    的 try **之外**，那里的 KeyError 到达 `_spawn` 咽喉时会话行**还在**：改前 `except KeyError`
    一刀切成「行已不在」——打 `[runner-dropped]` 假告警、不发 error 事件、状态钉死
    awaiting_human，再答一次同样炸。修后：先核 `store.get(sid) is None`，行还在就交回 `_fail`
    （C116 的 kind 分类、C117 的行没了守卫照常生效）。
    C131——`answer_human` 的 `is_running` 只看本进程：多 worker 下场跑在别的 worker、回答落到
    本 worker 时 tasks 空 ⇒ 改前放行，对同一 thread 发起第二个 resume（双跑同一超步＝双倍发费）。
    修后 status==running 且本 worker 无任务 ⇒ 拒收（照 stop() 转发分支同一条判定）。
    """
    import io
    from loguru import logger

    def _drive(fn):
        buf = io.StringIO()
        hid = logger.add(buf, format="{message}", level="WARNING")
        try:
            out = fn()
        finally:
            logger.remove(hid)
        return out, buf.getvalue()

    # ① C125：装配期 KeyError + 行还在 ⇒ 按「真失败」收（[session-failed] kind=crash + 状态 failed）
    store, runner, s = _make_runner()
    store.update(s.id, status=SessionStatus.awaiting_human)

    async def ensure_graph_blowup(sid, register=True):
        raise KeyError("未知 SOP 模板 no-such-template")

    runner._ensure_graph = ensure_graph_blowup

    async def crash_case():
        assert runner.answer_human(s.id, "批准") is True, "① 前提：回答该被接走（行在、没在跑）"
        await runner.tasks[s.id]
        return store.get(s.id).status.value

    status, log = _drive(lambda: asyncio.run(crash_case()))
    assert status == SessionStatus.failed.value, \
        f"① C125：装配崩溃被咽成散会，状态没进 failed：{status}"
    assert "[session-failed]" in log and "kind=crash" in log, \
        f"① 真失败没打告警（C116 分类在咽喉这一路丢了）：{log[:240]!r}"
    assert "[runner-dropped]" not in log, \
        f"① 行还在却打了「行已不在」（C125 的假话）：{log[:240]!r}"

    # ② C131：status=running 且本 worker 无任务 ⇒ 拒收、不起任务、不改状态
    store2, runner2, s2 = _make_runner()
    store2.update(s2.id, status=SessionStatus.running)
    assert runner2.answer_human(s2.id, "批准") is False, \
        "② C131：多 worker 撞车口没拦——回答落错 worker 会对同一 thread 并发 resume（双跑双费）"
    assert s2.id not in runner2.tasks, "② 拒收却起了任务"
    assert store2.get(s2.id).status == SessionStatus.running, "② 拒收不许顺手改状态"
    # ③ 阳性对照不在此处重复：awaiting_human 且无任务 ⇒ 照旧接走的形状，t14⑤ 已用真图钉过
    #    （本组只钉「拒收分支的边界」，接走路的回归由 t14⑤ 兜着）。
    print("  ok  t17 咽喉分得清两种 KeyError：装配崩溃→[session-failed] kind=crash（行还在不许打"
          "[runner-dropped]）；status=running 且本 worker 无任务→拒收（C131 多 worker 撞车口）")


def main():
    checks = [t1_interrupt_clears_task_slot, t2_no_slot_steal, t3_endpoint_returns_409,
              t4_breakpoint_settles_and_stops, t5_real_gate_interrupt, t6_real_approve_and_reject,
              t7_special_commands_never_park, t8_restart_keeps_parked_session,
              t9_failure_is_loud,
              t10_unknown_command_is_countable,
              t11_ask_human_does_not_replay_side_effects,
              t12_approval_and_ask_alternate,
              t13_as_node_replay_does_not_reseed,
              t14_spawn_drops_quietly_when_the_row_vanished,
              t15_approval_receipt_records_actor,
              t16_invalid_args_is_countable,
              t17_throat_tells_crash_from_dropped]
    for f in checks:
        f()
    print(f"\nS17 门禁通过：{len(checks)} 组 —— interrupt 后 tasks 清出核对 1 组 + "
          f"不抢槽/连点幂等 1 组 + human-input 409 端点半边 1 组 + 断点落态/停止/start 1 组 + "
          f"真图真审批卡驻留四格 1 组 + 批准真执行/拒绝不执行 1 组 + "
          f"特殊命令不进审批面 1 组 + 跨进程重启仍驻留且真恢复 1 组 + 会话失败留可 grep 告警 1 组 + "
          f"未知命令可数 1 组 + ask_human 不重放副作用 1 组（C59）+ "
          f"**审批×ask 交替与重启停在 ask 1 组（C59 未验边界闭合）** + "
          f"**外层节点重放不重复播种 1 组（C72）** + "
          f"**起跑咽喉撞空会话不抛穿/不留死槽 1 组（t8 现证的那条竞态）** + "
          f"**回执记审批人 who/when、首到生效、旧格式兼容、两档同形 1 组（C119）** + "
          f"**无效 args 第四计数：schema 违例落账、回喂不变、不虚计、快照带出 1 组（C122）** + "
          f"**咽喉分得清装配崩溃与行已不在 + 多 worker 撞车口拒收 1 组（C125/C131）**")


if __name__ == "__main__":
    import json
    import sys
    if "--worker" in sys.argv:                        # t8 格② / t12② 的子进程模式，见 `_worker`
        a = sys.argv[sys.argv.index("--worker") + 1:]
        print("RESULT " + json.dumps(_worker(a[0], Path(a[1]), a[2], a[3] if len(a) > 3 else "",
                                             a[4] if len(a) > 4 else "approve"),
                                     ensure_ascii=False), flush=True)
    else:
        main()
