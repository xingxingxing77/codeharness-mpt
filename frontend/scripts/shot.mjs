/** 无依赖的 CDP 截图/探针：用系统里已有的 Chrome/Edge 无头模式拿真实渲染像素。
 *  内置浏览器面板不可见时视口是 0，rAF 不跑、截图被挡，而布局门禁需要确切宽度。
 *
 *  node frontend/scripts/shot.mjs --width 1280 --height 900 \
 *       --url http://127.0.0.1:8718/ --out shots/w1280.png [--eval "JS表达式"] [--wait-for "JS条件"]
 *  --eval 的返回值会被打印（--probe 语义）；不给 --out 就只探针不截图。
 */
import { spawn } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

const CANDIDATES = [
  process.env.CHROME,
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe'
].filter(Boolean)

function arg(name, dflt = '') {
  const i = process.argv.indexOf('--' + name)
  return i >= 0 ? process.argv[i + 1] : dflt
}
const has = (name) => process.argv.includes('--' + name)

const OPT = {
  url: arg('url', 'http://127.0.0.1:8718/'),
  out: arg('out'),
  width: Number(arg('width', 1280)),
  height: Number(arg('height', 900)),
  eval: arg('eval'),
  waitFor: arg('wait-for'),
  theme: arg('theme'), // light | dark —— 截图前把主题钉住
  // A1：无头 Chrome 默认按 `prefers-reduced-motion: reduce` 报，于是**所有动画都被媒体查询关掉**
  // （读出来的 animationName 全是 none）。要读动效本身必须显式覆盖，否则探针会把「环境把动效关了」
  // 误报成「代码没写动效」——这正是 A1 三处源值最容易得出的假结论。
  reducedMotion: arg('reduced-motion'),   // no-preference | reduce —— 空则跟随浏览器默认
  // 端口不能写死：上一轮的 chrome 没退干净时端口还被旧实例占着，
  // 于是 jsonPort() 拿到的是**旧浏览器**的 page target，在它身上 evaluate 必然
  // 得到「Execution context was destroyed」——页面其实活着（批次14/16 误判为无头抖动）。
  port: Number(arg('port')) || 9300 + Math.floor(Math.random() * 600)
}

