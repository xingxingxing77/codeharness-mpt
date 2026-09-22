# 贪吃蛇会话验收报告（session 0e43be90）
## 结论：管线机制跑通了，但产物跑题——贪吃蛇没有做出来
## 运行数据（机制面：全绿）
指标 值 状态 finished（约 35 分钟） 花费 ¥1.16（84k 输入 + 40.4 万输出 token） LLM 调用 42 笔，延迟中位 158.6s / 最大 288.3s Langfuse 298 条 observations（256 CHAIN + 42 GENERATION），0 ERROR

顺带：断线缓冲在真环境验证成功 ——开跑时 Langfuse 是停的，5+ 批 span 落盘；你中途起 docker 后 20:41 自动补投完成 ，缓冲清空、数据全数入库。这正是上一轮设计的完整实战验证。

## 产物 vs 需求（业务面：失败）
需求是“单文件 index.html 贪吃蛇”，实际产出是一套 与此无关的 Python 样板工程 （main.py 信号处理样板 + config.py + utils.py），且：

- `main.py` 首行带着 没剥掉的````python` 围栏 ；
- `config.py` 、`test_main.py` 是 0 字节空文件 ；
- QA 测试照常“通过”——测的是错误产物。
## 根因链（Langfuse 实参铁证）
1. PRD 阶段（PM）完美 ：prd.md 准确承载了贪吃蛇全部需求（单文件、方向键、+10 分、空格重开），质量很高。
2. WriteTasks 首次调用被 max_token=16384 截断 （thinking 模型 reasoning 也占预算，老问题）→ 结构化解析失败。
3. 自愈重试丢上下文（主因， agent.py:128-130 ） ：`_act` 把错误回喂记忆后下一轮重试，但 inbox 已空，`prompt = 最近一条记忆` ——Langfuse 里这笔调用的输入 只有 1025 字：system 提示 + 那条报错文本 ，不含“贪吃蛇”/“index.html”/PRD 任何内容。模型只能幻觉出一份通用的"main.py+config.py+utils.py"任务清单。
4. Engineer 忠实执行了错误清单，QA 测了错误的产物，全程无人对照 PRD 验收 → “成功”交付。
## 待修清单（按你指令）
1. 自愈重试丢上下文 （根因）：BY_ORDER 第 2+ 动作重试时 prompt 应带上上游真产物（PRD），而不是只带错误消息；
2. WriteCode 落盘前剥残余围栏 + 拦截 0 字节文件；
3. （可选）QA 增加一道“对照 PRD 需求池逐条核对产物”的闸。