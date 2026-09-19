#!/usr/bin/env python -m asyncio
"""批次2: 仓库导入端点门禁（ImportRepo 从死件接成有唯一调用者）。

SearchEnhancedQA action 已删（与已接线的 tools/search_internet 重叠、且零装配）；联网面回归到
s4_tools 的 `t10-t11b`（search_internet 的 canned 断言）。这里只验 import_repo 端点：经 HTTP
调达、产物落本会话工作区、repo_path 越界拒。真模型通道 manual_search_import.py。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi.testclient import TestClient
from server.app import create_app


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


def main():
    print("=" * 60)
    print("批次2: 仓库导入端点门禁")
    print("=" * 60)
    try:
        t1_import_repo_endpoint()
        t2_import_repo_path_guard()
        print("\n" + "=" * 60 + "\n✅ 全部通过 (2/2)\n" + "=" * 60)
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
