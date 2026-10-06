"""C181：exp_pool → Langfuse dataset 的导出口（评估闭环的数据半边；manual 通道，零 LLM 花费）。

回答的问题：prompt 改动前后要拿同一批真实 req→resp 对做回归对比，数据从哪来——
经验池的 Qdrant 切片（`doc_type="exp"`）里已经存着 (user, project, action_tag, req)→output
的积累，Langfuse 侧却一个 dataset 都没有（`create_dataset`/`dataset_items` 全仓零命中）。
本工装是两者之间唯一的桥：scroll 全量经验点，灌成 Langfuse dataset item。

幂等：Langfuse item id 直接复用经验点 id（`exp_point_id` 的 uuid5，同 (user,project,tag,req)
恒等）——重复导出是 upsert 覆盖不是堆积，不会把 dataset 越跑越大。
形状：input=req 原文、expected_output=当时存下的答案、metadata 带 action_tag/quality_score/
user_id/project——切片比对在 Langfuse 界面上按 metadata 筛，这里不另开 per-租户 dataset。

零花费的边界（照实写）：scroll 不过 embeddings（不建 ExpStore、不碰 LLMGateway），
Langfuse 是本机自托管容器——本工装全程不发云端请求；把它跑成 experiment（拿 dataset
对比两版 prompt 的产出）才是花钱动作，按 §4 另问另拍。

跑法：
  自检（零服务、零容器、常跑）：
    cd /e/Codeharness && PYTHONPATH=/e/Codeharness PYTHONIOENCODING=utf-8 \
      F:/anaconda/python.exe -B tests/manual_export_langfuse_dataset.py
  live 导出（先 `docker start codeharness-qdrant` + langfuse 六容器，跑完 stop 回）：
    同上 + --live   （仍钉 REDIS__DB=15 / NO_PROXY，同 §5 姿势；LANGFUSE__ENABLED 由本脚本自带）
"""
import asyncio
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DATASET = "exp-pool-export"


def to_item(pid: str, payload: dict) -> dict:
    """Qdrant exp 点 → Langfuse dataset item。唯一 shaping 出口：自检与 live 都走它。

    字段缺省照 `exp_store.save` 的实际写入形状（output/score 必在，但老点/手补点可能缺）——
    取不到的给空值并继续，一条脏点不许把整趟导出陪葬（同「不许假 0」口径：缺就是缺，
    留在 metadata 里可见，不拿占位值顶）。"""
    return {
        "id": pid,
        "input": payload.get("text") or "",
        "expected_output": payload.get("output") or "",
        "metadata": {
            "action_tag": payload.get("action_tag") or "",
            "quality_score": payload.get("score", 0.0),
            "user_id": payload.get("user_id") or "",
            "project": payload.get("project") or "",
        },
    }


def self_check() -> None:
    full = to_item("11111111-1111-5111-8111-111111111111", {
        "text": "合并当前目录的 txt", "output": "已写入 merged.txt", "score": 0.97,
        "action_tag": "write_code", "user_id": "alice", "project": "demo"})
    assert full["id"] == "11111111-1111-5111-8111-111111111111"
    assert full["input"] == "合并当前目录的 txt" and full["expected_output"] == "已写入 merged.txt"
    assert full["metadata"] == {"action_tag": "write_code", "quality_score": 0.97,
                                "user_id": "alice", "project": "demo"}
    # 老点缺字段：给空值不炸；quality_score 缺省 0.0 是 save 侧的真实缺省（没打过分）
    bare = to_item(str(uuid.uuid5(uuid.NAMESPACE_OID, "exp:x")), {"text": "q"})
    assert bare["input"] == "q" and bare["expected_output"] == ""
    assert bare["metadata"]["quality_score"] == 0.0 and bare["metadata"]["action_tag"] == ""
    # id 必须是合法 uuid（Langfuse item id 的硬要求；经验点 id 本就是 uuid5，这里钉住这层假设）
    uuid.UUID(full["id"]) and uuid.UUID(bare["id"])
    print("self_check: 4 格全绿（形状 / 缺省 / 幂等 id / uuid 合法）")


async def _scroll_exp():
    """全量 scroll `doc_type="exp"`（含所有 user/project——切片靠 metadata，不在导出口收窄）。"""
    from qdrant_client import models as m
    from codeharness.configs.settings import settings
    from codeharness.document_store.qdrant_store import QdrantStore

    store = QdrantStore()
    flt = m.Filter(must=[m.FieldCondition(key="doc_type", match=m.MatchValue(value="exp"))])
    offset, total = None, 0
    while True:
        points, offset = await store.client.scroll(
            store.collection, scroll_filter=flt, limit=256, offset=offset, with_payload=True)
        for p in points:
            yield p.id, p.payload or {}
            total += 1
        if offset is None:
            break
    if total == 0:
        print("live: 集合里没有 exp 点（新池/没开过 dynamic 写入）——0 item，不是故障")


def live() -> None:
    import os
    # 客户端显式构造走 observability（C81 噪音滤网 / B⑥ 日志桥 / 双 key 校验都在里面）；
    # 本工装必须真连，不像门禁那样默认关——这里在 settings 导入前把开关顶开（.env 没开也能跑）。
    os.environ.setdefault("LANGFUSE__ENABLED", "true")
    from codeharness import observability
    if not observability.enabled():
        raise SystemExit("live: Langfuse 没起来（SDK 缺 / LANGFUSE__ENABLED=0 / 双 key 缺一）——"
                         "先 docker start langfuse 六容器，并确认 .env 里 LANGFUSE__PUBLIC_KEY/SECRET_KEY")
    lf = observability.client()
    try:
        lf.get_dataset(DATASET)
        print(f"live: dataset `{DATASET}` 已存在，走 item upsert")
    except Exception:
        lf.create_dataset(name=DATASET, description="exp_pool 导出（C181）：req→resp 经验对，"
                                                    "metadata 带 action_tag/quality_score/user_id/project")
        print(f"live: dataset `{DATASET}` 已创建")

    n, tags = 0, set()
    async def run():
        nonlocal n
        async for pid, payload in _scroll_exp():
            item = to_item(pid, payload)
            lf.create_dataset_item(dataset_name=DATASET, id=item["id"], input=item["input"],
                                   expected_output=item["expected_output"], metadata=item["metadata"])
            tags.add(item["metadata"]["action_tag"])
            n += 1
            if n % 50 == 0:
                print(f"live: 已 upsert {n} …")
    asyncio.run(run())
    observability.flush()
    observability.shutdown()
    print(f"live: 完成——{n} 个经验点 → dataset `{DATASET}`（action_tag {len(tags)} 种：{sorted(tags)}）；"
          "复跑是 upsert，不会重复堆积")


if __name__ == "__main__":
    self_check()
    if "--live" in sys.argv:
        live()
