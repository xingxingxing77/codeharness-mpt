# Codeharness 开发日志目录（`log/`）

约定照 `E:\java-agent\mall-dev\logs\` 的口径搬过来，逐条适配本仓。每次阶段开发都要遵守。

> ⚠ 别和 `logs/` 搞混：`logs/` 是 loguru 的**运行时输出**（`20260920.txt` 那种），而且
> `logs/sitecustomize.py` 是门禁跑法的依赖项——跑任何 `tests/s*.py` 都必须带
> `PYTHONPATH=/e/Codeharness:/e/Codeharness/logs`，少了这一段会撞上本机 WMI 永久卡死。
> **`logs/` 一个字都不要动、不要改名、不要合并进来。** 本目录只放人写的开发记录。

## 五条硬约定

1. **文件名**：`YYYY-MM-DD-<主题>.md`，一个阶段性任务一份。时间用 24 小时制带时区（本机 +08:00）。
   时刻一律用 `date` 取真实值；推断出来的写「约」。**（2026-09-21 C9 轮的实测教训：上一轮 handoff/队列
   里的时刻没按 `date` 取、超前真钟 15–25 分钟，直接把下一棒的窗口预算算错一半——「只剩 45 分钟」其实是
   113 分钟。交接里的每个时刻都要现取，别沿用记忆或推算。）**
2. **每条记录五段**：**现象 → 排查过程（含实际命令与输出证据）→ 根因 → 解决方案 → 解决后效果**，
   末尾标相关 commit 号与门禁读数。
3. 必须区分三种结论：**已修并实测** / **已修未实测**（写清为什么验不了）/ **不是代码问题**
   （环境或工具造成的假异常）。不允许把「看起来好了」写成「已解决」。
   本仓的具体形态：`FakeLLM` 门禁绿 ≠ 真模型行为对（README §陷阱 #2 已经应验三次），
   每条「效果」都要写明断言打在**回放**上还是打在**构造参数**上。
4. **自己引入的问题照实记**：笔误、凭印象写不存在的 API、误归因、把逻辑推断当成实测读数——
   都要写，不粉饰。这一节是本目录对下一轮会话最有用的部分。
5. 与 `log/error/` 的分工：本目录记一次开发的**全过程**（含已解决的）；
   `log/error/` 只记**卡住没解决**的，见该目录 README。

## 索引

| 文件 | 覆盖范围 |
|---|---|
| `2026-09-21-B1断线横幅与轮内error行.md` | B1 三格里两格 ✅、一格证伪后改档。**横幅**：源值逐条取自参照系 `ConnectionBanner.module.css`，第一版我从 `evtSource.readyState` 派生 → **实测后端活着时横幅就亮且永不消失**（`EventSource` 实例非 plain object，Vue 不 proxy，computed 停在首帧），改三态 `stream: idle/open/down` 后真断线 2.5s 现身、重启后 26.3s 自清（fixed/4px 12px/12·18/red-600 全对上，截图 `b1_banner.png`）。**error 红点行**：走块管线（`type='Error'`+`closed=true`，另开数组=第二个游标），历史回放与活流两条路各取一次（`wrap>prose>prose>errRow`、`tail=1` 证明 closed 必要），样式与文案照 `.turnErrorRow`/`message.turnError`。新增 `s8` **t12**（'Error' 不是 BlockType，t1 有盲区）+ 反向验证五例全 CAUGHT。**max-tokens 提示归错档**：源值出处找到（`turn-max-tokens` 由后端 `turn/end reason.kind` 驱动），本机桩服务实测 `finish_reason='length'` 在网关**两条路都进了 `response_metadata` 而全仓零消费者** → 截断今天静默，属档二，登记 PLAN **B8**；顺带推翻 `.env:13` 那句「会被截断（LengthFinishReasonError）」——content 非空时不抛。环境四条：面板不可见=0 宽布局（几何判据必须走 `shot.mjs`）、跨时间读数用「后台杀进程 + 前台长 eval」、反向验证锚点必须唯一（`b.closed = true` 有两处，撞出过假空转） |
| `2026-09-21-A2markdown层级实测与代码块补样式.md` | A2 六项判据全部取到计算值，并**推翻「CSS 已落位」这半句**：正文五项（h1 24/34、h2 22/32、h3 20/30、p `16px 0`、inline code 14px=0.875×16 + r6 + `0 5px`）确实在位；**code block banner `9px 14px` 全仓无落点**（`md-code-*` 四个类只有 `render.ts:35` 的 markup、零 CSS，实测 `pre padding=0`、字号继承 16px）→ 按参照系 `CodeBlock.module.css` 逐条补进 `MarkdownText.vue`，只用已有 token（不为 banner 造 `--dsw-font-xs-13`），复制按钮照抄 `font:inherit` 实测比标签高一号故改同档。样本自己造（判据点名的会话事件流在 db=0、隔离实例读 db15），踩空一次：`idea` 带中文 → 服务端没回 id（curl 载荷一律 ASCII 的老坑）。留一条设计问题：浅色档两个 alias 同为 `neutral-bluish-50` → banner 与代码无层次 |
| `2026-09-21-A3复制回执可信点击.md` | A3 从「只差留证」到 ✅（零代码改动，全部工作量在取证路径）。**核心事实：`doCopy` 只在 `clipboard.writeText` resolve 后才置 `copied`** → 没回执=写没成，不是漏渲染。两条浏览器路径实测都拿不到：内置面板 `clipboard-write=denied`；无头 Chrome `perm:"granted"` 但 `writeText` 仍 `NotAllowedError`（headless 无剪贴板后端）。解法：真窗口 Chrome + CDP `Browser.grantPermissions` + `Input.dispatchMouseEvent` 派发浏览器级可信输入（工装 `log/b14_a3_chrome.mjs`）。读数：`WRITE_OK` → 悬停 `[role=treeitem]` 出卡 → 点击 → `copied="复制成功"`、`cardClass="card copyable feedback"`，截图里卡**没被抽瘪**（`minHeight=copyHeight` 成立）。两次踩空：`.root` 是多组件共用的 scoped 类名（7 个候选全不是锚点）、第一版 `querySelector('.card .copied')` 打到了落地页 `.card.hero` |
| `2026-09-21-B2反向分页与首屏窗口.md` | B2 界面半边收口（用户 13:40 批「自起隔离实例」后）。**四条改判/新洞**：① `limit` 语义从「无 `before` 取头部」改判成**始终取窗口尾部**——首屏要的是「最新一屏」，取头部则胶囊永远没有下一页（`t11` 两头同判）；② 响应补 `has_more`（路由多取一条判显隐，bus 返回契约不动）；③ 反向翻页的整页游标全比 `lastCursor` 小 → 直接喂 `applyEvent` **被整页丢掉**，而被页界切开的块若 `tokens.push` 会把早半截排到晚半截后面 → `mergeEarlierPage` 在临时状态里合成再拼回正片；④ **`captureAnchor` 的「左边缘 +8px」打点永远落在正文列 32px 左内边距里**（四次探测全空 → 恒回 null → `restoreAnchor` 空转），这条零调用者的接缝一接上就红；改水平中心后锚点视口 y `-1242→-1242`。端到端读数：8721 + `REDIS__DB=15` 隔离实例、合成 60 块/895 条（**块长故意不整除 400**，定长 16 会让页界全落在块边界上、一次也走不到切块路径），首屏 27 行→两次点击 60 行、序号 `0..59` 升序无重复、到底 `has_more=false` 胶囊自隐。门禁：s8 11/11×3、s7 13/13 双配置、build 零 error×3、`tsc` 只剩一条与本次无关的旧错。一条环境噪声（Langfuse 未起的 export 报错）；**另有一条我自己取错的「发现」已当场撤销**：写过的「`think-14` 全仓无定义 → Think 行是豆腐块」不成立，别名在 `DsIcon.vue:45`、键在 `glyphs.ts`，探针实测是真 `svg+path`——失误在只 grep 了两张错表 + 把 16px 小图标看成 tofu（段 6 全文）。自纠四条见段 7（含「`python -B env …` 把 env 当脚本名 → exit 2 零 ✅，退出码非零 ≠ 测试红」） |
| `2026-09-21-C12取证过期引文与混币种根因.md` | C12 未起（剩 45→25 分钟要吃 s1..s6+s7），先做第 0.4 步锚点复核：**四条断言三条成立、两处更正**——① `tests/s7_platform.py:237` 措辞「原料就位」**不是臆造而是过期引文**（`git log -S` 定出生死：`187b3fc` 引入、`4849e32` B11 03:31 改写，旧文案仍印在 9 份 `log/*.out` 里），真锚点换成 `:241`/`:262`；**我自己第一版把它判成「编造的引文」并写进了 PLAN，全仓检索后当场回改**；② P95 半账面写大了（`GET /api/sessions/{sid}/trace` @ `sessions.py:288-294` 与前端 trace 展示都在位、span 带 `t0`/`ft`/`ts`）→ **只缺聚合口径**，且 `t0`/`ft` 可为 null 要先剔样本。**跨币种根因升级**：不是「测试缺覆盖」，是 `TOKEN_COSTS` 同表混两种币种（`:14` 人民币/千 vs 其余美元/千）相加进无单位 float `cost.py:26`+`:40`，符号还两处不一致（日志 `$`、界面 `¥`）→ 两条腿（A 分桶 / B 定汇率）登记 PLAN §9 待用户拍，不代拍、不编汇率。段 7 另记 D 档余账第一批：三条点名前提（`对照4:94` 装饰器零引用、`对照2:97` UploadKB 未通电、N6 两处）复核**全部仍成立、零文档改动**，并撤销一条我对 docs 的错判（docs 对、PLAN 错）。免门禁档，工作树两次都 34 件；三条检索坑见段 6 |
| `2026-09-21-C11时间旅行账面已过期.md` | C11 取证：PLAN 那格的证据**已过期**——「等 S7 快照采集」那两处 D1 在 06:37 就改准了（`施工4:110`/`README:222`），改准的新文字里 `make_checkpointer:80` 又是漂锚点（真值 `:82`，本轮两处改回）；回放面实测仍零（`get_state_history` 全仓零命中、无端点、前端只有事件 Timeline），采集侧有底（`s3b` t7/t8 真落断点）。**判据两支都不是自动化会话该拍的板** → 记 🟡 并把「做 vs 砍」连同成本写进 PLAN §9（原内容是「（空）」），顺带把 C1 与两条授权凑成一张待答清单 |
| `2026-09-21-C10鉴权不跨worker写进契约.md` | C10 按判据走第二支：「登录态不跨 worker」写进 `docs/前端/接口契约.md` §5.1（四条**进程内状态**表：token 表 / 登录失败计数 5×N / `users.json` 只有线程锁故跨进程丢账号 / 重启失效），并写明今天为什么打不到（compose 钉 `--workers 1` + auth 默认关）与提 workers 前的必做升级；**运行期行为一字未改，不是「修好了」**。**顺带实测查出一条反安全语义的错文档**：`auth.py:3` 与契约都写 `PLATFORM__AUTH`，但 settings 用 `env_nested_delimiter="__"` 且字段名是 `auth_enabled` → 两进程对照读数 `PLATFORM__AUTH=1` 打不开、`PLATFORM__AUTH_ENABLED=1` 才生效；照旧文档写会以为开了鉴权、实际全站不校验。读数：s8 11/11、s7 13/13 双配置×2（三份日志 ⚠=0 即 redis 分支未跳过）。同族错名已全仓反查并同批扫完：frontend 三处注释改真名（s8 11/11 + `npm run build` 零 error），只剩**别人在途的** `tests/s10_auth.py:3` 按铁律未动 |
| `2026-09-21-C9-prompt包名取证改判.md` | C9 由「只处理 3 处真隐患 prompt 文本 + 加法拼接校准常量」**改判 ⛔**，两条实测：① s6 t1 除逐字相等外还断言顶层常量**键集相等**，往受保护模块加一个新常量当场红（反向探针 exit 1，基线 23 组/t1 比中 54 项未跳过）；② 那三件资产 `codeharness/`+`server/` **零引用**、无反射旁路，真跑的 DI 线用自写 prompt → 隐患文本今天**没有触发路径**，写校准常量=新增死代码；重开条件与四处账面回写见文。另记一条：**上一棒 handoff 的时刻没按 `date` 取（超前真钟 15–25 分钟），导致「只剩 45 分钟」的窗口预算是错的** |
| `2026-09-21-C8精排默认值改未配置.md` | `RerankerConfig.base_url` 缺省从 `localhost:9998/v1` 改成空串（`.env`/compose 都没这服务，判据只挡「URL 空」→ 默认状态=每次走失败路径 + 一条 warning）；`s5 t32` 两层判据（配置类**默认值** + 哨兵客户端证明走跳过分支），**两层都反向验证会红**，层② 顺带实证哨兵异常被 `except Exception` 吞掉=「先抛再吞」的直接证据。**顺手发现**：`s5 t31` 的「A 写 B 查不到」是**空转断言**（替身 `search` 无条件返回 `[]`，连 A 自己都查不到）→ C4 不能拿它当已收口；另记一条自纠：C2 漏改 `docs/README.md` #4，**改门禁语义后要按新语义关键词全仓反查旧说法** |
| `2026-09-21-C2删除TeamState假通道.md` | C2 走「删干净」：`TeamState.docs` 字段 + 三处 init + `tests/` 7 处构造删掉，msgpack 白名单 `Document`/`Documents` **同批摘**（依据是实验：摘后整套 s3b 含真落断点 → **0 条 unregistered 告警**）；新增 `s3b t15` 两层正向守卫并反向验证会红；**关键实测：LangGraph 对未知状态键静默丢弃**（删字段后 `s16` 仍 5/5 exit 0）→ 残留写者炸不出来只能正向钉；**翻案**：`s3b main()` 中途抛错时 `close_all()` 不跑 → aiosqlite 线程吊住解释器 6.5 分钟不退出，上一轮那条「管道跑 26 分钟不返回」的悬案机制是它而非 grep 缓冲 |
| `2026-09-21-B2收口与SSE流尾游标竞态.md` | 接管僵尸锁（上一棒卡在等用户答复）后重新取证 B2：s8 **11/11** + s7 **13/13 双配置连跑 3 次**；**修掉一条生产真洞**——`RedisEventBus.subscribe()` 的流尾游标等后台 reader 首次调度才取，`/events` 走法 subscribe→history→续推，落在两者之间 XADD 的事件**永久丢失**；`log/s7_rep1.out`（03:21）证明该红早于本改动，隔离跑 t12 三遍全过；**「60s watchdog 卡死」其实是 t12 内层 `q.get()` 的 3s 超时冒到外层** → 看门禁红先读 traceback 里内层那一句 |
| `2026-09-21-阶段D文档对账.md` | PLAN §1 基线回写（HEAD 漂 24 笔、s7 13/13 + s8 10/10 真读数）；阶段 D 四项 D3/D4/D2/D1 收口全过程；新环境事实：**本机 ollama 推理已可用**、8718 未运行、后端没起时改用**只读枚举 `ch:index` status** 判「无人在跑」；长跑门禁别管道 grep（缓冲看不见进度） |
| `2026-09-20-计划审查与自动化建档.md` | `plan/PLAN.md` 逐条对账的取证结果（6 条事实断言已失效或口径反了）；两条心跳驱动器的互斥设计与 30 分钟错开；`log/` 目录的建立 |
