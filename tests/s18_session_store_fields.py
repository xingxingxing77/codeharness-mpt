"""S18 门禁：B3 —— `RedisSessionStore.update(persist=True)` 的注释说「只写传进来的字段」，
实现却是 `_dump(s)` 全量 HSET 整哈希。读-改-写整哈希意味着并发下两条路径会拿自己的旧快照
互相覆盖：`_sync_cost` 高频写 `cost` 与 API 写 `status` 同刻发生，后写的那一把把对方的字段
退回读之前的值。文件头宣称的「字段级更新天然并发安全」在修复前是假话。

  t1 persist 分支只 HSET 传入字段（钉住 mapping 的键集合），且归一不丢：
     枚举写成 value 而不是 `SessionStatus.running`，dict 字段走 `_JSON_FIELDS` 的 json.dumps，
     未传的字段不得被本次 update 按旧快照覆回去；返回值语义不变（仍是整条 Session）。
  t2 **验收主判据**：两线程对同一 sid 各改不同字段，互读不丢。
     用 Barrier 把「两线程都已 get」钉成前置条件——这样写序无论怎样都构成经典的
     丢失更新（lost update）：修复前最后写的一方用旧快照覆掉对方字段（确定性地红），
     修复后各写各的字段，两个新值都在。

对照模式（证明这两条真在验 B3，不是恒绿）：`S18_OLD_UPDATE=1` 会把 `update` 换回修复前的
全量重写实现，此时 t1/t2 都必须红。

跑法（一次性 redis-server，绝不指本机 6379 共享实例）：
  cd /e/Codeharness && PYTHONPATH=/e/Codeharness:/e/Codeharness/logs PYTHONIOENCODING=utf-8 \\
    PYTHONDONTWRITEBYTECODE=1 F:/anaconda/python.exe tests/s18_session_store_fields.py
"""
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import redis as sync_redis

from codeharness.configs.settings import RedisConfig
from platforms.session_store import KEY, RedisSessionStore, _dump
from server.sessions import Session, SessionStatus


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _old_update(self, sid: str, persist: bool = True, **fields) -> Session:
    """修复前的实现：读-改-写后用 `_dump(s)` 全量 HSET 整哈希。只给对照模式用。"""
    s = self.get(sid)
    if s is None:
        raise KeyError(sid)
    data = s.model_dump()
    data.update(fields)
    s = Session(**data)
    if persist:
        self.r.hset(KEY.format(sid), mapping=_dump(s))
    return s


def _mk_store(r):
    store = RedisSessionStore(RedisConfig(host="127.0.0.1", port=r["port"], db=r["db"]))
    kw = store.r.connection_pool.connection_kwargs
    assert (kw["host"], kw["port"]) == ("127.0.0.1", r["port"]), \
        f"连接没指到一次性实例（实际 {kw['host']}:{kw['port']}）——别碰本机共享 redis"
    return store


def t1_only_passed_fields_are_written(store, sid):
    """mapping 的键集合必须恰为传入字段；枚举/JSON 字段的归一与返回语义不得回退。"""
    seen: list = []
    real_hset = store.r.hset

    def spy(name, *a, **kw):
        if kw.get("mapping") is not None:
            seen.append(set(kw["mapping"]))
        return real_hset(name, *a, **kw)

    store.r.hset = spy
    try:
        s = store.update(sid, status=SessionStatus.running, error="boom")
    finally:
        store.r.hset = real_hset
    assert seen and seen[-1] == {"status", "error"}, \
        f"persist 分支写了 {len(seen[-1]) if seen else 0} 个字段 {sorted(seen[-1]) if seen else None}，" \
        f"不是恰好的传入字段（全量重写=并发覆盖的根因）"
    raw_status = store.r.hget(KEY.format(sid), "status")
    assert raw_status == "running", f"枚举必须落成 value，实际 {raw_status!r}"
    assert s.status == SessionStatus.running and s.error == "boom", "返回值语义变了"
    assert s.idea == "s18" and s.id == sid, "返回的整条对象里未传的字段丢了"
    # 带外改一个本次没传的字段：全量重写会按自己读到的旧快照把它覆回去
    store.r.hset(KEY.format(sid), "project_name", "renamed-out-of-band")
    store.update(sid, status=SessionStatus.finished)
    assert store.get(sid).project_name == "renamed-out-of-band", \
        "带外改的字段被 update 的旧快照覆写了——还是全量重写"
    store.update(sid, cost={"prompt": 12, "completion": 3})
    raw_cost = store.r.hget(KEY.format(sid), "cost")
    assert raw_cost == '{"prompt": 12, "completion": 3}', f"cost 没走 json.dumps：{raw_cost!r}"
    assert store.get(sid).cost == {"prompt": 12, "completion": 3}
    print("  t1 persist 只写传入字段（mapping 键集合=={status,error}）；未传字段不被覆写；"
          "枚举落成 value、cost 走 json.dumps、返回值仍是整条 Session")


