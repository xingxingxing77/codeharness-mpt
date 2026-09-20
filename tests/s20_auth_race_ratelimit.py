"""S20 门禁：B12 —— 注册竞态丢账号 + 登录无限试。

为什么另起一份而不并进 `tests/s10_auth.py`：那份在工作树里是**别人未提交的改动**
（治理队列铁律 4：不碰、不提交、不回退），把 B12 的读数混进去两笔账就分不清了。

  t1 两线程并发 register 不同用户名 → users.json 两个都在。
     读-改-写的窗口用「替换 `_save_users` 加 150ms 延时」撑开（真实世界里这个量级就是
     pbkdf2 120k 轮的耗时）。**对照组**用修复前的 register 本体（无锁、hash 在 load 之后算）
     跑同一个延时 → 表里只剩后写的那一个账号。没有对照组，「两个都在」只说明代码没崩。
  t2 连续 6 次错密：前 5 次仍是 401（= 修复前的行为，一条不少），第 6 次起 429；
     被限期间连正确密码也拒（限的是这个用户名的尝试次数）；窗口过期后恢复；
     成功登录会把计数清零（否则第 7 次就永久锁着）。

跑法：
  cd /e/Codeharness && PYTHONPATH=/e/Codeharness:/e/Codeharness/logs PYTHONIOENCODING=utf-8 \\
    PYTHONDONTWRITEBYTECODE=1 F:/anaconda/python.exe tests/s20_auth_race_ratelimit.py
"""
import contextlib
import tempfile
import threading
import time
from pathlib import Path

import server.auth as A


@contextlib.contextmanager
def _env(with_client: bool = False):
    """用户表/会话表指 tmp，不碰 server/data/users.json；不起 redis（本机 6379 是共享实例）。"""
    import server.sessions as ss
    from codeharness.configs.settings import settings
    tmp = Path(tempfile.mkdtemp())
    keep = (A.USERS_FILE, ss.SESSIONS_FILE, settings.platform.use_redis,
            settings.platform.auth_enabled, dict(A._login_fails))
    A.USERS_FILE = tmp / "users.json"
    ss.SESSIONS_FILE = tmp / "sessions.json"
    settings.platform.use_redis = False
    settings.platform.auth_enabled = True
    A._login_fails.clear()
    try:
        if with_client:
            from fastapi.testclient import TestClient
            from server.app import create_app
            with TestClient(create_app()) as c:
                assert type(c.app.state.store).__name__ == "SessionStore", "没隔离开，碰到共享 redis 了"
                yield c
        else:
            yield None
    finally:
        A.USERS_FILE, ss.SESSIONS_FILE = keep[0], keep[1]
        settings.platform.use_redis, settings.platform.auth_enabled = keep[2], keep[3]
        A._login_fails.clear()
        A._login_fails.update(keep[4])


def _slow_save(seconds=0.15):
    """把「load 之后、落盘之前」这段窗口撑开，让竞态可判定。返回恢复函数。"""
    real = A._save_users

    def slow(users):
        time.sleep(seconds)
        real(users)

    A._save_users = slow
    return lambda: setattr(A, "_save_users", real)


def _register_without_lock(username: str, password: str):
    """修复前的 register 本体（无锁、hash 在 load 之后才算）——只给对照组用。"""
    users = A._load_users()
    if username in users:
        raise ValueError("用户名已存在")
    salt = A.secrets.token_hex(16)
    users[username] = {"salt": salt, "hash": A._hash(password, salt), "created": "ctrl"}
    A._save_users(users)


def _concurrent(fn, names):
    def run(n):
        try:
            fn(n, "secret123")
        except Exception as e:                       # 409 / WinError 32 之类都要被看见，不许静默吞
            errs.append(f"{n}: {type(e).__name__}: {str(e)[:120]}")

    errs: list = []
    th = [threading.Thread(target=run, args=(n,)) for n in names]
    for t in th:
        t.start()
    for t in th:
        t.join(20)
    return set(A._load_users()), errs


