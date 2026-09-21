# 2026-09-21 17:2x · D 项：`users.json` 的写盘在 Windows 上会互踩（两条形态都实测到了）

治理 §3 余账第 5 条，登记时只有一句话：「`_save_users` 的临时文件名是写死的 `users.tmp`，
两笔并发 save 在 Windows 上 `replace` 直接撞 WinError 32……register 现在是 users.json 唯一写入方
且在锁内，所以不破；**若别处再开一个 users.json 写口就会复现**」。
本轮把它从"登记"做成"实测 + 收紧"，并且**多测出一条登记时没看到的形态**。

## 段 1 · 现象（三种，最后一种最糟）

`server/auth.py::_save_users` 原样：

```python
tmp = USERS_FILE.with_suffix(".tmp")          # 固定名 users.tmp
tmp.write_text(json.dumps(users, ...), encoding="utf-8")
tmp.replace(USERS_FILE)
```

1. **rename 撞车**：两笔写共用同一支 scratch，先 rename 的一方把源文件搬走 → 另一方的
   `replace` 砸在没了的路径上。
2. **写者与读者撞车**：`verify()`（登录必经）读 `users.json` 不持锁，与注册写盘同时发生 →
   Windows 上 rename 在飞的那几毫秒读目标文件回 `PermissionError`。
   **这一条今天就能发生，不需要"再开一个写口"** —— 治理登记时把它当成前提未破的假设，实测推翻了。
3. **表被写坏（最糟）**：`write_text` 内部是 `open('w')→write→close`。两笔同名写各自 truncate、
   各自从 0 覆写，短的那笔盖不掉长的那笔的尾巴 → 落盘是**半新半旧的 JSON**，
   `_load_users` 从此一调就抛，注册与登录全站 500。形态 1 至少是异常（可重试、可诊断），
   形态 3 是**静默的数据损坏**。

## 段 2 · 排查过程（含一次我自己的错判）

1. 第一次取证用 2 线程 + `threading.Barrier` 强制两笔都写完 scratch 再一起去 replace。读数：
   **两笔都成功、零报错**（`errs=[]`、`leftover tmp=[]`）。我据此一度写了一条
   「对照组必须报错」的断言 → **当场红在反方向**：判据的对照组不成立。
   → 结论：**"两笔同名 scratch 一起 rename"在这台机器上不是稳定失效**，
   拿 WinError 当唯一判据会做出一条空转/抖动的门禁。（这条错判照 §约定 4 记下来。）
2. 换判据思路：不再盯 rename，去盯 `write_text` 的**内部步骤**。手工按
   `open('w') / open('w') / write(长) + flush / write(短) + flush / close / close` 交错同一支文件 →
   文件长度等于长内容、前缀等于短内容，`json.loads` 抛 `JSONDecodeError`。
   形态 3 确认，且**确定性可复现**（不依赖调度）。
3. 再看真实并发：把写侧放大到 4 线程 × 8 轮（混 4 种长度的表）+ 2 个读者狂读 →
   旧写法实测 **18 次 `PermissionError`，其中打出来的就是
   `WinError 32 另一个程序正在使用此文件`** —— 治理登记的那句话方向没错，只是需要
   足够的并发量才显形（2 线程不够）。新写法（一次性名）在同一载体上只剩读者侧的
   `Errno 13 Permission denied` 一类，于是补上读侧重试。
4. 判「改哪侧」：读侧不重试 = 登录偶发 500；写侧不重试 = 注册偶发 500；scratch 不同名 =
   形态 3 消失。**三处都得动**，但每处都是一行判据，不是重构。

## 段 3 · 根因

一句话：**把「临时文件」当成全局单例**。原子写的约定是"每笔写有自己的 scratch，rename 进目标"；
名字写死等于所有写者共享一支 scratch，于是原子写退化成互相覆写的普通写。
外加 Windows 的 rename 语义（目标被占用即失败、且不带 POSIX 的"改名后旧句柄仍有效"），
读者与写者之间也缺一次有界的等待。

## 段 4 · 解决方案

1. `server/auth.py::_save_users()`：scratch 改一次性名 `users.<token_hex(8)>.tmp`；
   `tmp.replace()` 外面套 4 档退避（20/50/100/200ms），**最后一档不吞异常**——真进不去就照实抛，
   此时 scratch 还在盘上（名字唯一），账号没丢，是可查的失败而不是静默成功。
2. `server/auth.py::_load_users()`：读表对 `PermissionError` 重试三档（共 ~170ms），
   仍失败就抛。注释写明**为什么不返回空表**：空表 = "所有人都不存在"，比 500 更坏。
3. 判据 `tests/s20_auth_race_ratelimit.py::t1b`（新增，S20 从 2 组到 **3 组**）四格：
   ① 前提格（确定性）：手工交错 `write_text` 的步骤 → 必须写出坏 JSON 且 `json.loads` 抛；
   ② 生产路径：4 写者 + 2 读者同时跑真 `_save_users`/`_load_users` → 零报错、表可解析、无残留 `.tmp`；
   ③ 唯一性格：拦 `secrets.token_hex` 计数，两笔 save 必须各起一个不同名字
   （退回固定名时这一格计数为 0，当场红）；
   ④ **对照组**：修复前本体（固定名 + 单次 rename）跑同一个 hammer → 必须至少一次失败，
   实测 `WinError 32`×18。没有 ④，② 的"零报错"说明不了任何事。
4. 账面：`docs/前端/接口契约.md` §5.1 的 `users.json` 那行按新实现改准（读 `:35`、写 `:52`，
   并说明原子 rename 防的是撕裂写、**不防 lost update**），下面补一段"同族单机侧"，
   写清形态 2 今天就能发生、以及它**不改变**跨进程那条边界。

## 段 5 · 解决后效果（读数）

- `tests/s20_auth_race_ratelimit.py` **3/3 exit 0，连跑 3 次不抖**：
  `log/fixD_s20_run1.out` / `run2` / `run3`。t1b 打印：
  `①同名 scratch 手工交错实测写出坏 JSON；②4 写者+2 读者并发零报错、表可解析、无残留 .tmp；
  ③两笔 save 各起一次性名字（2 次全不同）；④对照组实测 PermissionError: [WinError 32] …（共 18 次）`
- 门禁按 §0.3（碰 `server/**`）：`s7_platform` **13/13 双配置**、`s8_frontend_contract` **12/12**；
  另跑 `s10_auth` **6/6**（auth 双态；这份文件在工作树里是别人在途的改动，**我只跑不改不提交**）。
  原始输出 `log/fixD_s7_platform.out`、`log/fixD_s8_frontend_contract.out`、`log/fixD_s10_auth.out`。
- 判据打在**真文件系统的 rename/read** 上（形态 1/2）与**真 `write_text` 步骤交错**上（形态 3），
  不是打在构造参数上。
- **我自己的错判照实记**：段 2 第 1 步那条「2 线程 + barrier 就能复现 rename 撞车」是错的，
  对照组不成立时我一度把它当判据；换成 4 线程 × 8 轮 + 混长度才稳定复现 `WinError 32`。
  教训：**反证类判据要先测出对照组的稳定失效率**，两线程跑一次没炸不等于不会炸。
- 测试自己也红过一次：② 的「无残留 .tmp」被 ① 的道具文件 `users.tmp` 绊倒（已 `unlink`）。
  这条断言因此顺带证明了自己不是摆设。
- **未验边界**：多进程（`--workers 2`）下的 lost update 没变、也不归本项——那是 §5.1 的既定边界，
  升级路径是换共享存储。真库并发压测（QPS 级）没做，本机只到 4 线程 × 8 轮这个量级。
