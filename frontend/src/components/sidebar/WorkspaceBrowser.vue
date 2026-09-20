<template>
  <div class="root" :class="{ rail: !wide }">
    <div class="sectionHeader">
      <span v-if="wide" class="sectionLabel">{{ view.groupBy === 'workspace' ? '项目' : '会话' }}</span>
      <div class="searchSlot" :class="{ searchSlotExpanded: searchOpen }">
        <div class="search" :class="{ searchExpanded: searchOpen }" @click="expandSearch">
          <button class="searchBtn" :aria-expanded="searchOpen" aria-label="搜索会话" @click.stop="toggleSearch">
            <DsIcon name="search" :size="searchOpen ? 11 : 14" />
          </button>
          <input
            ref="searchEl"
            v-model="query"
            class="searchInput"
            type="text"
            placeholder="搜索会话…"
            maxlength="500"
            :tabindex="searchOpen ? 0 : -1"
            @keydown.esc="closeSearch"
          />
          <button v-if="query" class="clearBtn" aria-label="清除搜索" @click.stop="query = ''">
            <DsIcon name="close-fill" :size="14" />
          </button>
        </div>
      </div>
      <div v-if="wide" class="headerActions">
        <VMenu :items="viewItems" align="end" compact @select="onView">
          <template #default="{ open, toggle }">
            <button class="iconBtn" :aria-expanded="open" aria-label="视图选项" @click.stop="toggle()">
              <DsIcon name="personalization" :size="16" />
            </button>
          </template>
        </VMenu>
      </div>
    </div>

    <div ref="listEl" class="list" role="tree" aria-label="会话">
      <div v-if="!total" class="empty">还没有会话</div>
      <div v-if="loading" class="empty">载入中…</div>

      <!-- 平铺模式 -->
      <template v-if="view.groupBy === 'flat'">
        <SessionRow
          v-for="s in shownFlat"
          :key="s.id"
          :s="s"
          :selected="s.id === store.currentId"
          :pinned="!!s.pinned"
          :draggable="canDrag"
          :drag-active="dragActive"
          :marker="markerFor(s.id)"
          @open="store.select"
          @rename="renaming = $event"
          @archive="onArchive"
          @pin="onPin"
          @remove="confirming = $event"
          @drag-start="onDragStart"
          @drag-end="onDragEnd"
          @drag-over="onDragOver"
          @drop="(id, half) => onDrop('', idsOf({ sessions: shownFlat }), id, half)"
        />
      </template>

      <!-- 分组模式 -->
      <div v-for="g in groups" v-else :key="g.key || '__loose'" class="groupSection">
        <ProjectRow
          :group-key="g.key"
          :label="g.label"
          :ungrouped="g.ungrouped"
          :expanded="view.isExpanded(g.key)"
          :contains-current="g.containsCurrent"
          @toggle="view.toggle($event)"
          @new-session="store.goHome()"
        />
        <template v-if="view.isExpanded(g.key)">
          <SessionRow
            v-for="s in visible(g)"
            :key="s.id"
            class="indented"
            :s="s"
            :selected="s.id === store.currentId"
            :pinned="!!s.pinned"
            :draggable="canDrag"
            :drag-active="dragActive"
            :marker="markerFor(s.id)"
            @open="store.select"
            @rename="renaming = $event"
            @archive="onArchive"
            @pin="onPin"
            @remove="confirming = $event"
            @drag-start="onDragStart"
            @drag-end="onDragEnd"
            @drag-over="onDragOver"
            @drop="(id, half) => onDrop(g.key, idsOf(g), id, half)"
          />
          <button
            v-if="g.sessions.length > LIMIT"
            class="overflowBtn"
            :aria-expanded="String(overflow[g.key] === true)"
            @click="setOverflow(g.key, !overflow[g.key])"
          >
            {{ overflow[g.key] ? '收起' : `展开其余 ${g.sessions.length - LIMIT} 个会话` }}
          </button>
          <div v-if="!g.sessions.length" class="empty indented">暂无会话</div>
        </template>
      </div>
      <span class="fade" />
    </div>

    <!-- 重命名 -->
    <VModal :open="!!renaming" title="重命名会话" width="440px" height="auto" @close="renaming = null">
      <div class="dialog">
        <div class="dialogTitle">重命名会话</div>
        <input
          v-model="renameValue"
          class="renameInput"
          maxlength="200"
          data-autofocus
          @keydown.enter="submitRename"
          @keydown.esc="renaming = null"
        />
        <div v-if="dialogError" class="renameError">{{ dialogError }}</div>
        <div class="dialogFoot">
          <VButton size="s" variant="ghost" @click="renaming = null">取消</VButton>
          <VButton size="s" variant="info" @click="submitRename">保存</VButton>
        </div>
      </div>
    </VModal>

    <!-- 删除确认 -->
    <VModal :open="!!confirming" title="删除会话" width="440px" height="auto" @close="confirming = null">
      <div class="dialog">
        <div class="dialogTitle">删除「{{ confirming?.idea || confirming?.id }}」？</div>
        <div class="dialogDesc">会话记录与事件流会被删除。工作区目录里的产物保留。</div>
        <div v-if="dialogError" class="renameError">{{ dialogError }}</div>
        <div class="dialogFoot">
          <VButton size="s" variant="ghost" @click="confirming = null">取消</VButton>
          <VButton size="s" variant="danger" :disabled="busy" @click="submitDelete">删除</VButton>
        </div>
      </div>
    </VModal>
  </div>