def t1_concurrent_register_keeps_both():
    print("  t1 并发 register ...", end=" ", flush=True)
    with _env():
        restore = _slow_save()
        try:
            both, errs = _concurrent(A.register, ("race_a", "race_b"))
            # 对照组：同一个延时下，修复前的本体确实丢账号。换一份独立的表，
            # 免得两轮的 `users.tmp`（这个名字是写死的）互相抢文件。
            A.USERS_FILE = A.USERS_FILE.parent / "users_ctrl.json"
            (A.USERS_FILE.parent / "users_ctrl.tmp").unlink(missing_ok=True)
            assert A._load_users() == {}, "清表没生效，对照组会被上一轮的账号干扰"
            ctrl, ctrl_errs = _concurrent(_register_without_lock, ("race_a", "race_b"))
        finally:
            restore()
        assert not errs, f"加锁后两笔注册竟报错：{errs}"
        assert both == {"race_a", "race_b"}, f"并发注册后表里是 {both}，两个都该在"
        assert len(ctrl) == 1 or ctrl_errs, \
            f"对照组竟然两个账号都活着且无报错（{ctrl}）——这条判据分辨不出竞态，t1 的正判据也不成立"
    shape = f"只剩 {sorted(ctrl)}" if len(ctrl) == 1 else f"replace 撞车 {ctrl_errs[0]}"
    print(f"✅ 修复后两线程 race_a/race_b 都在；对照组（无锁、同延时）{shape}"
          "（同一个根因：整文件读-改-写没互斥，且 `_save_users` 的 tmp 是写死的同名文件）")


def t2_login_rate_limited():
    print("  t2 登录限流 ...", end=" ", flush=True)
    with _env(with_client=True) as c:
        r = c.post("/api/auth/register", json={"username": "alice", "password": "secret123"})
        assert r.status_code == 200, r.text[:120]
        bad = [c.post("/api/auth/login", json={"username": "alice", "password": "wrong"}).status_code
               for _ in range(6)]
        assert bad[:5] == [401] * 5, f"前 5 次该照常 401（修复前的行为，不该被误伤）：{bad}"
        assert bad[5] == 429, f"第 6 次起该被拒：{bad}"
        r = c.post("/api/auth/login", json={"username": "alice", "password": "secret123"})
        assert r.status_code == 429, f"被限期间真密码也该拒（限的是尝试次数）：{r.status_code}"
        assert "后再试" in r.text, f"429 该给等待提示：{r.text[:120]}"
        # 别的用户名不受牵连
        r = c.post("/api/auth/register", json={"username": "bob", "password": "secret123"})
        assert r.status_code == 200, f"bob 的注册被 alice 的计数挡住：{r.status_code} {r.text[:120]}"
        # 窗口过期后恢复（把窗口压到 0.2s，不真等 60s）
        keep_w = A._LOGIN_WINDOW
        A._LOGIN_WINDOW = 0.2
        try:
            time.sleep(0.3)
            r = c.post("/api/auth/login", json={"username": "alice", "password": "secret123"})
            assert r.status_code == 200, f"窗口过期后应放行，实际 {r.status_code}: {r.text[:120]}"
        finally:
            A._LOGIN_WINDOW = keep_w
        # 成功登录已清零：再错满 5 次才会被限，第 6 次 429
        again = [c.post("/api/auth/login", json={"username": "alice", "password": "wrong"}).status_code
                 for _ in range(6)]
        assert again[:5] == [401] * 5 and again[5] == 429, f"计数没在成功登录时清零或清零后又没生效：{again}"
    print("✅ 前 5 次 401、第 6 次 429（真密码也拒、提示含等待秒数）；窗口过期恢复；成功登录清零后再计一轮仍生效")


def main():
    print("=" * 60)
    print("S20: 注册竞态 + 登录限流（B12）")
    print("=" * 60)
    try:
        t1_concurrent_register_keeps_both()
        t2_login_rate_limited()
        print("\n" + "=" * 60 + "\n✅ 全部通过 (t1–t2 2/2 全绿)\n" + "=" * 60)
        return 0
    except AssertionError as e:
        print(f"\n❌ 失败：{e}")
        return 1
    except Exception as e:
        print(f"\n❌ 异常：{type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
