"""S10 门禁：N1 账号边界（登录/多租户隔离）。

形态：`PLATFORM__AUTH` 双态——
  t1 关（默认）=单机现状：无 token 全通、会话 user_id 恒 "default"（15 个既有门禁的存活前提）；
  t2-t5 开=强制登录：401/注册/登录/me、跨用户隔离（404 不泄露存在性）、跨用户同名项目 409、
  注册重名/错密码/非法用户名。
t6 前端契约：client 带 Authorization、401 清票、SSE access_token 变通、登录页接线（照 s8 惯例机器化）。

跑法：
  cd /e/Codeharness && PYTHONPATH=/e/Codeharness PYTHONIOENCODING=utf-8 F:/anaconda/python.exe tests/s10_auth.py
"""
import tempfile
from pathlib import Path

import server.sessions as ss
import server.auth as auth_mod

ROOT = Path(__file__).resolve().parent.parent


class _Env:
    """每个用例独立的会话表 + 用户表 + 开关（收尾恢复）。"""

    def __init__(self, auth_on: bool):
        self.keep_sess, ss.SESSIONS_FILE = ss.SESSIONS_FILE, Path(tempfile.mkdtemp()) / "sessions.json"
        self.keep_users, auth_mod.USERS_FILE = auth_mod.USERS_FILE, ss.SESSIONS_FILE.parent / "users.json"
        from codeharness.configs.settings import settings
        self.keep_auth, self.keep_redis = settings.platform.auth_enabled, settings.platform.use_redis
        settings.platform.auth_enabled = auth_on
        settings.platform.use_redis = False        # 账号边界测试不测 redis 接缝（s7 已钉）

    def __enter__(self):
        from fastapi.testclient import TestClient
        from server.app import create_app
        self.client = TestClient(create_app())
        self.client.__enter__()
        return self.client

    def __exit__(self, *exc):
        from codeharness.configs.settings import settings
        settings.platform.auth_enabled, settings.platform.use_redis = self.keep_auth, self.keep_redis
        self.client.__exit__(*exc)
        ss.SESSIONS_FILE = self.keep_sess
        auth_mod.USERS_FILE = self.keep_users


def _hdr(token: str):
    return {"Authorization": f"Bearer {token}"}


def t1_auth_off_is_current_behavior():
    with _Env(auth_on=False) as c:
        h = c.get("/api/health").json()
        assert h["auth_enabled"] is False
        assert c.get("/api/sessions").status_code == 200, "auth 关时不该要票"
        s = c.post("/api/sessions", json={"idea": "离线", "project_name": "s10off"}).json()
        assert s["user_id"] == "default", "auth 关恒 default（qdrant payload 逐字节兼容的前提）"
        assert c.get(f"/api/sessions/{s['id']}").status_code == 200
    print("  t1 auth 关（默认）：无票全通、user_id 恒 default——既有行为零破坏")


def t2_auth_on_flow():
    with _Env(auth_on=True) as c:
        assert c.get("/api/sessions").status_code == 401, "无票必须 401"
        tok = c.post("/api/auth/register", json={"username": "alice", "password": "secret1"}).json()["token"]
        assert c.get("/api/sessions", headers=_hdr(tok)).status_code == 200
        me = c.get("/api/auth/me", headers=_hdr(tok)).json()
        assert me == {"user": "alice"}
        # 重复注册 409 / 错密码 401 / 非法用户名 422
        assert c.post("/api/auth/register", json={"username": "alice", "password": "secret2"}).status_code == 409
        assert c.post("/api/auth/login", json={"username": "alice", "password": "wrong!"}).status_code == 401
        assert c.post("/api/auth/register", json={"username": "a b!", "password": "secret1"}).status_code == 422
        # 登录与登出
        tok2 = c.post("/api/auth/login", json={"username": "alice", "password": "secret1"}).json()["token"]
        c.post("/api/auth/logout", headers=_hdr(tok2))
        assert c.get("/api/auth/me", headers=_hdr(tok2)).status_code == 401, "登出后票必须失效"
        assert c.get("/api/auth/me", headers=_hdr(tok)).status_code == 200
    print("  t2 auth 开：401/注册/登录/me/登出失效/重名 409/错密 401/非法名 422")


