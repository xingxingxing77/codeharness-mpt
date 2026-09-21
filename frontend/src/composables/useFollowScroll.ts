import { onBeforeUnmount, ref, watch, type Ref } from 'vue'

/** 流式对话的滚动跟随。照抄参考项目 ChatView 的判定，不重新发明。
 *
 *  难点是「谁动了滚动条」：内容在长、浏览器缩窗、程序回滚、用户滚轮都会触发同一个
 *  scroll 事件。这里不注册 wheel/touch 监听，而是每次程序化写入前记下 observedTop，
 *  再用 |scrollTop − min(observedTop, floor)| > 0.5 反推「读者自己动过」。
 *  浏览器缩窗导致的钳位会精确落在钳位后的 floor 上，因此不会被误判成用户滚动。 */

const FOLLOW_THRESHOLD = 24
const READER_EPS = 0.5

export interface FollowScroll {
  atBottom: Ref<boolean>
  /** 内容变化后调用。forced=true 表示「新出现的必须是用户自己的话」，即使读者
   *  正在往上翻也强制回到底部。 */
  notify: (forced?: boolean) => void
  /** 供 prepend 历史用：抓一个视觉锚点，插完之后把位置补回去 */
  captureAnchor: (keyOf: (el: Element) => string | null) => { key: string; top: number } | null
  restoreAnchor: (anchor: { key: string; top: number } | null, keyOf: (el: Element) => string | null) => void
  scrollToBottom: (smooth?: boolean) => void
}

export function useFollowScroll(
  scrollEl: Ref<HTMLElement | undefined>,
  flowEl: Ref<HTMLElement | undefined>,
  seatEl: Ref<HTMLElement | undefined>,
  followSig: () => string
): FollowScroll {
  const atBottom = ref(true)
  let observedTop = 0
  let lastSig = followSig()
  let ro: ResizeObserver | undefined

  const floorOf = (el: HTMLElement) => Math.max(0, el.scrollHeight - el.clientHeight)

  function write(fn: (el: HTMLElement) => void) {
    const el = scrollEl.value
    if (!el) return
    fn(el)
    // 记下这次程序化写入的结果，作为下一次「读者是否动过」的基准
    observedTop = el.scrollTop
  }

  function scrollToBottom(smooth = false) {
    write((el) => el.scrollTo({ top: el.scrollHeight, behavior: smooth ? 'smooth' : 'auto' }))
  }

  function pinned(el: HTMLElement) {
    return el.scrollHeight - el.scrollTop - el.clientHeight <= FOLLOW_THRESHOLD + 1
  }

  function onScroll() {
    const el = scrollEl.value
    if (!el) return
    const floor = floorOf(el)
    const movedByReader = Math.abs(el.scrollTop - Math.min(observedTop, floor)) > READER_EPS
    if (!movedByReader && pinned(el)) {
      // 读者没动而内容把页面顶起来了：贴回去，别让他看到半截行
      write((e) => (e.scrollTop = e.scrollHeight))
      atBottom.value = true
      return
    }
    atBottom.value = pinned(el)
  }

  function notify(forced = false) {
    const el = scrollEl.value
    if (!el) return
    const sig = followSig()
    const sigChanged = sig !== lastSig
    lastSig = sig
    if (forced) {
      scrollToBottom()
      atBottom.value = true
      return
    }
    if (sigChanged && atBottom.value) scrollToBottom()
  }

  function captureAnchor(keyOf: (el: Element) => string | null) {
    const el = scrollEl.value
    const flow = flowEl.value
    if (!el || !flow) return null
    // x 取滚动区水平中心。原先写的是「左边缘 +8px」，而正文列有 32px 左内边距——
    // 打点永远落在列的 padding 上，四次探测全空 → captureAnchor 恒回 null、
    // restoreAnchor 空转，prepend 一页就把读者那一屏整屏推走（B2 首次接上才发现）。
    const x = el.getBoundingClientRect().left + el.clientWidth / 2
    // 取视觉上第一个稳定行做锚点，而不是硬算行数
    const probes = [0.02, 0.12, 0.3, 0.5]
    for (const p of probes) {
      const hit = document.elementFromPoint(x, el.getBoundingClientRect().top + el.clientHeight * p)
      const row = hit?.closest('[data-chat-anchor-key]')
      const key = row ? keyOf(row) : null
      if (key && row) {
        const top = row.getBoundingClientRect().top - flow.getBoundingClientRect().top
        return { key, top }
      }
    }
    return null
  }

  function restoreAnchor(
    anchor: { key: string; top: number } | null,
    keyOf: (el: Element) => string | null
  ) {
    const el = scrollEl.value
    const flow = flowEl.value
    if (!el || !flow || !anchor) return
    const rows = Array.from(flow.querySelectorAll('[data-chat-anchor-key]'))
    const row = rows.find((r) => keyOf(r) === anchor.key)
    if (!row) return
    const delta = row.getBoundingClientRect().top - flow.getBoundingClientRect().top - anchor.top
    if (Math.abs(delta) > 0.5) write((e) => (e.scrollTop += delta))
  }

  function observe() {
    if (typeof ResizeObserver === 'undefined') return
    ro = new ResizeObserver(() => {
      // 只有钉在底部时才随内容增长跟滚，否则会把读者正在读的那屏拽走
      if (atBottom.value) scrollToBottom()
    })
    if (flowEl.value) ro.observe(flowEl.value)
    if (seatEl.value) ro.observe(seatEl.value)
  }

  watch([scrollEl, flowEl, seatEl], observe)

  onBeforeUnmount(() => ro?.disconnect())

  return { atBottom, notify, captureAnchor, restoreAnchor, scrollToBottom }
}
