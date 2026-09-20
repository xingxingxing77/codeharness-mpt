"""S13 门禁：S5 的 Redis/Qdrant 鉴权接线（不打 Docker，用真 redis 进程 + 抓头 HTTP 服务）。

compose 里给 redis 上 --requirepass、给 qdrant 上 api-key 只是半截；另外半截是本仓真把
凭据带上——两处此前都是断的，而且是共享出口断的：
  t1 to_url()：以前静默丢掉 username/password，而 platforms/ 五件 + runner 控制通道 +
     utils/redis 全走这一个出口。没密码时输出必须与改动前逐字节相同（本机直连不破）。
  t2 真进程：起一个 --requirepass 的一次性 redis-server，不带凭据必须 NOAUTH，
     带 settings.to_url() 必须读写通——这条把「to_url 是承重的」钉成实测而不是推断。
  t3 qdrant 的 api_key 真上到线上：本地抓头服务收 GET /collections/x，断言 Authorization
     带密钥（此前 QdrantConfig.api_key 全仓零读者，配了也不发）。
  t4 compose 文本：两处 `:?` 必填守卫与 --requirepass 在位，.env.example 列了两个变量。

跑法：
  cd /e/Codeharness && PYTHONPATH=/e/Codeharness:/e/Codeharness/logs PYTHONIOENCODING=utf-8 \\
    PYTHONDONTWRITEBYTECODE=1 F:/anaconda/python.exe tests/s13_redis_qdrant_auth.py
"""
import asyncio
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import redis as sync_redis

from codeharness.configs.settings import RedisConfig, settings

ROOT = Path(__file__).resolve().parent.parent
PW = "p@ss:w/rd"          # 故意带 @ 与 : ——不编码就会把分隔符拼进 URL


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def t1_to_url_carries_credentials():
    base = RedisConfig(host="h", port=6399, db=2)
    assert base.to_url() == "redis://h:6399/2", f"无凭据时不得改动原样，实际 {base.to_url()}"
    got = base.model_copy(update={"password": PW}).to_url()
    assert got == "redis://:p%40ss%3Aw%2Frd@h:6399/2", got
    got2 = base.model_copy(update={"username": "u1", "password": "a"}).to_url()
    assert got2 == "redis://u1:a@h:6399/2", got2
    got3 = base.model_copy(update={"password": "a", "ssl": True}).to_url()
    assert got3 == "rediss://:a@h:6399/2", got3
    print("  t1 to_url 带上凭据（含 @ : / 的百分号编码），无凭据时与改动前逐字节相同")


def t2_real_redis_requires_and_accepts():
    """一次性 redis-server 开 --requirepass：不带凭据 NOAUTH，带 to_url() 读写通。"""
    exe = Path("F:/Redis/redis-server.exe")
    cli = Path("F:/Redis/redis-cli.exe")
    if not exe.exists():
        print(f"  t2 跳过：本机无 {exe}，真进程读数拿不到（t1 已钉住 URL 构造）")
        return
    port = _free_port()
    proc = subprocess.Popen([str(exe), "--port", str(port), "--requirepass", PW, "--save", ""],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        cfg = settings.redis.model_copy(update={"host": "127.0.0.1", "port": port, "password": PW})
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

        # ① 不带凭据（= 修复前的 to_url 形状）必须被拒
        bare = sync_redis.Redis(host="127.0.0.1", port=port)
        try:
            bare.ping()
            raise AssertionError("--requirepass 没生效：匿名 ping 竟然通了")
        except sync_redis.RedisError as e:      # 各版本把 AuthenticationError 挂在 ConnectionError 下，
            assert "Authentication" in str(e) or "NOAUTH" in str(e), \
                f"应因鉴权被拒，实际 {type(e).__name__}: {e}"      # 本机实测：Authentication required.
        # ② 走修好的共享出口必须通（写读一遍，证明不只是握手）
        r = sync_redis.Redis.from_url(cfg.to_url())
        r.set("s13:probe", "ok")
        assert r.get("s13:probe") == b"ok", "带凭据的连接读不回"
        # ③ 裸协议的客户端命令行也一样被拒（复现「同网段任意主机 KEYS *」那条排查切入点）
        if cli.exists():
            out = subprocess.run([str(cli), "-p", str(port), "keys", "*"],
                                 capture_output=True, text=True)
            got = (out.stdout + out.stderr).strip()
            assert "NOAUTH" in got or "unauthenticated" in got.lower(), \
                f"redis-cli 匿名应被拒，实际 {got[:80]!r}"    # 本机实读：ERR Protocol error: unauthenticated multibulk length
        # ④ 异步侧同源：platforms/event_store 用的 aioredis.from_url 走同一个 to_url
        import redis.asyncio as aioredis

        async def _a():
            c = aioredis.from_url(cfg.to_url())
            try:
                await c.ping()
            finally:
                await c.aclose()
        asyncio.run(_a())
    finally:
        proc.terminate()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()
    print("  t2 真 redis：匿名 ping 被拒(Authentication required)、redis-cli keys 被拒(unauthenticated)；"
          "to_url() 带凭据读写通、异步侧同源")


class _Capture(BaseHTTPRequestHandler):
    seen: list = []

    def do_GET(self):
        # qdrant-client 把密钥放在 REST 头 `api-key`（async_qdrant_remote.py:141），不是 Authorization
        type(self).seen.append((self.path,
                                self.headers.get("api-key") or self.headers.get("Authorization")))
        body = b'{"result": {"exists": true}, "status": "ok", "time": 0.001}'
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):        # 别往门禁输出里刷屏
        pass


