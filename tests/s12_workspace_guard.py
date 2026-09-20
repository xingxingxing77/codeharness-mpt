"""S12 门禁：/workspace 静态挂载守门（S1）。

裸 StaticFiles 暴露面两条规矩，双 auth 态各钉一遍：
  t1 auth 关：会话产物照常 200；storage/ 一律 403（含 ./ 、..、反斜杠、大小写、%73 五种写法——
     这些正是「字符串前缀判据」会漏、而 resolve() 判据不漏的地方）；/api/* 不受牵连。
  t2 auth 开：无票/坏票 401，真票走 query 与 Bearer 两条都 200；storage 仍 403（守门在鉴权之前，
     有票也不给下断点库）。
  t3 前端半边（s8 惯例的源码契约）：workspaceUrl 有票就拼 access_token——<img> 发不了 header。
  t4 B10 半边：`/workspace/file` 文本预览的体积上限（超限 413 且证明没去读；图片元数据分支不受牵连）。

跑法：
  cd /e/Codeharness && PYTHONPATH=/e/Codeharness:/e/Codeharness/logs PYTHONIOENCODING=utf-8 \
    PYTHONDONTWRITEBYTECODE=1 F:/anaconda/python.exe tests/s12_workspace_guard.py
"""
import tempfile
from pathlib import Path

import server.sessions as ss
import server.auth as auth_mod


class _Env:
    """独立会话表 + 开关，收尾恢复（同 s10 的形状，但用户表不动：票直接 issue 不经注册）。"""

    def __init__(self, auth_on: bool):
        self.keep_sess, ss.SESSIONS_FILE = ss.SESSIONS_FILE, Path(tempfile.mkdtemp()) / "sessions.json"
        from codeharness.configs.settings import settings
        self.keep_auth, self.keep_redis = settings.platform.auth_enabled, settings.platform.use_redis
        settings.platform.auth_enabled = auth_on
        settings.platform.use_redis = False

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


def _make_artifact(c, project: str, headers: dict | None = None) -> str:
    """建一个会话并在其工作区放一张真图，返回可访问的相对路径。"""
    s = c.post("/api/sessions", json={"idea": "守门", "project_name": project},
               headers=headers or {}).json()
    wp = Path(s["workspace"])
    wp.mkdir(parents=True, exist_ok=True)
    # 1x1 PNG
    (wp / "px.png").write_bytes(bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
        "890000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"))
    return f"{project}/px.png"


def t1_auth_off():
    with _Env(auth_on=False) as c:
        rel = _make_artifact(c, "s12_off")
        r = c.get(f"/workspace/{rel}")
        assert r.status_code == 200, f"auth 关时合法产物应 200，实际 {r.status_code}"
        assert r.content[:8].startswith(b"\x89PNG"), f"应回真图字节，实际 {r.content[:16]!r}"
        # storage/ 五种写法：断点库从暴露面彻底摘掉，auth 关也挡
        for bad in ("storage/checkpoints.db", "./storage/checkpoints.db",
                    "s12_off/../storage/checkpoints.db", "storage\\checkpoints.db",
                    "STORAGE/checkpoints.db", "%73torage/checkpoints.db"):
            r = c.get(f"/workspace/{bad}")
            assert r.status_code == 403, f"越界写法 {bad!r} 应 403，实际 {r.status_code}"
            assert not r.content.startswith(b"SQLite format"), f"{bad!r} 漏出了断点库内容"
        assert c.get("/workspace/storage").status_code == 403, "storage 目录本身也应 403"
        assert c.get("/api/health").status_code == 200, "守门不得牵连 /api/*"
        assert c.get("/api/sessions").status_code == 200, "守门不得牵连 /api/*"
    print("  t1 auth 关：产物 200 / storage 五种越界写法全 403 / /api/* 不受牵连")