</template>

<script setup lang="ts">
/** 侧栏浏览区：分组/排序/搜索/溢出/重命名/归档/删除。
 *  后端没有 workspace 实体，所以这里不画「添加工作区」按钮（假壳子一律不画）。 */
import { computed, nextTick, reactive, ref, watch } from 'vue'
import { useSessionStore } from '../../stores/sessions'
import { useWorkspaceViewStore } from '../../stores/workspaceView'
import { useToastStore } from '../../stores/toast'
import DsIcon from '../ui/DsIcon.vue'
import VMenu from '../ui/VMenu.vue'
import VModal from '../ui/VModal.vue'
import VButton from '../ui/VButton.vue'
import type { MenuItem } from '../ui/menuTypes'
import ProjectRow from './ProjectRow.vue'
import SessionRow from './SessionRow.vue'
import type { Session } from '../../types'

const props = defineProps<{ wide: boolean }>()

const store = useSessionStore()
const view = useWorkspaceViewStore()
const toast = useToastStore()

/** 每组默认只给 5 条，多的收进溢出按钮（组件内态，不持久化，同参考项目） */
const LIMIT = 5
const overflow = reactive<Record<string, boolean>>({})
function setOverflow(key: string, v: boolean) {
  overflow[key] = v
}

/* ---------- 搜索 ---------- */
const query = ref('')
const searchOpen = ref(false)
const searchEl = ref<HTMLInputElement>()

function toggleSearch() {
  searchOpen.value ? collapseSearch() : expandSearch()
}

async function expandSearch() {
  if (searchOpen.value) return
  searchOpen.value = true
  await nextTick()
  searchEl.value?.focus()
}

function collapseSearch() {
  searchOpen.value = false
  query.value = ''
}

const filtered = computed(() => {
  const q = query.value.trim().toLowerCase()
  // 归档项默认不进列表；搜索只看标题（服务端没有内容检索接口，不假装能搜正文）
  const base = store.sessions.filter((s) => !s.archived)
  if (!q) return base
  return base.filter(
    (s) => (s.idea || '').toLowerCase().includes(q) || (s.project_name || '').toLowerCase().includes(q)
  )
})

const groups = computed(() => view.groups(filtered.value, store.currentId))
const shownFlat = computed(() => groups.value[0]?.sessions ?? [])
const total = computed(() => filtered.value.length)
const loading = computed(() => !store.sessions.length && !store.health)

function visible(g: { key: string; sessions: Session[] }): Session[] {
  return overflow[g.key] ? g.sessions : g.sessions.slice(0, LIMIT)
}

/* ---------- 拖拽排序（orderBy: 'manual'） ----------------------------------
 * 行只报「落在哪一行的上半/下半」，插位在这里算——因为基准必须是**当下渲染出来的
 * 那串 id**（含溢出收起后的可见顺序），行自己看不到兄弟节点。 */
const dragId = ref('')
const over = ref<{ id: string; half: 'before' | 'after' } | null>(null)
const dragActive = computed(() => !!dragId.value)

