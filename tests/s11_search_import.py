#!/usr/bin/env python -m asyncio
"""批次2: 仓库导入端点门禁（ImportRepo 从死件接成有唯一调用者）。

SearchEnhancedQA action 已删（与已接线的 tools/search_internet 重叠、且零装配）；联网面回归到
s4_tools 的 `t10-t11b`（search_internet 的 canned 断言）。这里只验 import_repo 端点：经 HTTP
调达、产物落本会话工作区、repo_path 越界拒。真模型通道 manual_search_import.py。

B7 追加两条：t4 auth 开时 repo_path 只许本会话工作区（跨会话目录会把别人的结构扫成自己的图）、
auth 关维持 workspace_root 旧口径；t5 `rglob("*")` 的规模护栏（撞上限 → `truncated`）。
t4/t5 都跑在 `_isolated()` 里：会话表指 tmp 且强制 use_redis=False——否则本机 6379 那个共享
开发 redis 会真收下 s11_a/s11_b 两条垃圾会话（前端列表里就看得见），lifespan 还会 heal_running()。
"""
import contextlib
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi.testclient import TestClient
from server.app import create_app


@contextlib.contextmanager
def _isolated():
    """临时会话表 + 不碰 redis（t4/t5 会自建会话，绝不能落到共享实例上）。"""
    import server.sessions as ss
    from codeharness.configs.settings import settings
    keep = (ss.SESSIONS_FILE, settings.platform.use_redis, settings.platform.auth_enabled)
    ss.SESSIONS_FILE = Path(tempfile.mkdtemp()) / "sessions.json"
    settings.platform.use_redis = False
    try:
        with TestClient(create_app()) as c:
            yield c
    finally:
        ss.SESSIONS_FILE, settings.platform.use_redis, settings.platform.auth_enabled = keep


def t1_import_repo_endpoint():
    """经 POST /{sid}/workspace/import_repo 调达 ImportRepo，产物落会话工作区。"""
    print("t1: import_repo via endpoint...", end=" ", flush=True)
    with TestClient(create_app()) as c:                 # with 才触发 lifespan 填 app.state.store
        s = c.post("/api/sessions", json={"idea": "导入测试", "project_name": "s11_import"}).json()
        sid, workspace = s["id"], s["workspace"]

        wp = Path(workspace)
        wp.mkdir(parents=True, exist_ok=True)
        (wp / "sample.py").write_text("x = 1\n", encoding="utf-8")

        r = c.post(f"/api/sessions/{sid}/workspace/import_repo",
                   json={"repo_path": str(wp), "save_name": "repo", "include_files": True})
        assert r.status_code == 200, f"端点应 200，实际{r.status_code}: {r.text[:200]}"
        body = r.json()
        assert body.get("node_count", 0) >= 1, f"应有节点，实际{body}"
        assert body.get("saved_json") and Path(body["saved_json"]).exists(), f"JSON 未落盘: {body}"
        assert body.get("saved_mmd") and Path(body["saved_mmd"]).exists(), f"Mermaid 未落盘: {body}"
    print("✅")


def t2_import_repo_path_guard():
    """repo_path 越界（workspace_root 外）必须 400——防任意目录读取。"""
    print("t2: import_repo path guard...", end=" ", flush=True)
    with TestClient(create_app()) as c:
        s = c.post("/api/sessions", json={"idea": "越界", "project_name": "s11_guard"}).json()
        sid = s["id"]
        for bad in ("C:\\Windows", "E:\\MetaGPT", "../../etc"):
            r = c.post(f"/api/sessions/{sid}/workspace/import_repo", json={"repo_path": bad})
            assert r.status_code == 400, f"越界 repo_path={bad!r} 应 400，实际{r.status_code}"
    print("✅")


