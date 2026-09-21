"""S20 门禁：B12 —— 注册竞态丢账号 + 登录无限试。

为什么另起一份而不并进 `tests/s10_auth.py`：那份在工作树里是**别人未提交的改动**
（治理队列铁律 4：不碰、不提交、不回退），把 B12 的读数混进去两笔账就分不清了。

  t1 两线程并发 register 不同用户名 → users.json 两个都在。
     读-改-写的窗口用「替换 `_save_users` 加 150ms 延时」撑开（真实世界里这个量级就是
     pbkdf2 120k 轮的耗时）。**对照组**用修复前的 register 本体（无锁、hash 在 load 之后算）
     跑同一个延时 → 表里只剩后写的那一个账号。没有对照组，「两个都在」只说明代码没崩。
  t1b F-D：`_save_users` 的临时名原先写死 `users.tmp`（治理 §3 第 5 条）。A/B 用同一个强制
     交错（两笔都写完 scratch 再一起去 replace），只差 scratch 怎么起名：旧形状必须报错，
     一次性临时名必须零报错且表合法、无残留 .tmp。
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


def _save_once(save, table, errs):
    try:
        save(table)
    except Exception as e:
        errs.append(f"{type(e).__name__}: {e}")


def t1b_concurrent_save_shares_one_tmp():
    """F-D（治理 §3 第 5 条）：`_save_users` 的临时名原先写死 `users.tmp`。

    失效形态**不是**抛异常那么客气。`write_text` 内部是 open('w')→write→close，两笔并发
    同名写会各自 truncate、各自从 0 覆写；短的那笔盖不掉长的那笔的尾巴 →
    **users.json 变成半新半旧的坏 JSON**，从此 `_load_users` 一调就炸，全站注册/登录 500。
    （本机实测：两笔同名 scratch 一起 `tmp.replace()` 反而不报错，见段 5 的读数——
    所以判据钉在"表被写坏"这条确定形态上，不钉在 WinError 上。）

    三格：① 手工按 write_text 的步骤交错同一支文件，证明上面那条腐败形态是真的（前提格）；
    ② 真 `_save_users` 多线程多轮混长度写，每轮都必须还能 `json.loads`（生产路径）；
    ③ 每笔 save 必须各起一个一次性名字（观察 `_save_users` 实际调用了几次随机名）。"""
    import json
    print("  t1b 并发 _save_users 的临时名 ...", end=" ", flush=True)
    with _env():
        long_tbl = {f"u{i}": {"salt": "s", "hash": "h" * 40, "created": "x" * 30} for i in range(40)}
        short_tbl = {"u1": {"salt": "s", "hash": "h", "created": "x"}}
        lj, sj = json.dumps(long_tbl), json.dumps(short_tbl)

        # ① 前提：同一支文件、两个句柄，各自从 0 覆写
        same = A.USERS_FILE.with_suffix(".tmp")
        h1, h2 = same.open("w", encoding="utf-8"), same.open("w", encoding="utf-8")
        h1.write(lj); h1.flush()            # 长的那笔先落
        h2.write(sj); h2.flush()            # 短的那笔只盖住前 len(sj) 字节
        h2.close(); h1.close()
        wrecked = same.read_text(encoding="utf-8")
        assert len(wrecked) == len(lj) and wrecked.startswith(sj), \
            f"①前提不成立：同名 scratch 没写出半新半旧的表（长度 {len(wrecked)} vs {len(lj)}）"
        try:
            json.loads(wrecked)
            raise AssertionError("①前提不成立：那支坏文件竟然还能解析")
        except json.JSONDecodeError:
            pass
        same.unlink()                     # ①的道具清掉，否则 ② 的「无残留 .tmp」会被自己绊倒
                                          # （第一次跑就是这么红的：断言抓的是测试自己的垃圾）

        # ② 生产路径：4 个写者 + 2 个读者同时跑（读侧就是 `verify()` 那条不加锁的路），
        #    每一步都必须还能解析、不许抛异常
        errs: list = []
        tables = [long_tbl, short_tbl,
                  {f"k{i}": {"salt": "s", "hash": "h", "created": "x"} for i in range(20)},
                  {"only": {"salt": "s", "hash": "h", "created": "x"}}]

        def hammer(n, save=None, out=None):
            out = errs if out is None else out
            for k in range(8):
                _save_once(save or A._save_users, tables[(n + k) % len(tables)], out)

        def reader(errs):
            for _ in range(40):
                _save_once(lambda _t: A._load_users(), None, errs)

        th = ([threading.Thread(target=hammer, args=(n,)) for n in range(4)] +
              [threading.Thread(target=reader, args=(errs,)) for _ in range(2)])
        for t in th:
            t.start()
        for t in th:
            t.join(40)
        assert not errs, f"②失效：并发读写用户表报错 {errs[:2]}"
        assert isinstance(A._load_users(), dict), "②失效：并发之后用户表读不出来了"
        left = [p.name for p in A.USERS_FILE.parent.glob("*.tmp")]
        assert not left, f"②一次性临时名留下了没被 rename 走的垃圾：{left[:3]}"

        # ③ 每笔 save 各起一个名字（旧写法这里计数为 0 → 当场红）
        seen: list = []
        real_hex = A.secrets.token_hex
        A.secrets.token_hex = lambda n, **kw: (seen.append(real_hex(n)), seen[-1])[1]
        try:
            A._save_users(long_tbl)
            A._save_users(short_tbl)
        finally:
            A.secrets.token_hex = real_hex
        assert len(seen) >= 2 and len(set(seen)) == len(seen), \
            f"③失效：两笔 save 只起了 {len(set(seen))} 个随机名（调用 {len(seen)} 次），临时名可能又是同一个"

        # ④ 对照组＝修复前本体（固定 users.tmp + 单次 replace），跑同一个 hammer：
        #    必须至少出一次错，否则 ② 是空转断言。换一支表，别污染 ②/③ 的落点。
        A.USERS_FILE = A.USERS_FILE.parent / "users_ctrl.json"

        def old_save(users):
            t = A.USERS_FILE.with_suffix(".tmp")
            t.write_text(json.dumps(users, ensure_ascii=False, indent=2), encoding="utf-8")
            t.replace(A.USERS_FILE)

        ctrl_errs: list = []
        th = [threading.Thread(target=hammer, args=(n, old_save, ctrl_errs)) for n in range(4)]
        for t in th:
            t.start()
        for t in th:
            t.join(40)
        try:
            A._load_users()
        except Exception as e:
            ctrl_errs.append(f"读表 {type(e).__name__}")
        assert ctrl_errs, "④对照组（写死名字+单次 rename）竟然全程无事——②的判据就没有区分力"
    print(f"✅ ①同名 scratch 手工交错实测写出坏 JSON；②4 写者+2 读者并发零报错、表可解析、无残留 .tmp；"
          f"③两笔 save 各起一次性名字（{len(seen)} 次全不同）；④对照组实测 {ctrl_errs[0][:52]}"
          f"（共 {len(ctrl_errs)} 次）")


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
        t1b_concurrent_save_shares_one_tmp()
        t2_login_rate_limited()
        print("\n" + "=" * 60 + "\n✅ 全部通过 (t1 / t1b / t2 3/3 全绿)\n" + "=" * 60)
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
