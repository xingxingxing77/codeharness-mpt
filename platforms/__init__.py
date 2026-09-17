"""S7 平台适配器（包外，施工4 边界纪律：内核零 Redis 依赖，全部注入发生在 server 装配）。

三个接缝（store/bus/chatqueue）各留进程内实现，`settings.platform.use_redis` 双跑；
全绿才删旧路径。同步客户端用于低频调用（会话态每会话几次、插话每轮一次）；
事件流是唯一需要 sync→async 桥的（内核报道槽是普通函数），见 event_store.py。

⚠ 包名偏离施工4 文件清单（platform/ → platforms/）：仓根在 PYTHONPATH 上，
`platform/` 会遮蔽标准库 platform——实测 openai/httpx/qdrant_client 当场炸
`module 'platform' has no attribute 'python_implementation'`。复数形态无此患。
"""
