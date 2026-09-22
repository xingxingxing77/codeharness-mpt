## Langfuse 数据体检报告（11 个会话，全部观测拉取完毕）
数据面 ：今天下午你跑的 6 场真会话（S9 曲线对照 classic/dynamic/react × todo.py/tinycli + 2 场打油诗 demo）+ 我的两场验证 + 3 场门禁 FakeLLM 线，全部进了 Langfuse。价目匹配 100% （自建 qwen3.8-flash 定义生效），会话归组/停止/完成状态全对。以下是从 trace 里挖出的问题。

## 一、出错的地方（数据实证）
1. 记忆召回无熔断——每轮必炸一次（ERROR 最大簇，~30 条）`memory.recall` 连续报`APIConnectionError` （90d816e8 十几条、92bc51ac、f5eed1cb 同样）。根因：ollama(11434)/qdrant(6333) 未启动，RoleZero 每轮 think 照打 embedding 调用。上层降级兜住了（dynamic 会话照样 finished），但 每轮白付一次连接超时 ，且 ERROR 噪音淹没真问题。
→ 待完善：embedding/qdrant 探活缓存 +「本轮已失败就不再试」的会话级熔断。

2. RoleZero 工具实参幻觉（dynamic 线功能性缺陷） TOOL ERROR 至少 6 笔，全是模型没按`@tool` schema 传参：

- `write_file` 收到`file_path` （应`path` ）×2 场
- `search_file` 缺`search_term`
- `edit_file_by_replace` 三种错法：行号与内容不匹配 / 缺`new_content` / 缺 4 个字段
→ 待完善：工具 schema 在 prompt 里逐字段强化（few-shot），或在工具入口做参数别名归一。

3. 错误消息没进 span——观测面最大盲区（高优先） 906d56b5（classic 对照线，被你手动 stop 的那场）有 37 条 ERROR，statusMessage 全部为空、output=None （Engineer×11、act×11、LangGraph×12…）。LangChain 的`on_chain_error/on_chat_model_error` 异常文本没落到 Langfuse 的`statusMessage` 字段——恰恰是排障最需要的。该场 18 分钟里 Engineer 环节反复失败，只能靠猜。
→ 待完善：handler 侧把 error 事件的异常文本写入 span（`record_exception` 或 statusMessage 透传）。

4. 1 笔 generation 漏 usage （906d56b5 @ 06:38:40）——与已知的「max_token 截断那次也花了钱但拿不到用量」同口径，低频但记账缺口。

5. 门禁 FakeLLM 会话污染生产观测 ：f484cedd 等 3 场 0 LLM、16 条链 1 秒跑完（不在 redis = 测试进程线）。测试数据和真会话混在同一个 Langfuse project。
→ 待完善：tests 里强制`LANGFUSE__ENABLED=0` ，或打`environment=test` 标签区分。

## 二、未完善但不算错（口径与覆盖面）
6. reasoning token 是成本大头、两条记账线口径不同 ：906d56b5 reasoning=44.4万 vs 可见 output=4.5万（ 87% 花在思考上 ）；Langfuse 单列 reasoning，CostManager 合流。S9 双跑对照表取数前必须钉死口径，否则“token 下降”判据失真。

7. >180s 空洞每场都有 （220s/262s/296s，与最大 LLM 延迟重合）——长思考不是故障，但前端 4s 轮询期间观感是“卡死”；现在可以靠 Langfuse 定位到具体 generation，N4 面板接上后可在产品内解释。

8. classic 线没有 TOOL/RETRIEVER span （BY_ORDER Agent 无工具循环）——符合设计，但意味着经典线的可观测面只有链 + LLM 两层。

## 三、建议动手顺序
序 事项 收益 1 修 #3 错误消息透传（observability 层小改） 37 类空错误从此可读，排障不再靠猜 2 修 #2 工具参数归一/强化（dynamic 线） 直接减少 dynamic 线失败率 3 修 #1 recall 熔断 去噪 + 每轮省一次超时 4 #5 测试隔离 + #6 口径钉死 S9 对照数据可信的前提