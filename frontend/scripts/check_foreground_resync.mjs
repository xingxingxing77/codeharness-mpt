/** C110 自测：回到前台必须**重开一次流**，把后台被「丢最旧」的那段从历史补回来。
 *  跑法：node frontend/scripts/check_foreground_resync.mjs
 *
 *  背景：服务端那条订阅队列有界（C90 的 Redis 版、C103 的进程内版），满了丢**最旧**；
 *  依据是「旧事件重连时由 `/events/history` 补得回来」。但**触发点**今天不存在：
 *  后台标签页被浏览器冻结时连接没断 ⇒ EventSource 不重连 ⇒ 洞一直留着，除非用户手动刷新。
 *  修法不新造机制：`connect()` 里挂一次性 `visibilitychange`，回前台时重开流——
 *  路由本来就是「先按 `after` 回放历史、再跟活流」（`server/api/sessions.py:591-596`）。
 *
 *  五格：
 *    ① `connect()` 建的第一条流，URL 必须带 `after=<已应用的游标>`（这是补回的**前提**，不是本件新增，
 *       但以后谁把它改掉，补历史就成了空话 ⇒ 钉住）；
 *    ② 监听只挂一次（连开三次流 ⇒ 仍是 1 个 listener，否则回一次前台重开 N 条流）；
 *    ③ 派发 `visibilitychange` 且 `visible` ⇒ **新建一条流**，且 `after` 与①同（补的就是丢掉那段）；
 *    ④ 阳性对照：`hidden` 时派发 ⇒ **不许**重开（不然打字中途也在反复断流重连）；
 *    ⑤ 换会话（`currentId` 置空）后派发 ⇒ 不许重开（`connect()` 自己会 return）。
 *
 *  与 `check_page_replay.mjs` 同一套姿势：vite 自带的 esbuild 现打自包含产物（零新依赖），
 *  `localStorage`/`document`/`EventSource` 都是**替身**——只测 store 的派发与参数，不测浏览器。
 */
import { buildSync } from 'esbuild'
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const storePath = fileURLToPath(new URL('../src/stores/sessions.ts', import.meta.url)).replaceAll('\\', '/')
const dir = mkdtempSync(join(tmpdir(), 'ch_fwdresync_'))
const entry = join(dir, 'entry.ts')
const out = join(dir, 'bundle.mjs')
writeFileSync(entry, `export { createPinia, setActivePinia } from 'pinia'\nexport { useSessionStore } from '${storePath}'\n`)
const nodeModules = fileURLToPath(new URL('../node_modules', import.meta.url)).replaceAll('\\', '/')
buildSync({ entryPoints: [entry], outfile: out, bundle: true, format: 'esm', platform: 'node',
            logLevel: 'silent', nodePaths: [nodeModules] })

const { createPinia, setActivePinia, useSessionStore } = await import(pathToFileURL(out).href)
let failed = 0
const eq = (name, got, want) => {
  const ok = JSON.stringify(got) === JSON.stringify(want)
  if (!ok) failed++
  console.log(`${ok ? '✅' : '❌'} ${name}${ok ? '' : ` 得到 ${JSON.stringify(got)} 期望 ${JSON.stringify(want)}`}`)
}

// —— 浏览器替身：`getToken()` 读 localStorage、connect() 用 EventSource、钩子用 document ——
globalThis.localStorage = { getItem: () => '', setItem: () => {}, removeItem: () => {} }
const opened = []
globalThis.EventSource = class {
  constructor(url) { this.url = url; opened.push(url) }
  close() {}
}
const listeners = []
const doc = {
  _v: 'hidden',
  addEventListener(type, fn) { if (type === 'visibilitychange') listeners.push(fn) },
  get visibilityState() { return this._v },
}
globalThis.document = doc

setActivePinia(createPinia())
const s = useSessionStore()
const CUR = '0000000000042-000000'
s.currentId = 's1'
s.lastCursor = CUR

s.connect()
eq('① 首条流带 after=已应用游标（补回的前提）', opened.length === 1 && opened[0].includes(`after=${encodeURIComponent(CUR)}`), true)
eq('② 监听只挂一次', listeners.length, 1)

s.connect()
s.connect()
eq('② 连开三次仍只有一个监听（没挂多次）', listeners.length, 1)
eq('② 三次各建了一条流（说明②不是「没执行」）', opened.length, 3)

doc._v = 'visible'
listeners.forEach((fn) => fn())
eq('③ 回前台 ⇒ 新建一条流', opened.length, 4)
eq('③ 补回用的还是那条游标（被丢的那段从历史回放）', opened[3].includes(`after=${encodeURIComponent(CUR)}`), true)

doc._v = 'hidden'
listeners.forEach((fn) => fn())
eq('④ 后台时不许重开（阳性对照）', opened.length, 4)

doc._v = 'visible'
s.currentId = ''
listeners.forEach((fn) => fn())
eq('⑤ 没有当前场次时不重开', opened.length, 4)

rmSync(dir, { recursive: true, force: true })
console.log(failed ? `\n${failed} 条失败` : '\n全过')
process.exit(failed ? 1 : 0)
