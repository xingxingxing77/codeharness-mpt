import { defineStore } from 'pinia'
import type { Session } from '../types'
import { parseBackendTime } from '../utils/relativeTime'

export type GroupBy = 'workspace' | 'flat'
export type OrderBy = 'updated' | 'created' | 'manual'

const KEY = 'ch.workspace.view.v1'

export interface SessionGroup {
  key: string
  label: string
  /** 未分组桶没有工作区身份，所以不挂 hover 卡 */
  ungrouped: boolean
  sessions: Session[]
  /** 组内是否含当前会话：决定文件夹染业务蓝 */
  containsCurrent: boolean
}

interface ViewState {
  groupBy: GroupBy
  orderBy: OrderBy
  expansion: Record<string, boolean>
  archivedOpen: boolean
  /** 组键 → 会话 id 顺序。只存顺序不存时间戳：拖完即定，新会话不在这张表里就排最后。 */
  manual: Record<string, string[]>
}

function load(): ViewState {
  const base: ViewState = {
    groupBy: 'workspace', orderBy: 'updated', expansion: {}, archivedOpen: false, manual: {}
  }
  try {
    return Object.assign(base, JSON.parse(localStorage.getItem(KEY) || '{}'))
  } catch {
    return base
  }
}

/** 排序键：后端没有 updatedAt 字段，status 变化只改内存态，所以「最近活动」用
 *  finished_at → started_at → created_at 里能取到的最后一个。 */
function activityOf(s: Session): number {
  return (
    parseBackendTime(s.finished_at) || parseBackendTime(s.started_at) || parseBackendTime(s.created_at)
  )
}

function newestFirst(a: Session, b: Session, by: OrderBy): number {
  const t = by === 'created' ? parseBackendTime(a.created_at) : activityOf(a)
  const u = by === 'created' ? parseBackendTime(b.created_at) : activityOf(b)
  if (u !== t) return u - t
  // 同分按 id 定序，避免每次刷新顺序抖
  return a.id < b.id ? -1 : 1
}

/** 按该组的手动表重排。表里没有的（新会话、别的设备建的）不给名次：
 *  `Array#sort` 稳定，所以它们保持原时间序并整体排在最后，而不是插到中间。 */
function applyManual(list: Session[], order: string[] | undefined): Session[] {
  if (!order || !order.length) return list
  const rank = new Map(order.map((id, i) => [id, i] as const))
  const at = (s: Session) => rank.get(s.id) ?? order.length
  return [...list].sort((a, b) => at(a) - at(b))
}

export const useWorkspaceViewStore = defineStore('workspaceView', {
  state: (): ViewState => load(),

  actions: {
    persist() {
      // 必须逐键取：`{ ...this }` 展开 pinia option store 会把内部引用一起卷进来，
      // JSON.stringify 当场抛「Converting circular structure to JSON」
      // （component → vnode 成环）——于是分组展开与排序从来没落过盘。
      const { groupBy, orderBy, expansion, archivedOpen, manual } = this
      localStorage.setItem(KEY, JSON.stringify({ groupBy, orderBy, expansion, archivedOpen, manual }))
    },

    setGroupBy(v: GroupBy) {
      this.groupBy = v
      this.persist()
    },

    setOrderBy(v: OrderBy) {
      this.orderBy = v
      this.persist()
    },

    /** 拖拽落点。`ids` 是 owner 当下渲染出来的顺序——首次拖动就以它为基准，
     *  不必先「快照成 manual 再拖」，也不会把没拖过的组凭空定死。 */
    moveManual(groupKey: string, ids: string[], sid: string, target: string, half: 'before' | 'after') {
      if (sid === target) return
      const list = ids.filter((x) => x !== sid)
      const at = list.indexOf(target)
      if (at < 0) return
      list.splice(half === 'before' ? at : at + 1, 0, sid)
      this.manual = { ...this.manual, [groupKey]: list }
      if (this.orderBy !== 'manual') this.orderBy = 'manual'
      this.persist()
    },

    toggle(key: string) {
      this.expansion[key] = !this.isExpanded(key)
      this.persist()
    },

    /** 组展开是持久化的；当前会话落进某组时自动展开该组。 */
    isExpanded(key: string): boolean {
      return this.expansion[key] !== false
    },

    expand(key: string) {
      if (this.expansion[key] === false) {
        this.expansion[key] = true
        this.persist()
      }
    },

    /** 分组派生放在 store 里，渲染层不再自己扫列表。 */
    groups(sessions: Session[], currentId: string): SessionGroup[] {
      // manual 的自然序打底用「最近活动」：表里没登记的新会话因此保持时间序排在最后
      const natural = this.orderBy === 'manual' ? 'updated' : this.orderBy
      const sorted = [...sessions].sort((a, b) => newestFirst(a, b, natural))
      if (this.groupBy === 'flat') {
        return [
          {
            key: '',
            label: '',
            ungrouped: true,
            sessions: applyManual(sorted, this.manual['']),
            containsCurrent: !!currentId
          }
        ]
      }

      const map = new Map<string, Session[]>()
      for (const s of sorted) {
        // 服务端在 project_name 为空时回填 sid，所以「项目名等于自己的 id」就是未分组，
        // 否则每个匿名会话都会撑出一个只有它自己的假项目组。
        const key = s.project_name && s.project_name !== s.id ? s.project_name : ''
        if (!map.has(key)) map.set(key, [])
        map.get(key)!.push(s)
      }

      const groups: SessionGroup[] = []
      for (const [key, list] of map) {
        if (!key) continue
        groups.push({
          key,
          label: key,
          ungrouped: false,
          sessions: applyManual(list, this.manual[key]),
          containsCurrent: list.some((s) => s.id === currentId)
        })
      }
      const loose = map.get('')
      if (loose?.length) {
        groups.push({
          key: '',
          label: '未分组',
          ungrouped: true,
          sessions: applyManual(loose, this.manual['']),
          containsCurrent: loose.some((s) => s.id === currentId)
        })
      }
      return groups
    }
  }
})