function findBrowser() {
  for (const p of CANDIDATES) if (fs.existsSync(p)) return p
  throw new Error('找不到 Chrome/Edge，可用 --chrome=<路径> 或设置 CHROME 环境变量')
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function jsonPort(port) {
  for (let i = 0; i < 60; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${port}/json/list`)
      if (r.ok) return await r.json()
    } catch {}
    await sleep(250)
  }
  throw new Error('DevTools 端口没起来')
}

class Cdp {
  constructor(ws) {
    this.ws = ws
    this.id = 0
    this.pending = new Map()
    ws.addEventListener('message', (ev) => {
      const msg = JSON.parse(ev.data)
      const p = this.pending.get(msg.id)
      if (!p) return
      this.pending.delete(msg.id)
      msg.error ? p.reject(new Error(JSON.stringify(msg.error))) : p.resolve(msg.result)
    })
  }

  send(method, params = {}) {
    const id = ++this.id
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject })
      this.ws.send(JSON.stringify({ id, method, params }))
    })
  }

  async expr(expression) {
    const r = await this.send('Runtime.evaluate', {
      expression,
      awaitPromise: true,
      returnByValue: true
    })
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || 'eval 抛错')
    return r.result.value
  }
}

async function main() {
  const exe = findBrowser()
  const port = OPT.port
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'ch-shot-'))
  const proc = spawn(
    exe,
    [
      '--headless=new',
      '--disable-gpu',
      '--hide-scrollbars',
      `--remote-debugging-port=${port}`,
      `--user-data-dir=${profile}`,
      '--no-first-run',
      '--no-default-browser-check',
      `--window-size=${OPT.width},${OPT.height}`,
      OPT.url
    ],
    { stdio: 'ignore' }
  )

  try {
    // 只认 URL 与本次请求一致的 target：端口已经随机化，这一条兜住重定向/复用等残余情形，
    // 宁可等不到报错，也不要在上一次跑的页面上做探针。
    const want = new URL(OPT.url).href
    let page = null
    for (let i = 0; i < 40 && !page; i++) {
      page = (await jsonPort(port)).find((t) => t.type === 'page' && t.url === want)
      if (!page) await sleep(250)
    }
    if (!page) {
      const seen = (await jsonPort(port)).map((t) => t.url).join(', ')
      throw new Error(`端口 ${port} 上没有 URL 为 ${want} 的 page target（实际有：${seen}）`)
    }
    const ws = new WebSocket(page.webSocketDebuggerUrl)
    await new Promise((r, j) => {
      ws.addEventListener('open', r)
      ws.addEventListener('error', j)
    })
    const cdp = new Cdp(ws)

    await cdp.send('Page.enable')
    await cdp.send('Runtime.enable')
    // 显式覆盖度量：无头窗口尺寸不等于 CSS 视口
    await cdp.send('Emulation.setDeviceMetricsOverride', {
      width: OPT.width,
      height: OPT.height,
      deviceScaleFactor: 1,
      mobile: false
    })

    // 必须等这次导航提交完再发长求值。判定不能用 `document.readyState==='complete'` 一条：
    // 提交前 Chrome 给的是 about:blank，它的 readyState 本来就是 complete，于是探针照旧
    // 发在提交前，等真正文档提交时上下文被销毁 → -32000。必须再加「页面自己的 location.href
    // 已经是本次 URL」这一条。此前它被当成「无头页面打崩了、重跑即可」（批次14/16 的结论），
    // 其实一直是探针连早了。
    const readyExpr = `location.href === ${JSON.stringify(want)} && document.readyState === 'complete' ? 1 : 0`
    for (let i = 0; i < 150; i++) {
      let ready = false
      try {
        ready = (await cdp.expr(readyExpr)) === 1
      } catch {
        ready = false                      // 这一次求值正好撞上提交，下一轮重来
      }
      if (ready) break
      await sleep(100)
    }
    // 提交之后 Vue 还要挂载与拉数据，给它一个最短稳定窗口
    await sleep(300)

    if (OPT.waitFor) {
      let ok = false
      for (let i = 0; i < 40; i++) {
        if (await cdp.expr(`(${OPT.waitFor}) ? 1 : 0`)) {
          ok = true
          break
        }
        await sleep(200)
      }
      if (!ok) throw new Error('等不到条件：' + OPT.waitFor)
    }

    // A1：先把动效媒体特性钉住，再截图/探针（默认无头会报 reduce，动效全被关掉）
    if (OPT.reducedMotion) {
      await cdp.send('Emulation.setEmulatedMedia', {
        features: [{ name: 'prefers-reduced-motion', value: OPT.reducedMotion }]
      })
      await sleep(120)
    }

    // 深色就是 body 上的 presence 属性，CSS 只认它，不必绕进 store
    if (OPT.theme) {
      await cdp.expr(
        `document.body.toggleAttribute('data-ds-dark-theme', ${OPT.theme === 'dark'}); document.documentElement.style.colorScheme=${JSON.stringify(OPT.theme || 'light')}; 1`
      )
      await sleep(120)
    }

    if (OPT.eval) {
      const v = await cdp.expr(`(async () => { ${OPT.eval} })()`)
      console.log(typeof v === 'string' ? v : JSON.stringify(v, null, 1))
    }

    if (OPT.out) {
      const r = await cdp.send('Page.captureScreenshot', { format: 'png' })
      fs.mkdirSync(path.dirname(path.resolve(OPT.out)), { recursive: true })
      fs.writeFileSync(path.resolve(OPT.out), Buffer.from(r.data, 'base64'))
      console.log('写入 ' + OPT.out + ' (' + OPT.width + '×' + OPT.height + ')')
    }
    ws.close()
  } finally {
    proc.kill()
    for (let i = 0; i < 20; i++) {
      try {
        fs.rmSync(profile, { recursive: true, force: true })
        break
      } catch {
        await sleep(150)
      }
    }
  }
}

main().catch((e) => {
  console.error('失败：' + e.message)
  process.exit(1)
})