def t2_auth_on():
    with _Env(auth_on=True) as c:
        assert c.get("/api/health").json()["auth_enabled"] is True
        tok = auth_mod.tokens.issue("s12-user")
        rel = _make_artifact(c, "s12_on", headers={"Authorization": f"Bearer {tok}"})
        assert c.get(f"/workspace/{rel}").status_code == 401, "auth 开无票应 401"
        assert c.get(f"/workspace/{rel}?access_token=nope").status_code == 401, "坏票应 401"
        r = c.get(f"/workspace/{rel}?access_token={tok}")
        assert r.status_code == 200, f"带真票应 200，实际 {r.status_code}"
        r = c.get(f"/workspace/{rel}", headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 200, f"Bearer 头等价，实际 {r.status_code}"
        # 守门在鉴权之前：有票也下不走断点库
        assert c.get(f"/workspace/storage/checkpoints.db?access_token={tok}").status_code == 403
        assert c.get("/api/health").status_code == 200, "/api/health 本就在免鉴权面"
    print("  t2 auth 开：无票/坏票 401、query 与 Bearer 两路 200、storage 有票仍 403")


def t3_frontend_token_wired():
    """s8 惯例：机器化钉前端半边——workspaceUrl 有票必须拼 access_token，否则 auth 开必破图。"""
    src = (Path(__file__).resolve().parent.parent / "frontend" / "src" / "stores" / "sessions.ts").read_text(encoding="utf-8")
    body = src.split("workspaceUrl(absPath", 1)[1].split("\n    }", 1)[0]
    assert "getToken()" in body, "workspaceUrl 没取票"
    assert "access_token=" in body, "workspaceUrl 没拼 access_token——auth 开时 <img> 会 401 破图"
    print("  t3 前端 workspaceUrl 无条件拼 access_token")


def t4_file_size_cap():
    """B10：`/workspace/file` 的文本预览有上限——超限 413 且**不去读**；图片分支不受牵连。"""
    import pathlib
    from server.api.workspace import MAX_PREVIEW_BYTES
    print("  t4 文本预览上限（>5MB → 413）...", end=" ", flush=True)
    with _Env(auth_on=False) as c:
        s = c.post("/api/sessions", json={"idea": "大文件", "project_name": "s12_cap"}).json()
        sid = s["id"]
        wp = Path(s["workspace"])
        wp.mkdir(parents=True, exist_ok=True)
        (wp / "ok.md").write_text("# hi\n" * 500, encoding="utf-8")
        (wp / "exact.md").write_bytes(b"y" * MAX_PREVIEW_BYTES)
        (wp / "big.log").write_bytes(b"x" * (MAX_PREVIEW_BYTES + 4096))
        (wp / "big.png").write_bytes(bytes.fromhex("89504e470d0a1a0a") + b"\x00" * (MAX_PREVIEW_BYTES + 4096))

        r = c.get(f"/api/sessions/{sid}/workspace/file", params={"path": str(wp / "ok.md")})
        assert r.status_code == 200 and r.json().get("content", "").startswith("# hi"), \
            f"上限内应照常 200 带 content：{r.status_code} {r.text[:120]}"
        r = c.get(f"/api/sessions/{sid}/workspace/file", params={"path": str(wp / "exact.md")})
        assert r.status_code == 200, f"恰好等于上限应放行（判据是 >，不是 >=）：{r.status_code}"

        reads: list = []
        real_read_text = pathlib.Path.read_text
        pathlib.Path.read_text = lambda self, *a, **kw: (reads.append(self.name),
                                                          real_read_text(self, *a, **kw))[-1]
        try:
            r = c.get(f"/api/sessions/{sid}/workspace/file", params={"path": str(wp / "big.log")})
        finally:
            pathlib.Path.read_text = real_read_text
        assert r.status_code == 413, f"超上限应 413，实际 {r.status_code}: {r.text[:120]}"
        assert not reads, f"413 之前仍然把整个文件读了（等于没护栏）：{reads}"
        assert "MB" in r.text, f"413 该说清大小与上限：{r.text[:160]}"
        # 图片分支只回元数据、不读字节 → 不受上限牵连
        r = c.get(f"/api/sessions/{sid}/workspace/file", params={"path": str(wp / "big.png")})
        assert r.status_code == 200 and "content" not in r.json() and r.json()["mime"] == "image/png", \
            f"图片元数据分支被上限误伤：{r.status_code} {r.text[:120]}"
    print(f"✅ 上限内 200/恰好等于 200；超限 413 且 read_text 调用数=0；{MAX_PREVIEW_BYTES // 1048576}MB+ 的图仍 200（只回元数据）")


def main():
    print("=" * 60)
    print("S12: /workspace 静态挂载守门（S1）")
    print("=" * 60)
    try:
        t1_auth_off()
        t2_auth_on()
        t3_frontend_token_wired()
        t4_file_size_cap()
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
    raise SystemExit(main())