/** 搜索态不拖：拖的是过滤后的半截列表，落点会算进被过滤掉的邻居身上。 */
const canDrag = computed(() => !query.value.trim())

function markerFor(id: string): 'before' | 'after' | null {
  return over.value && over.value.id === id ? over.value.half : null
}

function onDragStart(id: string) {
  dragId.value = id
  over.value = null
}

function onDragEnd() {
  dragId.value = ''
  over.value = null
}

function onDragOver(id: string, half: 'before' | 'after') {
  if (!dragId.value || id === dragId.value) return
  over.value = { id, half }
}

function onDrop(groupKey: string, ids: string[], id: string, half: 'before' | 'after') {
  if (dragId.value) view.moveManual(groupKey, ids, dragId.value, id, half)
  onDragEnd()
}

const idsOf = (g: { sessions: Session[] }) => g.sessions.map((s) => s.id)

/* ---------- 视图选项 ---------- */
const viewItems = computed<MenuItem[]>(() => [
  { kind: 'label', label: '分组方式' },
  { key: 'g:workspace', label: '按项目', checked: view.groupBy === 'workspace' },
  { key: 'g:flat', label: '在一个列表里', checked: view.groupBy === 'flat' },
  { kind: 'sep' },
  { kind: 'label', label: '排序' },
  { key: 'o:updated', label: '最近活动', checked: view.orderBy === 'updated' },
  { key: 'o:created', label: '创建时间', checked: view.orderBy === 'created' },
  { key: 'o:manual', label: '手动排序', checked: view.orderBy === 'manual' }
])

function onView(it: MenuItem) {
  const k = String(it.key)
  if (k.startsWith('g:')) view.setGroupBy(k.slice(2) as 'workspace' | 'flat')
  else if (k.startsWith('o:')) view.setOrderBy(k.slice(2) as 'updated' | 'created' | 'manual')
}

/* ---------- 行操作 ---------- */
const renaming = ref<Session | null>(null)
const confirming = ref<Session | null>(null)
const renameValue = ref('')
const dialogError = ref('')
const busy = ref(false)

watch(renaming, (s) => {
  renameValue.value = s?.idea || ''
  dialogError.value = ''
})

async function submitRename() {
  const s = renaming.value
  if (!s) return
  const v = renameValue.value.trim()
  if (!v) {
    dialogError.value = '名称不能为空'
    return
  }
  busy.value = true
  try {
    await store.patchSession(s.id, { idea: v })
    renaming.value = null
  } catch (e) {
    dialogError.value = (e as Error).message
  } finally {
    busy.value = false
  }
}

async function onArchive(s: Session) {
  try {
    await store.patchSession(s.id, { archived: !s.archived })
    toast.push(s.archived ? '已取消归档' : '已归档', 'success')
  } catch (e) {
    toast.push((e as Error).message, 'error')
  }
}

async function onPin(s: Session) {
  try {
    await store.patchSession(s.id, { pinned: !s.pinned })
  } catch (e) {
    toast.push((e as Error).message, 'error')
  }
}

async function submitDelete() {
  const s = confirming.value
  if (!s) return
  busy.value = true
  try {
    await store.removeSession(s.id)
    confirming.value = null
    toast.push('会话已删除', 'success')
  } catch (e) {
    dialogError.value = (e as Error).message
  } finally {
    busy.value = false
  }
}
</script>

<style scoped>
.root {
  --dsh-session-list-edge-inset: var(--dsh-sidebar-inline-padding, 12px);
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  padding-right: var(--dsh-session-list-edge-inset);
}

.rail {
  padding-right: 0;
}

.sectionHeader {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 4px;
  height: 36px;
  padding-left: 4px;
  margin-bottom: 4px;
  box-sizing: border-box;
  border-radius: 12px;
  overflow: hidden;
}

.rail .sectionHeader {
  gap: 0;
  padding-left: 0;
  margin-bottom: 12px;
  justify-content: flex-start;
}

.sectionLabel {
  flex: none;
  max-width: 45%;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  font-size: 14px;
  line-height: 20px;
  color: var(--dsw-alias-label-tertiary);
}

.searchSlot {
  flex: 1;
  max-width: 28px;
  margin-left: auto;
  transition: max-width 180ms var(--ds-ease-in-out);
}

.searchSlotExpanded {
  max-width: 100%;
}