def t3_cross_user_isolation():
    with _Env(auth_on=True) as c:
        ta = c.post("/api/auth/register", json={"username": "alice", "password": "secret1"}).json()["token"]
        tb = c.post("/api/auth/register", json={"username": "bob", "password": "secret2"}).json()["token"]
        sid = c.post("/api/sessions", json={"idea": "a 的会话"}, headers=_hdr(ta)).json()["id"]
        assert all(x["user_id"] == "alice" for x in c.get("/api/sessions", headers=_hdr(ta)).json())
        assert c.get("/api/sessions", headers=_hdr(tb)).json() == [], "bob 不能看见 alice 的列表"
        # 越权一律 404——get/start/stop/workspace 不泄露存在性
        for method, url in (("get", f"/api/sessions/{sid}"), ("start", f"/api/sessions/{sid}/start"),
                            ("stop", f"/api/sessions/{sid}/stop"), ("files", f"/api/sessions/{sid}/workspace/files"),
                            ("history", f"/api/sessions/{sid}/events/history?after=0"),
                            ("graph", f"/api/sessions/{sid}/graph")):
            rsp = c.get(url, headers=_hdr(tb)) if method in ("get", "files", "history", "graph") \
                else c.post(url, headers=_hdr(tb))
            assert rsp.status_code == 404, f"{method} 越权必须 404，得到 {rsp.status_code}"
        # 归属方一切正常
        assert c.get(f"/api/sessions/{sid}", headers=_hdr(ta)).status_code == 200
        # SSE 查询参数等价（EventSource 发不了 header）。⚠ 不能对归属方直接 GET /events——
        # 那是无限流，TestClient 一读就挂死（README 已登记的陷阱第二次应验）；用 /me 验
        # 查询参数通道 + bob 的 SSE 在流开始前 404 两侧钉住。
        assert c.get(f"/api/auth/me?access_token={ta}").json() == {"user": "alice"}
        assert c.get(f"/api/sessions/{sid}/events?after=0&access_token={tb}").status_code == 404
    print("  t3 跨用户隔离：列表/读取/操作/文件树/事件全 404，SSE access_token 变通按归属放行")


def t4_project_name_collision_across_users():
    with _Env(auth_on=True) as c:
        ta = c.post("/api/auth/register", json={"username": "alice", "password": "secret1"}).json()["token"]
        tb = c.post("/api/auth/register", json={"username": "bob", "password": "secret2"}).json()["token"]
        assert c.post("/api/sessions", json={"idea": "x", "project_name": "collide"},
                      headers=_hdr(ta)).status_code == 200
        assert c.post("/api/sessions", json={"idea": "y", "project_name": "collide"},
                      headers=_hdr(tb)).status_code == 409, "跨用户同名=产物目录冲突，create 即 409"
        assert c.post("/api/sessions", json={"idea": "z", "project_name": "collide"},
                      headers=_hdr(ta)).status_code == 200, "同用户重名沿用现状（多场同名合法）"
    print("  t4 同名项目跨用户 409 / 同用户沿用现状")


def t5_user_scoped_quota():
    """N1：配额按 user 分桶——alice 打满不影响 bob（quota 走真 redis，探活式）。"""
    import redis as _r
    from codeharness.configs.settings import settings
    try:
        _r.Redis.from_url(settings.redis.to_url(), decode_responses=True).ping()
    except Exception:
        print("  t5 跳过（无 Redis）")
        return
    with _Env(auth_on=True) as c:
        from platforms.quota import Quota
        import time as _t
        q = Quota()
        # 直打 quota 分桶语义（HTTP 30/min 烧满太慢）：alice 的桶打到上限，bob 的桶必须独立
        assert q.allow("create:alice", 2, 60) and q.allow("create:alice", 2, 60)
        assert not q.allow("create:alice", 2, 60), "alice 超限"
        assert q.allow("create:bob", 2, 60), "bob 的桶被 alice 连坐=分桶失效"
        win = int(_t.time() // 60)
        q.r.delete(f"ch:quota:create:alice:{win}", f"ch:quota:create:bob:{win}")
    print("  t5 配额按 user 分桶：alice 超限不连坐 bob")


def t6_frontend_contract():
    """照 s8 惯例把前端接线钉成断言（auth 半边在 client/store/App/Sidebar 四处）。"""
    fe = ROOT / "frontend" / "src"
    client = (fe / "api" / "client.ts").read_text(encoding="utf-8")
    assert "Authorization" in client and "Bearer" in client, "请求必须带票"
    assert "rsp.status === 401" in client and "setToken('')" in client, "401 必须清票"
    assert "/api/auth/login" in client and "/api/auth/register" in client, "auth API 面在 client"
    sse = (fe / "stores" / "sessions.ts").read_text(encoding="utf-8")
    assert "access_token=" in sse, "SSE 必须走查询参数变通（EventSource 发不了 header）"
    app = (fe / "App.vue").read_text(encoding="utf-8")
    assert 'v-if="auth.needLogin"' in app and "LoginPage" in app, "登录页接管必须在 App 根"
    assert "auth.init()" in app and "!auth.needLogin) store.init()" in app, "init 顺序：先 auth 后会话"
    assert (fe / "components" / "LoginPage.vue").exists()
    sb = (fe / "components" / "SessionSidebar.vue").read_text(encoding="utf-8")
    assert "doLogout" in sb and "auth.user" in sb, "侧栏必须有用户行+退出"
    types = (fe / "types.ts").read_text(encoding="utf-8")
    assert "auth_enabled" in types and "user_id" in types, "契约字段进 types"
    print("  t6 前端契约：带票/401 清票/SSE 变通/登录页接管/init 顺序/侧栏用户行")


def main():
    t1_auth_off_is_current_behavior()
    t2_auth_on_flow()
    t3_cross_user_isolation()
    t4_project_name_collision_across_users()
    t5_user_scoped_quota()
    t6_frontend_contract()
    print("\ns10_auth: 6/6 全绿（N1 账号边界，auth 双态）")


if __name__ == "__main__":
    main()