def t3_import_repo_save_name_guard():
    """save_name 越界（S2）：带路径分隔必须 400 且服务端无新文件；带空格的合法名仍 200。"""
    print("t3: import_repo save_name guard...", end=" ", flush=True)
    with TestClient(create_app()) as c:
        s = c.post("/api/sessions", json={"idea": "穿越", "project_name": "s11_traversal"}).json()
        sid, workspace = s["id"], s["workspace"]
        wp = Path(workspace)
        wp.mkdir(parents=True, exist_ok=True)
        (wp / "sample.py").write_text("x = 1\n", encoding="utf-8")

        bad_names = ("../../server/data/users", "../../server/data/x", "/tmp/x", "a/b", "  ")
        # 越界路径上的文件既不许多建也不许多改（users.json 是现成的靶，只比 exists() 抓不到覆写）
        targets = [(wp / f"{n}{ext}").resolve() for n in bad_names for ext in (".json", ".mmd")]
        before = {t: (t.exists(), t.read_bytes() if t.exists() else None) for t in targets}

        for bad in bad_names:
            r = c.post(f"/api/sessions/{sid}/workspace/import_repo",
                       json={"repo_path": str(wp), "save_name": bad})
            assert r.status_code == 400, f"越界 save_name={bad!r} 应 400，实际{r.status_code}: {r.text[:200]}"
        for t in targets:
            now = (t.exists(), t.read_bytes() if t.exists() else None)
            assert now == before[t], f"越界产物已落盘/覆写：{t}"

        r = c.post(f"/api/sessions/{sid}/workspace/import_repo",
                   json={"repo_path": str(wp), "save_name": " repo ", "include_files": True})
        assert r.status_code == 200, f"合法 save_name（含空格）应 200，实际{r.status_code}: {r.text[:200]}"
        assert Path(r.json()["saved_json"]).name == "repo.json", f"应落 repo.json: {r.json()}"
    print("✅")


def t4_import_repo_cross_session_guard():
    """B7：auth 开 → 只许本会话工作区；auth 关 → 维持 workspace_root 旧口径。"""
    print("t4: import_repo 跨会话边界（auth 开/关）...", end=" ", flush=True)
    from codeharness.configs.settings import settings
    from server.auth import current_user
    with _isolated() as c:
        store = c.app.state.store
        assert type(store).__name__ == "SessionStore", f"没隔离干净，store={type(store).__name__}"
        a = store.create(idea="a", project_name="s11_alice_a", user_id="alice")
        b = store.create(idea="b", project_name="s11_alice_b", user_id="alice")
        for s in (a, b):
            p = Path(s.workspace) / "keep.py"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x = 1\n", encoding="utf-8")
        # 对照：旧口径（只判到 workspace_root）对同一个 b 目录是放行的。没有这句，
        # 那条 400 可能来自别的判据（比如目录不存在），证不了「新边界挡住了跨会话」。
        from codeharness.runtime import session_root
        ws_root = Path(session_root()).resolve().parent
        assert Path(b.workspace).resolve().is_relative_to(ws_root), \
            "对照组不成立：b 目录本来就不在 workspace_root 内，旧口径也会拒"
        c.app.dependency_overrides[current_user] = lambda: "alice"
        settings.platform.auth_enabled = True
        try:
            r = c.post(f"/api/sessions/{a.id}/workspace/import_repo", json={"repo_path": str(Path(b.workspace))})
            assert r.status_code == 400, f"auth 开、导入别人会话目录应 400，实际{r.status_code}: {r.text[:200]}"
            assert "本会话" in r.text, f"400 该说明边界是本会话工作区（旧口径只判到 workspace_root）：{r.text[:200]}"
            r = c.post(f"/api/sessions/{a.id}/workspace/import_repo", json={"repo_path": str(Path(a.workspace))})
            assert r.status_code == 200, f"auth 开、导入自己会话应 200，实际{r.status_code}: {r.text[:200]}"
            # B11 存在性 oracle：边界判定必须在**存在性判定之前**。原先先判 `is_dir()`、后判边界，
            # 两句 400 文案不同 ⇒ 已登录用户拿绝对路径就能枚举宿主上哪些目录存在
            # （「必须是已存在的目录」= 存在，「必须在…内」= 不存在）。判据：边界外的**存在**目录
            # 与**不存在**路径必须得到同一句话——两句不同就是 oracle 又开了。
            outside_exists = c.post(f"/api/sessions/{a.id}/workspace/import_repo",
                                    json={"repo_path": str(Path(a.workspace).parent)})   # 存在、但在边界外
            outside_missing = c.post(f"/api/sessions/{a.id}/workspace/import_repo",
                                     json={"repo_path": "C:\\s11-no-such-dir-xyz"})       # 不存在、也在边界外
            assert outside_exists.status_code == 400 and outside_missing.status_code == 400, \
                f"边界外应一律 400：存在={outside_exists.status_code} 不存在={outside_missing.status_code}"
            assert outside_exists.json()["detail"] == outside_missing.json()["detail"], \
                (f"B11 存在性 oracle 又开了：边界外的『存在』与『不存在』答的不是同一句话 —— "
                 f"{outside_exists.json()['detail']!r} vs {outside_missing.json()['detail']!r}")
            assert "本会话" in outside_exists.json()["detail"], \
                f"边界外那句该说边界，而不是别的：{outside_exists.json()['detail']!r}"
        finally:
            c.app.dependency_overrides.clear()
            settings.platform.auth_enabled = False       # 反证：auth 关时同一跨目录请求回到旧口径、仍许
        r = c.post(f"/api/sessions/{a.id}/workspace/import_repo",
                   json={"repo_path": str(Path(b.workspace)), "save_name": "from_b"})
        assert r.status_code == 200, f"auth 关跨会话应仍 200（公共模板目录的合法用法不破），实际{r.status_code}"
    print("✅ auth 开跨会话 400（文案含「本会话」）+ 本会话 200；边界外「存在」与「不存在」两句 400 文案相同"
          "（B11 存在性 oracle 已关）；auth 关同一请求 200")