.search {
  flex: none;
  display: flex;
  align-items: center;
  width: 100%;
  height: 28px;
  border-radius: 50%;
  background: transparent;
  cursor: text;
  color: var(--dsw-alias-label-secondary);
  overflow: hidden;
}

.searchExpanded {
  width: calc(100% + 4px);
  height: 30px;
  margin-inline: -2px;
  padding: 0 4px 0 0;
  box-sizing: border-box;
  border: 1px solid var(--dsw-alias-border-l2);
  border-radius: 10px;
  color: var(--dsw-alias-label-caption);
}

.searchBtn {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  padding: 0;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: inherit;
  cursor: pointer;
}

.rail .searchBtn {
  width: 36px;
  height: 36px;
}

.searchInput {
  flex: 1;
  width: 0;
  min-width: 0;
  border: none;
  outline: none;
  background: transparent;
  opacity: 0;
  pointer-events: none;
  font-family: inherit;
  font-size: 13px;
  line-height: 18px;
  color: var(--dsw-alias-label-primary);
  transition: opacity 120ms var(--ds-ease-in-out);
}

.searchExpanded .searchInput {
  margin-left: -2px;
  opacity: 1;
  pointer-events: auto;
}

.searchInput::placeholder {
  color: var(--dsw-alias-label-tertiary);
}

.clearBtn {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  padding: 0;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: var(--dsw-alias-label-secondary);
  cursor: pointer;
}

.clearBtn:hover {
  background: var(--dsw-alias-interactive-bg-hover);
}

.headerActions {
  flex: none;
  display: flex;
  gap: 4px;
}

.iconBtn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  padding: 0;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--dsw-alias-label-secondary);
  cursor: pointer;
}

.iconBtn:hover {
  background: var(--dsw-alias-interactive-bg-hover);
  color: var(--dsw-alias-label-primary);
}

.list {
  position: relative;
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  /* 负边距把滚动条推到列边缘，行背景仍留 12px 内缩 */
  margin-left: -4px;
  margin-right: 2px;
  padding-left: 4px;
  padding-right: calc(var(--dsh-session-list-edge-inset) - 8px - 2px);
  padding-bottom: 16px;
  box-sizing: border-box;
  scrollbar-gutter: stable;
}

.groupSection {
  position: relative;
}

.groupSection + .groupSection {
  margin-top: 4px;
}

.list > * + * {
  margin-top: 2px;
}

.indented {
  padding-left: 30px;
}

.overflowBtn {
  width: 100%;
  height: 28px;
  border: none;
  border-radius: 8px;
  padding: 0 12px 0 28px;
  background: transparent;
  cursor: pointer;
  text-align: left;
  font-family: inherit;
  font-size: 12px;
  color: var(--dsw-alias-label-tertiary);
}

.overflowBtn:hover {
  color: var(--dsw-alias-label-secondary);
}

.empty {
  padding: 16px 12px;
  font-size: 13px;
  color: var(--dsw-alias-label-tertiary);
}

.fade {
  position: absolute;
  left: 0;
  right: var(--dsh-session-list-edge-inset);
  bottom: 0;
  height: 24px;
  background: linear-gradient(to bottom, transparent, var(--dsw-specific-sidebar-fill));
  pointer-events: none;
}

.dialog {
  display: flex;
  flex-direction: column;
  gap: 12px;
  width: 100%;
  padding: 20px;
  box-sizing: border-box;
}

.dialogTitle {
  font-size: 16px;
  font-weight: 500;
  line-height: 24px;
  color: var(--dsw-alias-label-primary);
}

.dialogDesc {
  font-size: 13px;
  line-height: 20px;
  color: var(--dsw-alias-label-secondary);
}

.renameInput {
  box-sizing: border-box;
  width: 100%;
  height: 44px;
  padding: 7px 14px;
  border: 1px solid var(--dsw-alias-border-l2);
  border-radius: 22px;
  outline: none;
  background: transparent;
  font-family: inherit;
  font-size: 14px;
  line-height: 22px;
  color: var(--dsw-alias-label-primary);
}

.renameInput:focus {
  border-color: var(--dsw-alias-state-business-primary);
}

.renameError {
  font-size: 12px;
  line-height: 18px;
  color: var(--dsw-alias-state-error-primary);
}

.dialogFoot {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 4px;
}
</style>
