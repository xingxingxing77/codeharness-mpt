/** 方案 C 前端半边：`.mmd` 是落盘真源，渲染与导出都发生在这里。
 *
 * - **按需加载**：mermaid 走动态 import（体积大），页面没图就不下载。
 * - **主题跟随应用暖调**（style.css 的 token），不用 mermaid 默认蓝紫。
 * - **失败不白板**：解析错误时保留源码 + 一行原因——坏图也要能读（trustworthy）。
 */
import type { Mermaid } from 'mermaid'

let once: Promise<Mermaid> | null = null

function getMermaid(): Promise<Mermaid> {
  if (!once) {
    once = import('mermaid').then(({ default: m }) => {
      m.initialize({
        startOnLoad: false,
        securityLevel: 'strict',
        theme: 'base',
        fontFamily: '-apple-system, BlinkMacSystemFont, Segoe UI, Microsoft YaHei, sans-serif',
        flowchart: { curve: 'linear', htmlLabels: false },
        themeVariables: {
          background: '#fdfcf9',
          primaryColor: '#f1ecfd',
          primaryBorderColor: '#7a5af8',
          primaryTextColor: '#262521',
          secondaryColor: '#f4f2ee',
          secondaryBorderColor: '#8f8b7e',
          secondaryTextColor: '#55524a',
          tertiaryColor: '#e7dec7',
          lineColor: '#8f8b7e',
          textColor: '#262521',
          fontSize: '14px'
        }
      })
      return m
    })
  }
  return once
}

let uid = 0

/** 渲染源码 → {svg} 或 {error}。永不抛：调用方拿到的永远是可展示的两种结果之一。 */
export async function renderMermaid(src: string): Promise<{ svg?: string; error?: string }> {
  try {
    const m = await getMermaid()
    const { svg } = await m.render(`mmd-${Date.now()}-${uid++}`, src)
    return { svg }
  } catch (e: any) {
    return { error: String(e?.message || e).split('\n')[0] }
  }
}

export function downloadSvg(svg: string, name: string) {
  const a = document.createElement('a')
  a.href = URL.createObjectURL(new Blob([svg], { type: 'image/svg+xml' }))
  a.download = `${(name || 'diagram').replace(/\.[a-z0-9]+$/i, '')}.svg`
  a.click()
  URL.revokeObjectURL(a.href)
}

function esc(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

/** 把渲染结果装进 host：工具条（图形/源码 + 导出 SVG）+ 两个视图。
 *  MermaidView 与 markdown 围栏水合共用这一份 DOM，样式与行为只实现一次。 */
export async function mountFigure(host: HTMLElement, code: string, name: string): Promise<void> {
  host.textContent = ''
  host.className = 'mmd-mount'
  if (!code.trim()) {
    host.innerHTML = '<div class="mmd-load">图为空</div>'
    return
  }
  const load = document.createElement('div')
  load.className = 'mmd-load'
  load.textContent = '图渲染中…'
  host.append(load)

  const r = await renderMermaid(code)
  if (!host.isConnected) return // 宿主内容已被重绘，不往孤儿节点上贴

  host.textContent = ''
  const fig = document.createElement('div')
  fig.className = r.svg ? 'mmd-fig' : 'mmd-fig mmd-bad'

  const bar = document.createElement('div')
  bar.className = 'mmd-bar'
  bar.innerHTML =
    `<span class="mmd-name">${esc(name)}</span><span class="mmd-grow"></span>` +
    `<span class="mmd-seg" role="group" aria-label="视图切换">` +
    `<button class="on" data-v="fig">图形</button><button data-v="src">源码</button></span>` +
    `<button class="mmd-exp"${r.svg ? '' : ' disabled'}>导出 SVG</button>`
  fig.append(bar)

  const body = document.createElement('div')
  body.className = 'mmd-body mmd-figview'
  body.setAttribute('role', 'img')
  body.setAttribute('aria-label', `${name} 图`)
  if (r.svg) body.innerHTML = r.svg
  else body.hidden = true
  fig.append(body)

  const srcView = document.createElement('pre')
  srcView.className = 'mmd-body mmd-srcview'
  srcView.textContent = code
  if (r.svg) srcView.hidden = true
  fig.append(srcView)

  if (r.error) {
    const err = document.createElement('div')
    err.className = 'mmd-err'
    err.textContent = `无法解析图：${r.error}`
    fig.append(err)
  }

  bar.querySelector('.mmd-seg')!.addEventListener('click', (e) => {
    const b = (e.target as HTMLElement).closest('button')
    if (!b || b.classList.contains('on')) return
    bar.querySelectorAll('.mmd-seg button').forEach((x) => x.classList.remove('on'))
    b.classList.add('on')
    const showSrc = b.dataset.v === 'src'
    body.hidden = showSrc
    srcView.hidden = !showSrc
  })
  const exp = bar.querySelector<HTMLButtonElement>('.mmd-exp')!
  if (r.svg) exp.addEventListener('click', () => downloadSvg(r.svg!, name))

  host.append(fig)
}

/** v-html 渲染出来的 markdown 里，```mermaid 围栏已被换成 div.mmd-src（见 render.ts），
 *  挂载后逐块水合成图。幂等：data 标记过的跳过；重绘产生的新节点只补新的。 */
export async function hydrateMermaid(root: HTMLElement | null): Promise<void> {
  if (!root) return
  for (const el of Array.from(root.querySelectorAll<HTMLElement>('div.mmd-src'))) {
    if (el.dataset.mmd) continue
    el.dataset.mmd = '1'
    await mountFigure(el, el.textContent || '', 'diagram')
  }
}