def t2_concurrent_field_updates_no_loss(store, sid):
    """两线程各改不同字段：Barrier 保证双方都读过后才各自写 = 必然构成丢失更新的前置。"""
    store.update(sid, status=SessionStatus.created, cost={"prompt": 0})
    real_get = RedisSessionStore.get
    barrier = threading.Barrier(2)

    def get_then_wait(self, _sid):        # 本测试只有一个 sid，读哪个都用闭包里的这个
        s = real_get(self, sid)
        barrier.wait(10)      # 两条线程都读完了初始状态，此后谁先写都不影响对方读到旧值
        return s

    errors: list = []

    def writer(**fields):
        try:
            store.update(sid, **fields)
        except Exception as e:                       # Barrier 超时/断连也要显式暴露
            errors.append(f"{type(e).__name__}: {e}")

    RedisSessionStore.get = get_then_wait
    try:
        th = [threading.Thread(target=writer, kwargs={"status": SessionStatus.running}),
              threading.Thread(target=writer, kwargs={"cost": {"prompt": 4242}})]
        for t in th:
            t.start()
        for t in th:
            t.join(20)
        assert not any(t.is_alive() for t in th), "有线程没在 20s 内收尾"
    finally:
        RedisSessionStore.get = real_get
    assert not errors, f"并发 update 抛错：{errors}"
    s = store.get(sid)
    assert s.status == SessionStatus.running, \
        f"status 被 cost 那条线程的旧快照覆回 {s.status.value}（丢失更新——B3 的现场）"
    assert s.cost == {"prompt": 4242}, \
        f"cost 被 status 那条线程的旧快照覆回 {s.cost}（丢失更新——B3 的现场）"
    print("  t2 两线程各改 status / cost，两个新值都在（互不丢失）")


def main():
    print("=" * 60)
    print("S18: RedisSessionStore.update 字段级 HSET（B3）")
    print("=" * 60)
    exe = Path("F:/Redis/redis-server.exe")
    if not exe.exists():
        print(f"⚠️  跳过全部：本机无 {exe}，真服务读数拿不到（退出码 0 不代表验过）")
        return 0
    port, db = _free_port(), 3
    r = {"port": port, "db": db}
    control = bool(os.environ.get("S18_OLD_UPDATE"))
    if control:
        RedisSessionStore.update = _old_update
        print("  ⚠ 对照模式：update 已换回修复前的全量重写——下面 t1/t2 必须都红")
    proc = subprocess.Popen([str(exe), "--port", str(port), "--save", "",
                             "--dir", tempfile.mkdtemp(prefix="s18_rd_")],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        cfg = RedisConfig(host="127.0.0.1", port=port, db=db)
        deadline = 10.0
        while deadline > 0:
            try:
                sync_redis.Redis.from_url(cfg.to_url()).ping()
                break
            except Exception:
                time.sleep(0.3)
                deadline -= 0.3
        else:
            raise AssertionError("一次性 redis-server 起不来，本条判据拿不到读数")
        store = _mk_store(r)
        assert store.r.flushdb(), "清库没生效——判据会受残留数据干扰"
        sid = store.create(idea="s18", project_name=f"s18-{int(time.time())}").id
        fails = []
        for fn in (t1_only_passed_fields_are_written, t2_concurrent_field_updates_no_loss):
            try:
                fn(store, sid)
            except AssertionError as e:
                fails.append(f"{fn.__name__}: {e}")
                print(f"  ❌ {fn.__name__}：{e}")
        if fails:
            print(f"\n❌ 失败 {len(fails)}/2 条")
            return 1
        print("\n" + "=" * 60 + "\n✅ 全部通过 (t1–t2 2/2 全绿)\n" + "=" * 60)
        return 0
    except Exception as e:
        print(f"\n❌ 异常：{e}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        proc.terminate()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