def t3_qdrant_api_key_reaches_the_wire():
    from codeharness.document_store.qdrant_store import QdrantStore

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Capture)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    keep = settings.qdrant.api_key
    try:
        _Capture.seen = []
        settings.qdrant.api_key = "wire-test-key"
        st = QdrantStore(url=f"http://127.0.0.1:{port}")

        async def _ask():
            try:
                await st.client.collection_exists("codeharness")
            finally:
                await st.client.close()
        asyncio.run(_ask())
        assert _Capture.seen, "一次请求都没收到——qdrant 客户端根本没打出去"
        path, hdr = _Capture.seen[0]
        assert path.startswith("/collections/"), f"请求路径不对：{path}"
        assert hdr and "wire-test-key" in hdr, f"api_key 没上线：api-key 头={hdr!r}"
        # 反向：不设密钥时不得凭空造出 api-key 头（本机/CI 无密钥形态必须不破）
        settings.qdrant.api_key = ""
        _Capture.seen = []
        st2 = QdrantStore(url=f"http://127.0.0.1:{port}")

        async def _ask2():
            try:
                await st2.client.collection_exists("codeharness")
            finally:
                await st2.client.close()
        asyncio.run(_ask2())
        assert _Capture.seen[0][1] in (None, ""), f"空 api_key 却发了鉴权头：{_Capture.seen[0][1]!r}"
    finally:
        settings.qdrant.api_key = keep
        srv.shutdown()
        srv.server_close()
    print("  t3 Qdrant 客户端真把密钥发到 api-key 头上（空值时不发头）")


def t4_compose_and_example_wired():
    y = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "--requirepass" in y and "${REDIS_PASSWORD:?need REDIS_PASSWORD}" in y, "redis 缺必填密钥守卫"
    assert "QDRANT__SERVICE__API_KEY" in y and "${QDRANT__API_KEY:?need QDRANT__API_KEY}" in y, \
        "qdrant 缺必填密钥守卫"
    assert "REDIS__PASSWORD:" in y and "QDRANT__API_KEY:" in y, "backend 没收到同源凭据"
    ex = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "REDIS_PASSWORD=" in ex and "QDRANT__API_KEY=" in ex, ".env.example 少变量"
    assert ".env" in {l.strip() for l in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()}, \
        ".env 不再被忽略——真值会入库"
    print("  t4 compose 两处 :? 守卫 + backend 同源凭据 + .env.example 两变量 + .env 仍不入库")


def main():
    print("=" * 60)
    print("S13: Redis/Qdrant 鉴权接线（S5）")
    print("=" * 60)
    try:
        t1_to_url_carries_credentials()
        t2_real_redis_requires_and_accepts()
        t3_qdrant_api_key_reaches_the_wire()
        t4_compose_and_example_wired()
        print("\n" + "=" * 60 + "\n✅ 全部通过 (4/4)\n" + "=" * 60)
        return 0
    except AssertionError as e:
        print(f"\n❌ 失败：{e}")
        return 1
    except Exception as e:
        print(f"\n❌ 异常：{e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
