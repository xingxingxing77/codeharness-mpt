# `log/error/` 目录约定

规则：**开发过程中出现但没有解决的问题，一律记录在本目录，写成 md 文档，必须带时间与出现在哪个开发阶段。**
（口径搬自 `E:\java-agent\mall-dev\error\`。）

- 文件名：`YYYY-MM-DD-<阶段号>-<一句话主题>.md`，阶段号用 `PLAN.md` 的项编号（`A1`/`B3`/`C5`/`S2`…）。
- 每份固定四段：**现象** / **已排除的原因（含实测命令与输出）** / **当前最可能的怀疑** / **需要谁做什么才能继续**。
- 「已排除的原因」是本目录的重点：只有把查过并否掉的路写下来，下一次（可能是十几个小时后的另一个
  全新会话）才不必重复排查。写「试过 X 不行」不算，要给命令与真实输出摘要。
- 问题解决后**不删文件**，在文末追加「✅ 已解决：时间、提交号、根因、修法」，保留完整脉络。
- 与 `log/` 的分工：`log/` 记一次开发会话的全过程（含已解决的），本目录只记**卡住没解决**的。

## 本仓已经长期成立的「不算 bug 的 bug」清单

写新文档前先对照，命中就别往代码上找原因：

| 症状 | 真因 |
|---|---|
| 跑 `tests/*.py` 像是挂死不动 | `PYTHONPATH` 少了 `/e/Codeharness/logs` → 撞本机 WMI 永久卡死 |
| 跑门禁偶发 `access violation` / 段错误 | 本机 anaconda 写 `.pyc` 的问题，加 `PYTHONDONTWRITEBYTECODE=1` 或 `-B` |
| 脚本打印 ✅ 就 `UnicodeEncodeError` 退 1 | Windows GBK 控制台，缺 `PYTHONIOENCODING=utf-8` |
| 5173 上测出一堆异常 | 那是**另一个项目**的 Vite；本仓产物在 `http://127.0.0.1:8718/?v=<新串>` |
| `npm run build` exit 0 但页面是坏的 | vite 会吞模板编译错误，必须 grep 输出里的 `error` |
| 合成 `.click()` 点了没反应 | composer 卡内控件是 `@mousedown.prevent`，要 `dispatchEvent(new MouseEvent('mousedown',{bubbles:true}))` |
| 门禁 `exit 2` 且**一条 ✅ 都没有**、`.out` 首行是 `can't open file 'env'` | 写成了 `python -B env FOO=1 x.py`——`env` 被当成脚本名。环境变量要放在**解释器之前** |
| `uvicorn` 起在 8719 报 `bind … [winerror 10013]` | 不是 Windows 排除段（`netsh interface ipv4 show excludedportrange` 查不到它），是**别的进程在听**；`netstat -ano \| grep 8719` 现查 PID 再换端口 |
| `evaluate_script` 返回对象时少几个键（数值/字符串都可能丢） | MCP 桥的序列化行为，**原因未查**。判据一律在页内拼成**一个字符串**再返回，别指望对象键齐全 |
| 页内 `elementFromPoint(滚动区左边缘+8, …)` 永远命中不到行 | 正文列有 **32px 左内边距**，+8px 落在 padding 上（`useFollowScroll.captureAnchor` 就因此恒回 null，见 `log/2026-09-21-B2…` 段 4）。打点取水平中心 |
| HoverCard 点了卡但「复制成功」不出现 | **不是代码问题**：`doCopy` 刻意只在 `clipboard.writeText` resolve 后才置回执。内置面板 `clipboard-write=denied`；无头 Chrome 显示 `granted` 但 `writeText` 仍抛 `NotAllowedError`（headless 没有剪贴板后端）。要验就起**真窗口 Chrome + `Browser.grantPermissions` + `Input.dispatchMouseEvent`**，见 `log/2026-09-21-A3复制回执可信点击.md` |
| ~~真模型验收全跑不了~~（**已推翻**） | 2026-09-21 06:14 实测本机 ollama 推理可用（`/api/generate` 真回内容，模型 `qcwind/qwen2.5-7B-instruct-Q4_K_M`）→ 旧「CUDA 坏」那条是 serve 没起造成的误判。**边界仍在**：7B 能否产出合法 tool call 未验（A4 用户已批「先用本机 7B 试一轮」）；云端额度只剩约 3 元，花钱前必须先问 |
| 自动化作业「没报错但再也不跑了」 | `qoder_cron` action=update 的 patch 不带 `expiresAt` → **被静默重置成约 10 小时默认**（2026-09-20 23:50 实测：09-25 变 09-21 09:50）。改配置一律 `schedule` + `expiresAt` 同带，并在返回里核对 `nextRunAt`/`expiresAt`；治理侧 23:32 那起「擅自改 expiresAt」是同一陷阱 |

## 索引

| 文件 | 阶段 | 状态 |
|---|---|---|
| （暂无） | | |
