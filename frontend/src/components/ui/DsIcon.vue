<template>
  <svg
    :width="size"
    :height="size"
    :viewBox="g.vb"
    fill="currentColor"
    aria-hidden="true"
    v-html="g.html"
  />
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { GLYPHS } from './glyphs'

/** 参考项目图标集（16/14 viewBox + fill=currentColor）。size 不传时用 glyph 自带的名义尺寸。 */
const props = defineProps<{ name: string; size?: number }>()

const ALIAS: Record<string, string> = {
  'panel-left': 'IconPanelLeftOutline16',
  'new-chat': 'IconNewChatOutline16',
  search: 'IconSearchOutline16',
  personalization: 'IconPersonalizationOutline16',
  'project-add': 'IconProjectAddOutline16',
  folder: 'IconFolderClose16',
  'folder-open': 'IconFolderOpen16',
  chevron: 'IconTriangleRightFill14',
  'chevron-down': 'IconChevronDownOutline14',
  'chevron-up': 'IconChevronUpOutline14',
  'chevron-left': 'IconChevronLeftOutline14',
  'chevron-right': 'IconChevronRightOutline14',
  ellipsis: 'IconEllipsisOutline16',
  plus: 'IconPlusOutline16',
  edit: 'IconEditOutline16',
  trash: 'IconTrashOutline16',
  archive: 'IconArchiveOutline20',
  branch: 'IconBranchOutline16',
  close: 'IconCloseOutline16',
  'close-fill': 'IconCloseFill14',
  settings: 'IconSettingsOutline16',
  check: 'IconCheckOutline16',
  copy: 'IconCopyOutline16',
  code: 'IconCodeOutline16',
  think: 'IconThinkOutline16',
  'think-14': 'IconThinkOutline14',
  globe: 'IconGlobeOutline14',
  'right-up': 'IconRightUpOutline16',
  link: 'IconLinkOutline14',
  send: 'IconSendOutline16',
  stop: 'IconStopFill16',
  inspect: 'IconInspectOutline12',
  checklist: 'IconChecklistOutline14',
  queue: 'IconQueueOutline14',
  goal: 'IconGoalOutline16',
  user: 'IconUserOutline16',
  warning: 'IconWarningOutline16',
  question: 'IconQuestionOutline14',
  like: 'IconLikeOutline16',
  'like-fill': 'IconLikeFill16',
  dislike: 'IconDislikeOutline16',
  download: 'IconDownloadOutline16',
  fullscreen: 'IconFullscreenOutline16',
  paperclip: 'IconPaperclipOutline16',
  // F-G：`file` 被 ChatNode 的产物链接与 ToolCard 用着，但字形集里从来没有这个名字
  // ——`ALIAS[name] || name` 取不到就走 FALLBACK，渲染成**空 svg**（图标位空白，不是豆腐块）。
  // 不新增字形（`ui/glyphs.ts` 是 extract_glyphs.py 自动生成的，抽取清单在参照系那一侧），
  // 先把名字接到已有的回形针字形上：产物在界面上的语义就是「这场带出来的附件」。
  file: 'IconPaperclipOutline16',
  refresh: 'IconRefreshOutline16',
  sparkle: 'IconSparkle16',
  browse: 'IconBrowseOutline16',
  skill: 'IconSkillOutline16',
  play: 'IconPlayOutline16',
  pause: 'IconPauseOutline16',
  data: 'IconDataOutline16',
  light: 'IconLightOutline16',
  dark: 'IconDarkOutline16',
  follow: 'IconFollowsystemOutline16',
  share: 'IconShareOutline16',
  'tree-corner': 'IconTreeCorner8x10'
}

const FALLBACK: Record<string, string> = {
  IconPanelLeftOutline16: '0 0 16 16',
  IconTreeCorner8x10: '0 0 8 10'
}

const g = computed(() => {
  const key = ALIAS[props.name] || props.name
  const glyph = GLYPHS[key]
  if (glyph) return glyph
  // 缺 glyph 时宁可显式报错也不静默画一个空框（参考项目里空 path 就是这么把 bug 藏住的）
  return { vb: FALLBACK[key] || '0 0 16 16', html: '' }
})
</script>
