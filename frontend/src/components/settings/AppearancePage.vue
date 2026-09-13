<template>
  <div class="spage">
    <h1 class="sptitle">外观</h1>

    <div class="ssec">主题</div>
    <div class="ssec-sub">选择应用的配色方案</div>
    <RadioCards v-model="theme" :options="themes" />

    <div class="ssec">显示</div>
    <div class="sgroup">
      <Row title="界面语言" desc="与常规设置中的语言项同步生效">
        <SelectBox v-model="p.language" :options="['自动检测', '简体中文', 'English']" />
      </Row>
      <Row title="紧凑模式" desc="减少对话与面板的留白间距">
        <Toggle v-model="compact" />
      </Row>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useSettingsStore } from '../../stores/settings'
import { useUiStore } from '../../stores/ui'
import RadioCards from './RadioCards.vue'
import Row from './Row.vue'
import SelectBox from './SelectBox.vue'
import Toggle from './Toggle.vue'

const p = useSettingsStore().prefs
const ui = useUiStore()

const theme = computed({
  get: () => ui.theme,
  set: (v: string) => (ui.theme = v as 'dark' | 'light')
})

const compact = ref(false)

const themes = [
  { value: 'light', icon: 'sun', title: '亮色', desc: '适合明亮环境的浅色主题' },
  { value: 'dark', icon: 'moon', title: '暗色', desc: '低亮度环境下的深色主题' }
]
</script>
