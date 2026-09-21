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
    """假图：按需产出事件，`hold` 不释就不收口（用来模拟「会话真在跑」）。"""

    def __init__(self, events=(), hold=None):
        self.events, self.hold = list(events), hold

    async def astream_events(self, _input, _config, version=None):
        for ev in self.events:
            yield ev
        if self.hold is not None:
            await self.hold.wait()


INTERRUPT = {"event": "on_interrupt", "data": {"chunk": None},
             "value": {"question": "要哪个方案？"}}


def _install(runner, team, project):
    async def fake_prepare(session, proj, cost_manager):
        return team, {"configurable": {"thread_id": proj}}, {"messages": []}

    runner._prepare = fake_prepare
    runner.projects[project] = project


def t1_interrupt_clears_task_slot():
    """前置核对：interrupt 后 `_run` 是否已从 `tasks` 清出（决定守卫写法）。"""
    store, runner, s = _make_runner()
    _install(runner, _Team(events=[INTERRUPT]), s.project_name)

    async def body():
        task = asyncio.create_task(runner._run(s))
        runner.tasks[s.id] = task
        await task
        return runner.is_running(s.id), s.id in runner.tasks, store.get(s.id).status.value

    running_after, in_tasks, status = asyncio.run(body())
    assert running_after is False, "t1 核对失配：interrupt 后仍算在跑 → 守卫要改成按状态分流"
    assert in_tasks is False, "t1 核对失配：interrupt 后 tasks 里还留着活任务"
    assert status == "awaiting_human", \
        f"t1 核对失配：interrupt 收尾后 store 状态={status!r}，不是 awaiting_human——" \
        "收尾那句又在无条件写 finished（见 t4 对照组）"
    print(f"  ok  t1（核对读数：interrupt 收尾后 is_running=False、tasks 已清出、"
          f"store 状态={status!r}）")


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
    _install(runner, _Team(events=[INTERRUPT]), s.project_name)

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
    _install(runner3, _Team(events=[INTERRUPT]), s3.project_name)

    def old_settle(sid):
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


def main():
    checks = [t1_interrupt_clears_task_slot, t2_no_slot_steal, t3_endpoint_returns_409,
              t4_breakpoint_settles_and_stops]
    for f in checks:
        f()
    print(f"\nS17 门禁通过：{len(checks)} 组 —— interrupt 后 tasks 清出核对 1 组 + "
          f"不抢槽/连点幂等 1 组 + human-input 409 端点半边 1 组 + 断点落态/停止/start 1 组")


if __name__ == "__main__":
    main()
