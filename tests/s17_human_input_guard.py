"""S17 门禁（B2）：`runner.answer_human` 不得覆盖同 sid 的运行任务引用。

钉三件事：
  t1 **前置核对**（总文档 §B2 要求先核的那条）：图 interrupt 后 `_run` 走 `finally` 把
     `tasks[sid]` 清出 → 人工回答时刻 `is_running()` 已是 False，所以守卫用 `is_running` 就够，
     不需要「按状态分流」。同时把收尾时的 store 状态如实记进读数（不假设）。
  t2 runner 级：会话真在跑（有活任务）时 `answer_human` 必须返回 False 且**不换槽**；
     连点两次只起一个 resume 任务；无活任务（awaiting_human）时照常起。
  t3 HTTP 级：running 时 POST /api/sessions/{sid}/human-input → 409，且 tasks 槽未被覆盖。

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
        return runner.is_running(s.id), s.id in runner.tasks, str(store.get(s.id).status)

    running_after, in_tasks, status = asyncio.run(body())
    assert running_after is False, "t1 核对失配：interrupt 后仍算在跑 → 守卫要改成按状态分流"
    assert in_tasks is False, "t1 核对失配：interrupt 后 tasks 里还留着活任务"
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


def main():
    checks = [t1_interrupt_clears_task_slot, t2_no_slot_steal, t3_endpoint_returns_409]
    for f in checks:
        f()
    print(f"\nS17 门禁通过：{len(checks)} 组 —— interrupt 后 tasks 清出核对 1 组 + "
          f"不抢槽/连点幂等 1 组 + human-input 409 端点半边 1 组")


if __name__ == "__main__":
    main()