def t5_import_repo_scale_guard():
    """B7：`rglob("*")` 的规模护栏——撞上限要在返回里标 truncated，未撞上限行为不变。"""
    import codeharness.actions.import_repo as IM
    print("t5: import_repo 规模护栏...", end=" ", flush=True)
    with _isolated() as c:
        s = c.post("/api/sessions", json={"idea": "巨型目录", "project_name": "s11_big"}).json()
        sid, wp = s["id"], Path(s["workspace"])
        wp.mkdir(parents=True, exist_ok=True)
        for i in range(12):
            (wp / f"f{i}.py").write_text("x = 1\n", encoding="utf-8")
        keep = IM.MAX_IMPORT_NODES
        IM.MAX_IMPORT_NODES = 5
        try:
            r = c.post(f"/api/sessions/{sid}/workspace/import_repo",
                       json={"repo_path": str(wp), "save_name": "capped"})
        finally:
            IM.MAX_IMPORT_NODES = keep
        assert r.status_code == 200, f"撞上限不是错误，应 200 带标记，实际{r.status_code}: {r.text[:200]}"
        body = r.json()
        assert body.get("truncated") is True, f"撞上限却没标 truncated：{body}"
        assert 5 < body["node_count"] < 13, f"节点数没被护栏挡住（12 文件+根）：{body['node_count']}"
        r2 = c.post(f"/api/sessions/{sid}/workspace/import_repo",
                    json={"repo_path": str(wp), "save_name": "full"})
        assert r2.status_code == 200 and r2.json().get("truncated") is False, \
            f"未撞上限也标了截断：{r2.json()}"
        assert r2.json()["node_count"] > body["node_count"], "完整扫描的节点数应多于截断后的"
    print(f"✅ 上限 5 → truncated=true 且 node_count={body['node_count']}（<13）；恢复上限后 truncated=false")


def main():
    print("=" * 60)
    print("批次2: 仓库导入端点门禁")
    print("=" * 60)
    try:
        t1_import_repo_endpoint()
        t2_import_repo_path_guard()
        t3_import_repo_save_name_guard()
        t4_import_repo_cross_session_guard()
        t5_import_repo_scale_guard()
        print("\n" + "=" * 60 + "\n✅ 全部通过 (5/5)\n" + "=" * 60)
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
