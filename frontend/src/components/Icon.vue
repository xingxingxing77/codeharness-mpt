<template>
  <svg
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.8"
    stroke-linecap="round"
    stroke-linejoin="round"
    v-html="path"
  />
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ name: string; size?: number }>()

const P: Record<string, string> = {
  edit: `<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/>`,
  search: `<circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>`,
  grid: `<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>`,
  clock: `<circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15.5 14"/>`,
  phone: `<rect x="7" y="2" width="10" height="20" rx="2.5"/><line x1="11" y1="18.5" x2="13" y2="18.5"/>`,
  folder: `<path d="M3 7a2 2 0 0 1 2-2h4l2.3 2.3H19a2 2 0 0 1 2 2V17a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/>`,
  'folder-open': `<path d="M3 7a2 2 0 0 1 2-2h4l2 2h6a2 2 0 0 1 2 2v1H6.5L3 17Z"/><path d="M3 17V7"/><path d="M6.5 10H21l-2.3 7H3Z"/>`,
  chat: `<path d="M21 11.5a8.5 8.5 0 0 1-8.5 8.5c-1.6 0-3.1-.4-4.4-1.2L3 20l1.2-5.1A8.5 8.5 0 1 1 21 11.5Z"/>`,
  gear: `<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1Z"/>`,
  plus: `<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>`,
  mic: `<path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3Z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/>`,
  'arrow-up': `<line x1="12" y1="19" x2="12" y2="5"/><polyline points="5 12 12 5 19 12"/>`,
  monitor: `<rect x="2" y="4" width="20" height="13" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/>`,
  'panel-right': `<rect x="3" y="4" width="18" height="16" rx="2"/><line x1="15" y1="4" x2="15" y2="20"/>`,
  lock: `<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>`,
  shield: `<path d="M12 3l7 3v5c0 5-3.5 8-7 9-3.5-1-7-4-7-9V6Z"/><path d="M9 11.5l2 2 4-4"/>`,
  'chevron-down': `<polyline points="6 9 12 15 18 9"/>`,
  branch: `<line x1="6" y1="3" x2="6" y2="15"/><circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M18 9a9 9 0 0 1-9 9"/>`,
  terminal: `<polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/>`,
  globe: `<circle cx="12" cy="12" r="9"/><line x1="3" y1="12" x2="21" y2="12"/><path d="M12 3a14.5 14.5 0 0 1 0 18 14.5 14.5 0 0 1 0-18Z"/>`,
  eye: `<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8Z"/><circle cx="12" cy="12" r="3"/>`,
  file: `<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"/><polyline points="14 2 14 8 20 8"/>`,
  copy: `<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>`,
  pencil: `<path d="M17 3a2.8 2.8 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/>`,
  dots: `<circle cx="5" cy="12" r="1.4" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="1.4" fill="currentColor" stroke="none"/><circle cx="19" cy="12" r="1.4" fill="currentColor" stroke="none"/>`,
  x: `<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>`,
  external: `<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/>`,
  sparkle: `<path d="M12 2l2.2 7.8L22 12l-7.8 2.2L12 22l-2.2-7.8L2 12l7.8-2.2Z" fill="currentColor" stroke="none"/>`,
  check: `<polyline points="20 6 9 17 4 12"/>`,
  user: `<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 3.6-6 8-6s8 2 8 6"/>`,
  sliders: `<line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/><line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/><line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/><line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/><line x1="17" y1="16" x2="23" y2="16"/>`,
  refresh: `<polyline points="23 4 23 10 17 10"/><path d="M20.5 15a9 9 0 1 1-2-9.5L23 10"/>`,
  logout: `<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/>`,
  key: `<path d="M21 2l-2 2m-7.6 7.6a5.5 5.5 0 1 1-7.8 7.8 5.5 5.5 0 0 1 7.8-7.8Zm0 0L15.5 7.5m0 0 3 3L22 7l-3-3"/>`,
  coins: `<circle cx="12" cy="12" r="9"/><path d="M8.5 8.5h7M8.5 12h7M8.5 15.5h7"/>`,
  sun: `<circle cx="12" cy="12" r="4"/><line x1="12" y1="2" x2="12" y2="4"/><line x1="12" y1="20" x2="12" y2="22"/><line x1="4.93" y1="4.93" x2="6.34" y2="6.34"/><line x1="17.66" y1="17.66" x2="19.07" y2="19.07"/><line x1="2" y1="12" x2="4" y2="12"/><line x1="20" y1="12" x2="22" y2="12"/><line x1="4.93" y1="19.07" x2="6.34" y2="17.66"/><line x1="17.66" y1="6.34" x2="19.07" y2="4.93"/>`,
  keyboard: `<rect x="2" y="6" width="20" height="12" rx="2"/><line x1="6" y1="10" x2="6.01" y2="10"/><line x1="10" y1="10" x2="10.01" y2="10"/><line x1="14" y1="10" x2="14.01" y2="10"/><line x1="18" y1="10" x2="18.01" y2="10"/><line x1="7" y1="14" x2="17" y2="14"/>`,
  plug: `<path d="M9 2v6M15 2v6"/><path d="M6 8h12v4a6 6 0 0 1-12 0Z"/><line x1="12" y1="18" x2="12" y2="22"/>`,
  anchor: `<circle cx="12" cy="5" r="2.5"/><line x1="12" y1="7.5" x2="12" y2="21"/><path d="M5 12H3a9 9 0 0 0 18 0h-2"/>`,
  palette: `<circle cx="12" cy="12" r="9"/><circle cx="8.5" cy="9.5" r="1" fill="currentColor" stroke="none"/><circle cx="15.5" cy="9.5" r="1" fill="currentColor" stroke="none"/><circle cx="9" cy="15" r="1" fill="currentColor" stroke="none"/><path d="M15.5 15a3.5 3.5 0 0 1-3.5 3"/>`,
  window: `<rect x="3" y="4" width="18" height="16" rx="2"/><line x1="3" y1="9" x2="21" y2="9"/>`,
  cursor: `<path d="M4 4l7 17 2.5-7.5L21 11Z"/>`,
  archive: `<rect x="3" y="4" width="18" height="5" rx="1"/><path d="M5 9v9a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V9"/><line x1="10" y1="13" x2="14" y2="13"/>`,
  'arrow-left': `<line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/>`,
  laptop: `<rect x="4" y="5" width="16" height="11" rx="1.5"/><path d="M2 19h20"/>`,
  download: `<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="3" x2="12" y2="15"/>`,
  moon: `<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z"/>`
}

const path = computed(() => P[props.name] || '')
</script>
