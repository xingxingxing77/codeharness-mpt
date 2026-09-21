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

PAGE_FIELDS = {"checkpoint_id", "step", "source", "ts", "next", "writes", "tasks"}


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
    assert all(isinstance(it["writes"], list) for it in items), "writes 该是键名数组"

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
            runner._ensure_graph = lambda _sid: asyncio.sleep(0, (app, cfg))   # 注入真图

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
            runner._ensure_graph = lambda _sid: asyncio.sleep(0, None)
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


def main():
    try:
        asyncio.run(t1_page_semantics())
        asyncio.run(t2_endpoints())
        asyncio.run(t3_read_only())
    finally:
        # 不关连接的话解释器会被 aiosqlite 的后台线程吊住（C2 那轮的收官教训：断言全过但永不退出）
        asyncio.run(close_all())
    print("\ns21_checkpoint_replay: 3/3 全绿")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
